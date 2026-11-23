"""The extraction pipeline: check, chunk, call, parse/repair, normalize, stitch, validate.

``extract_batch`` is the one entry point every shell uses (AD-1). Every Claude call (chunk, half
or repair) goes through ``_Run._call``, which reserves and settles the budget (AD-8).
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from resibo import pdf_utils
from resibo.config import Settings
from resibo.llm import InvoiceLLM, LLMResult
from resibo.models import (
    ErrorCode,
    Extraction,
    Flag,
    Invoice,
    RawOutput,
    ResiboError,
    Severity,
    normalize_invoice,
)
from resibo.validation import SPLIT_ACROSS_CHUNKS, validate_invoices

log = logging.getLogger("resibo.extractor")

TRUNCATED_STOPS = {"max_tokens", "model_context_window_exceeded"}


@dataclass
class CallPolicy:
    """Limits and hooks for one extraction. ``None`` means unlimited / no hook."""

    max_halving_depth: int | None = None
    max_repairs: int | None = None
    reserve: Callable[[int], Any] | None = None
    settle: Callable[[Any, int | None, int | None], None] | None = None
    max_pages: int | None = None  # defaults to Settings.max_pages
    public: bool = False  # only changes the wording of the page-limit message
    on_checked: Callable[[int], None] | None = None  # after the PDF checks, before any call


class _Run:
    def __init__(
        self, data: bytes, page_count: int, file_name: str, llm: InvoiceLLM, policy: CallPolicy
    ):
        self.data = data
        self.page_count = page_count
        self.file_name = file_name
        self.llm = llm
        self.policy = policy
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0
        self.repairs = 0
        self.halvings = 0

    def _call(self, first: int, last: int, repair_errors: str | None) -> LLMResult:
        policy = self.policy
        pages = last - first + 1
        token = policy.reserve(pages) if policy.reserve else None
        try:
            if first == 1 and last == self.page_count:
                chunk = self.data
            else:
                chunk = pdf_utils.extract_pages(self.data, first, last)
            result = self.llm.extract(
                chunk,
                first,
                last,
                repair_errors,
                total_pages=self.page_count,
                file_name=self.file_name,
            )
        except BaseException:
            if policy.settle:
                policy.settle(token, None, None)
            raise
        self.llm_calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        if policy.settle:
            policy.settle(token, result.input_tokens, result.output_tokens)
        return result

    def _check_stop(self, result: LLMResult) -> bool:
        """True when the range was truncated and must be halved."""
        reason = result.stop_reason
        if reason in TRUNCATED_STOPS:
            log.warning("stop_reason=%s", reason)
            return True
        if reason == "refusal":
            log.warning("stop_reason=refusal")
            raise ResiboError(
                ErrorCode.UPSTREAM_REFUSED,
                "The AI service declined to read this document.",
            )
        if reason != "end_turn":
            raise ResiboError(
                ErrorCode.UPSTREAM_ERROR,
                f"The AI service stopped unexpectedly ({reason or 'no reason given'}).",
            )
        return False

    @staticmethod
    def _parse(raw: dict, first: int, last: int) -> list[Invoice]:
        """Parse and structurally check one chunk. Raises ValueError with the error text."""
        try:
            parsed = RawOutput.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            )
            raise ValueError(problems) from exc
        problems = []
        for index, inv in enumerate(parsed.invoices):
            outside = [p for p in inv.source_pages if p < first or p > last]
            if outside:
                problems.append(
                    f"invoices.{index}.source_pages: pages {outside} are outside {first}-{last}"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return [normalize_invoice(raw_inv) for raw_inv in parsed.invoices]

    def read_range(self, first: int, last: int) -> list[Invoice] | None:
        """Call, parse and repair once. ``None`` means the range was truncated: halve it."""
        repair_errors: str | None = None
        while True:
            result = self._call(first, last, repair_errors)
            if self._check_stop(result):
                return None
            try:
                return self._parse(result.raw_output, first, last)
            except ValueError as exc:
                errors = str(exc)
                limit = self.policy.max_repairs
                if repair_errors is None and (limit is None or self.repairs < limit):
                    self.repairs += 1
                    repair_errors = errors
                    continue
                log.warning("extraction invalid: %s", errors)
                raise ResiboError(
                    ErrorCode.EXTRACTION_INVALID,
                    f"The AI service returned data that failed validation: {errors}",
                ) from exc


def _first_page(inv: Invoice) -> int:
    return min(inv.source_pages)


def _last_page(inv: Invoice) -> int:
    return max(inv.source_pages)


def _split_flag() -> Flag:
    return Flag(
        code=SPLIT_ACROSS_CHUNKS,
        severity=Severity.WARNING,
        field=None,
        message="This document may continue across two reads and could be incomplete.",
    )


def extract_batch(
    pdf_bytes: bytes,
    file_name: str,
    llm: InvoiceLLM,
    settings: Settings,
    policy: CallPolicy | None = None,
    today: date | None = None,
) -> Extraction:
    """Run the whole pipeline on one PDF. ``today`` (Asia/Manila date) is required for the date
    rules; the caller owns the clock."""
    if today is None:
        raise ValueError("extract_batch needs today (the caller owns the clock)")
    policy = policy or CallPolicy()
    started = time.perf_counter()

    limit = policy.max_pages if policy.max_pages is not None else settings.max_pages
    prepared = pdf_utils.prepare_pdf(pdf_bytes, limit, public=policy.public)
    page_count = prepared.page_count
    if policy.on_checked:
        policy.on_checked(page_count)

    run = _Run(prepared.data, page_count, file_name, llm, policy)
    # queue items: (first_page, last_page, halving_depth)
    queue: deque[tuple[int, int, int]] = deque(
        (a, b, 0) for a, b in pdf_utils.chunk_ranges(page_count, settings.pages_per_chunk)
    )
    kept: list[Invoice] = []
    chunks = 0

    while queue:
        first, last, depth = queue.popleft()
        chunks += 1
        invoices = run.read_range(first, last)
        if invoices is None:  # truncated: halve and requeue both halves in order
            max_depth = policy.max_halving_depth
            if first == last or (max_depth is not None and depth >= max_depth):
                raise ResiboError(
                    ErrorCode.EXTRACTION_TRUNCATED,
                    "The AI response was cut off. Try a smaller file or fewer pages.",
                )
            mid = (first + last) // 2
            run.halvings += 1
            queue.appendleft((mid + 1, last, depth + 1))
            queue.appendleft((first, mid, depth + 1))
            continue

        restart = _restart_page(invoices, last)
        if restart is not None and queue:
            if restart > first:
                # discard everything that starts at or after the partial invoice; read it again
                invoices = [inv for inv in invoices if _first_page(inv) < restart]
                _, next_last, next_depth = queue.popleft()
                queue.appendleft((restart, next_last, next_depth))
            else:
                # restarting here would loop: accept the partial invoice and flag it
                for inv in invoices:
                    if not inv.is_complete and _last_page(inv) == last:
                        inv.flags.append(_split_flag())
        kept.extend(invoices)

    kept.sort(key=_first_page)  # stable: model order is kept within a page
    for number, inv in enumerate(kept, start=1):
        inv.id = f"INV-{number:03d}"
    validated = validate_invoices(kept, settings, today)

    return Extraction(
        invoices=validated,
        page_count=page_count,
        model=settings.claude_model,
        chunks=chunks,
        duration_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        repair_retries=run.repairs,
        halvings=run.halvings,
        llm_calls=run.llm_calls,
    )


def _restart_page(invoices: list[Invoice], chunk_last: int) -> int | None:
    """Section 8.2 stitching: the page where the next read must start, if the chunk's last
    invoice (highest last page, ties highest first page) is incomplete and reaches its end."""
    if not invoices:
        return None
    last_invoice = max(invoices, key=lambda inv: (_last_page(inv), _first_page(inv)))
    if not last_invoice.is_complete and _last_page(last_invoice) == chunk_last:
        return _first_page(last_invoice)
    return None
