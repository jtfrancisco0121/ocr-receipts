"""Cached sample: load a recording (format v1, AD-4) and replay it through the normal pipeline.

``ReplayLLM`` implements the ``InvoiceLLM`` port, so the replayed documents go through the same
normalize, stitch and validate path as a live run and the flags always reflect the current rules.
This module only reads recordings; the only writer is ``llm.RecordingLLM`` (AD-5).
"""

import json
import secrets
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import RECORDING_FORMAT_VERSION, LLMResult
from resibo.models import Batch, ErrorCode, Extraction, ResiboError, build_batch
from resibo.store import BatchStore
from resibo.validation import manila_today

NOT_RECORDED_MESSAGE = (
    "Sample result not recorded yet. POST the sample PDF to /api/extract to process it live."
)


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


class SampleService:
    """One shared cached-sample batch per process, rebuilt under a lock when it has expired or
    when its id is no longer in the store (LRU eviction), so repeat calls do not grow the store."""

    def __init__(
        self,
        samples_dir: Path,
        settings: Settings,
        store: BatchStore,
        clock: Callable[[], datetime],
    ):
        self.samples_dir = Path(samples_dir)
        self._settings = settings
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._batch_id: str | None = None

    @property
    def pdf_path(self) -> Path:
        return self.samples_dir / "sample-invoices.pdf"

    @property
    def recording_path(self) -> Path:
        return self.samples_dir / "sample-result.json"

    def get_batch(self) -> Batch:
        with self._lock:
            if self._batch_id is not None and self._store.contains(self._batch_id):
                return self._store.get(self._batch_id)
            recording = load_recording(self.recording_path)  # SAMPLE_NOT_RECORDED when absent
            try:
                pdf_bytes = self.pdf_path.read_bytes()
            except OSError as exc:
                raise ResiboError(
                    ErrorCode.INTERNAL, "The sample PDF is missing on this server."
                ) from exc
            now = self._clock()
            extraction = replay_extraction(recording, pdf_bytes, self._settings, manila_today(now))
            batch = build_batch(
                extraction,
                secrets.token_urlsafe(8),
                recording.file_name,
                now + timedelta(minutes=self._settings.result_ttl_minutes),
                cached=True,
                recorded_at=datetime.fromisoformat(recording.recorded_at),
            )
            self._store.put(batch)
            self._batch_id = batch.batch_id
            return batch
