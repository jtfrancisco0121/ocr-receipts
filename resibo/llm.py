"""The InvoiceLLM port and its adapters. The only module that imports ``anthropic`` (AD-1).

``AnthropicLLM`` makes the one fixed request of AD-3. ``RecordingLLM`` wraps any adapter and
writes recording format v1 (AD-4) to an explicit path. No per-request state is stored on any
adapter: one ``AnthropicLLM`` is shared by concurrent threadpool requests.
"""

import base64
import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import anthropic

from resibo import config, prompts
from resibo.config import Settings
from resibo.models import ErrorCode, Extraction, ResiboError
from resibo.schema import RECORD_INVOICES_SCHEMA

log = logging.getLogger("resibo.llm")

RECORDING_FORMAT_VERSION = 1


class LLMResult(NamedTuple):
    raw_output: dict
    input_tokens: int
    output_tokens: int
    stop_reason: str


class InvoiceLLM(Protocol):
    def extract(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None = None,
        *,
        total_pages: int,
        file_name: str,
    ) -> LLMResult: ...


class AnthropicLLM:
    """Claude through ``messages.stream`` with structured outputs. The client is built lazily."""

    def __init__(self, settings: Settings, client: Any | None = None):
        self._settings = settings
        self._client = client
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                if not self._settings.anthropic_api_key:
                    raise ResiboError(
                        ErrorCode.UPSTREAM_ERROR, "ANTHROPIC_API_KEY is not set on the server."
                    )
                self._client = anthropic.Anthropic(
                    api_key=self._settings.anthropic_api_key, max_retries=1, timeout=300.0
                )
            return self._client

    def build_request(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None,
        total_pages: int,
        file_name: str,
    ) -> dict[str, Any]:
        s = self._settings
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": RECORD_INVOICES_SCHEMA}
        }
        if config.supports_effort(s.claude_model):
            output_config["effort"] = s.claude_effort
        return {
            "model": s.claude_model,
            "max_tokens": s.max_output_tokens,
            "system": prompts.SYSTEM_PROMPT,
            "output_config": output_config,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": prompts.instruction(
                                first_page, last_page, total_pages, file_name, repair_errors
                            ),
                        },
                    ],
                }
            ],
        }

    def extract(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None = None,
        *,
        total_pages: int,
        file_name: str,
    ) -> LLMResult:
        request = self.build_request(
            pdf_bytes, first_page, last_page, repair_errors, total_pages, file_name
        )
        client = self._get_client()
        try:
            with client.messages.stream(**request) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as exc:
            request_id = getattr(exc, "request_id", None)
            log.error("anthropic error type=%s request_id=%s", type(exc).__name__, request_id)
            raise ResiboError(
                ErrorCode.UPSTREAM_ERROR,
                "The AI service could not process this request. Please try again in a moment.",
            ) from exc
        stop_reason = getattr(message, "stop_reason", None) or ""
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            log.warning("refusal category=%s", getattr(details, "category", None))
        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        try:
            parsed = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        usage = message.usage
        return LLMResult(
            raw_output=parsed if isinstance(parsed, dict) else {},
            input_tokens=int(usage.input_tokens),
            output_tokens=int(usage.output_tokens),
            stop_reason=stop_reason,
        )


class RecordingLLM:
    """Wraps an adapter and remembers every call; ``save`` writes recording format v1."""

    def __init__(
        self,
        inner: InvoiceLLM,
        path: str | os.PathLike[str],
        clock: Callable[[], datetime] | None = None,
    ):
        if not path:
            raise ValueError("RecordingLLM needs an explicit path")
        self._inner = inner
        self._path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def extract(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None = None,
        *,
        total_pages: int,
        file_name: str,
    ) -> LLMResult:
        result = self._inner.extract(
            pdf_bytes,
            first_page,
            last_page,
            repair_errors,
            total_pages=total_pages,
            file_name=file_name,
        )
        with self._lock:
            self._calls.append(
                {
                    "first_page": first_page,
                    "last_page": last_page,
                    "repair": repair_errors is not None,
                    "raw_output": result.raw_output,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "stop_reason": result.stop_reason,
                }
            )
        return result

    def save(self, extraction: Extraction, file_name: str, pages_per_chunk: int) -> Path:
        cost = config.estimate_cost(
            extraction.model, extraction.input_tokens, extraction.output_tokens
        )
        document = {
            "format_version": RECORDING_FORMAT_VERSION,
            "file_name": file_name,
            "page_count": extraction.page_count,
            "pages_per_chunk": pages_per_chunk,
            "model": extraction.model,
            "recorded_at": self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_ms": extraction.duration_ms,
            "usage": {
                "input_tokens": extraction.input_tokens,
                "output_tokens": extraction.output_tokens,
                "estimated_cost_usd": float(cost) if cost is not None else None,
            },
            "calls": list(self._calls),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self._path)
        return self._path
