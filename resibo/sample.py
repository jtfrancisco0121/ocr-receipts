"""Cached sample: load a recording (format v1, AD-4) and replay it through the normal pipeline.

``ReplayLLM`` implements the ``InvoiceLLM`` port, so the replayed documents go through the same
normalize, stitch and validate path as a live run and the flags always reflect the current rules.
This module only reads recordings; the only writer is ``llm.RecordingLLM`` (AD-5).
"""

import json
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import RECORDING_FORMAT_VERSION, LLMResult
from resibo.models import ErrorCode, Extraction, ResiboError

NOT_RECORDED_MESSAGE = "Sample result not recorded yet. Run live to process the sample instead."


@dataclass(frozen=True)
class Recording:
    file_name: str
    page_count: int
    pages_per_chunk: int
    model: str
    recorded_at: str
    duration_ms: int
    input_tokens: int
    output_tokens: int
    calls: tuple[dict, ...]


def load_recording(path: Path) -> Recording:
    """Read a recording. A missing file is ``SAMPLE_NOT_RECORDED``; a malformed one is INTERNAL."""
    path = Path(path)
    if not path.is_file():
        raise ResiboError(ErrorCode.SAMPLE_NOT_RECORDED, NOT_RECORDED_MESSAGE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["format_version"] != RECORDING_FORMAT_VERSION:
            raise ValueError(f"unsupported recording format {data['format_version']!r}")
        return Recording(
            file_name=data["file_name"],
            page_count=int(data["page_count"]),
            pages_per_chunk=int(data["pages_per_chunk"]),
            model=data["model"],
            recorded_at=data["recorded_at"],
            duration_ms=int(data["duration_ms"]),
            input_tokens=int(data["usage"]["input_tokens"]),
            output_tokens=int(data["usage"]["output_tokens"]),
            calls=tuple(data["calls"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ResiboError(ErrorCode.INTERNAL, "The recorded sample result is unreadable.") from exc


class ReplayLLM:
    """Serves recorded calls by ``(first_page, last_page, repair)``, in recorded order."""

    def __init__(self, recording: Recording):
        self._queues: dict[tuple[int, int, bool], deque[dict]] = defaultdict(deque)
        for call in recording.calls:
            key = (call["first_page"], call["last_page"], bool(call["repair"]))
            self._queues[key].append(call)
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
        key = (first_page, last_page, repair_errors is not None)
        with self._lock:
            queue = self._queues.get(key)
            if not queue:
                raise ResiboError(
                    ErrorCode.INTERNAL,
                    f"The recording has no call for pages {first_page}-{last_page}"
                    f"{' (repair)' if key[2] else ''}.",
                )
            call = queue.popleft()
        return LLMResult(
            raw_output=call["raw_output"],
            input_tokens=int(call["input_tokens"]),
            output_tokens=int(call["output_tokens"]),
            stop_reason=call["stop_reason"],
        )


def replay_extraction(
    recording: Recording, pdf_bytes: bytes, settings: Settings, today: date
) -> Extraction:
    """Run the recording through ``extract_batch`` with the recording's chunk size, the unlimited
    policy and no budget hooks; model, usage and duration come from the recorded run."""
    replay_settings = settings.model_copy(
        update={"pages_per_chunk": recording.pages_per_chunk, "claude_model": recording.model}
    )
    policy = CallPolicy(max_pages=max(recording.page_count, 1))
    extraction = extract_batch(
        pdf_bytes,
        recording.file_name,
        ReplayLLM(recording),
        replay_settings,
        policy,
        today,
    )
    extraction.model = recording.model
    extraction.input_tokens = recording.input_tokens
    extraction.output_tokens = recording.output_tokens
    extraction.duration_ms = recording.duration_ms
    return extraction
