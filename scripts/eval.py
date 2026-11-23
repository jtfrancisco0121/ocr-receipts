"""Accuracy eval: run the real pipeline on the sample PDF and score it against expected.json.

    export ANTHROPIC_API_KEY=...
    python scripts/eval.py [--model MODEL] [--record samples/sample-result.json]

Needs a real API key and makes real calls (cost shown at the end). ``--record`` takes an
explicit path and writes the same recording format as the CLI (the cached sample). This script
never edits ``samples/expected.json``; it only reads it.

The scoring function is separate from the I/O so it can run offline.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from resibo.config import Settings, estimate_cost
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import AnthropicLLM, InvoiceLLM, RecordingLLM
from resibo.models import Extraction, Invoice, ResiboError

ROOT = Path(__file__).resolve().parent.parent
MONEY_TOLERANCE = Decimal("0.01")


# --- scoring (pure) -------------------------------------------------------------------------


@dataclass
class Check:
    document: str
    name: str
    ok: bool
    expected: object
    actual: object


@dataclass
class Score:
    checks: list[Check] = field(default_factory=list)
    docs_expected: int = 0
    docs_found: int = 0
    docs_extra: int = 0
    line_items_correct: int = 0
    line_items_total: int = 0
    missing_required: list[str] = field(default_factory=list)
    unexpected_flags: list[str] = field(default_factory=list)
    optional_flags: list[str] = field(default_factory=list)  # reported, never scored

    @property
    def correct(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def percent(self) -> float:
        return 100.0 * self.correct / self.total if self.total else 0.0


def _norm_text(value: object) -> str:
    return " ".join(str(value).split()).lower()


def values_match(expected: object, actual: object) -> bool:
    """Amounts within 0.01; strings case- and space-insensitive; everything else equal."""
    if actual is None:
        return False
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, int | float) and isinstance(actual, int | float | Decimal):
        return abs(Decimal(str(expected)) - Decimal(str(actual))) <= MONEY_TOLERANCE
    return _norm_text(expected) == _norm_text(actual)


def _field_value(invoice: Invoice, name: str) -> object:
    value = getattr(invoice, name, None)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def match_documents(expected_docs: list[dict], invoices: list[Invoice]) -> list[Invoice | None]:
    """Pair each expected document, in order, with the first unused invoice that starts on the
    same page (so two receipts on one page pair up in reading order)."""
    used: set[int] = set()
    paired: list[Invoice | None] = []
    for doc in expected_docs:
        first_page = min(doc["source_pages"])
        found = None
        for index, invoice in enumerate(invoices):
            if (
                index not in used
                and invoice.source_pages
                and min(invoice.source_pages) == first_page
            ):
                used.add(index)
                found = invoice
                break
        paired.append(found)
    return paired


def score_extraction(expected: dict, invoices: list[Invoice]) -> Score:
    """Score extracted ``invoices`` against the expected.json ground truth (format v1)."""
    docs = expected["documents"]
    score = Score(docs_expected=len(docs))
    paired = match_documents(docs, invoices)
    score.docs_found = sum(1 for p in paired if p is not None)
    score.docs_extra = len(invoices) - score.docs_found

    for doc, invoice in zip(docs, paired, strict=True):
        label = doc["label"]
        for name, want in doc["fields"].items():
            got = _field_value(invoice, name) if invoice else None
            score.checks.append(Check(label, name, values_match(want, got), want, got))

        want_items = len(doc["line_items"])
        got_items = len(invoice.line_items) if invoice else 0
        score.line_items_total += 1
        score.line_items_correct += int(want_items == got_items)

        flags = {f.code for f in invoice.flags} if invoice else set()
        required = set(doc["required_flags"])
        optional = set(doc["optional_flags"])
        for code in doc["required_flags"]:
            ok = code in flags
            score.checks.append(
                Check(label, f"flag {code}", ok, "raised", "raised" if ok else "missing")
            )
            if not ok:
                score.missing_required.append(f"{label}: {code}")
        for code in sorted(flags & optional):
            score.optional_flags.append(f"{label}: {code}")
        for code in sorted(flags - required - optional):
            score.unexpected_flags.append(f"{label}: {code}")

        # status is scored, except an optional status is only judged when its optional flag fired
        judge_status = not doc["status_optional"] or bool(flags & optional)
        if judge_status:
            got_status = invoice.status.value if invoice else None
            score.checks.append(
                Check(
                    label,
                    "status",
                    got_status == doc["expected_status"],
                    doc["expected_status"],
                    got_status,
                )
            )
    return score


def format_report(score: Score) -> str:
    lines = [
        f"Documents found: {score.docs_found}/{score.docs_expected}"
        + (f" (+{score.docs_extra} extra)" if score.docs_extra else ""),
        f"Line-item counts correct: {score.line_items_correct}/{score.line_items_total}",
        "",
        f"{'Doc':<5} {'Check':<22} {'OK':<4} Expected -> Actual",
    ]
    for check in score.checks:
        mark = "yes" if check.ok else "NO"
        lines.append(
            f"{check.document:<5} {check.name:<22} {mark:<4} {check.expected} -> {check.actual}"
        )
    lines.append("")
    if score.missing_required:
        lines.append("Missing required flags: " + ", ".join(score.missing_required))
    if score.unexpected_flags:
        lines.append("Unexpected flags: " + ", ".join(score.unexpected_flags))
    if score.optional_flags:
        lines.append("Optional flags raised (not scored): " + ", ".join(score.optional_flags))
    lines.append(f"Fields correct: {score.correct}/{score.total} ({score.percent:.1f}%)")
    return "\n".join(lines)


# --- I/O ------------------------------------------------------------------------------------


def run_eval(
    samples_dir: Path,
    settings: Settings,
    llm: InvoiceLLM,
    record: Path | None,
    out=None,
) -> int:
    out = out or sys.stdout
    expected = json.loads((samples_dir / "expected.json").read_text(encoding="utf-8"))
    pdf_bytes = (samples_dir / "sample-invoices.pdf").read_bytes()
    recorder = RecordingLLM(llm, record) if record else None
    started = time.perf_counter()
    try:
        extraction: Extraction = extract_batch(
            pdf_bytes,
            "sample-invoices.pdf",
            recorder or llm,
            settings,
            CallPolicy(max_pages=10_000),
            date.fromisoformat(expected["anchor_date"]),  # reproducible date rules
        )
    except ResiboError as exc:
        print(f"{exc.code.value}: {exc.message}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started

    print(format_report(score_extraction(expected, extraction.invoices)), file=out)
    cost = estimate_cost(extraction.model, extraction.input_tokens, extraction.output_tokens)
    print(
        f"Model: {extraction.model}  Time: {elapsed:.1f} s  "
        f"Tokens: {extraction.input_tokens + extraction.output_tokens:,}  "
        f"Est. cost: {'n/a' if cost is None else f'${cost:.4f}'}",
        file=out,
    )
    if recorder:
        saved = recorder.save(extraction, "sample-invoices.pdf", settings.pages_per_chunk)
        print(f"Recorded to {saved}", file=out)
    return 0


def main(
    argv: list[str] | None = None,
    llm_factory: Callable[[Settings], InvoiceLLM] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Score Resibo on the sample invoices")
    parser.add_argument("--model", help="Claude model id (default: CLAUDE_MODEL)")
    parser.add_argument(
        "--record", type=Path, metavar="PATH", help="save the raw model output to PATH"
    )
    parser.add_argument("--samples", type=Path, default=ROOT / "samples", help="samples folder")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"claude_model": args.model})
    if llm_factory is None and not (
        settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    ):
        print(
            "ANTHROPIC_API_KEY is not set. The eval runs the real pipeline and needs a key "
            "(no call was made).",
            file=sys.stderr,
        )
        return 2
    llm = llm_factory(settings) if llm_factory else AnthropicLLM(settings)
    return run_eval(args.samples, settings, llm, args.record)


if __name__ == "__main__":
    sys.exit(main())
