"""CLI: ``python -m resibo extract file.pdf -o out.xlsx [--csv|--json] [--record PATH]``.

Runs the same ``extract_batch`` as the API with the unlimited policy. ``--record`` takes an
explicit path (there is no default) and writes the raw model output per chunk (AD-4, AD-5).
"""

import argparse
import secrets
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from resibo import exporters
from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import AnthropicLLM, InvoiceLLM, RecordingLLM
from resibo.models import ResiboError, build_batch
from resibo.validation import manila_today

LLMFactory = Callable[[Settings], InvoiceLLM]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resibo", description="Resibo invoice extractor")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="extract invoices from a PDF")
    extract.add_argument("pdf", type=Path, help="the PDF to read")
    extract.add_argument("-o", "--output", type=Path, help="output file (default: next to the PDF)")
    fmt = extract.add_mutually_exclusive_group()
    fmt.add_argument("--csv", action="store_true", help="write invoices CSV and line items CSV")
    fmt.add_argument("--json", action="store_true", help="write the Batch as JSON")
    extract.add_argument(
        "--record",
        type=Path,
        metavar="PATH",
        help="save the raw model output per chunk to PATH (for the cached demo)",
    )
    extract.add_argument("--model", help="Claude model id (default: CLAUDE_MODEL)")
    return parser


def run_extract(
    args: argparse.Namespace,
    llm_factory: LLMFactory | None,
    now: datetime,
    out=None,
) -> int:
    out = out or sys.stdout
    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"claude_model": args.model})
    if llm_factory is None and not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set. Export it and try again.")
    try:
        data = args.pdf.read_bytes()
    except OSError as exc:
        raise SystemExit(f"Cannot read {args.pdf}: {exc.strerror or exc}") from exc

    llm: InvoiceLLM = llm_factory(settings) if llm_factory else AnthropicLLM(settings)
    recorder: RecordingLLM | None = None
    if args.record:
        recorder = RecordingLLM(llm, args.record)
        llm = recorder

    extraction = extract_batch(
        data,
        args.pdf.name,
        llm,
        settings,
        CallPolicy(max_pages=10_000),
        manila_today(now),
    )
    batch = build_batch(
        extraction,
        secrets.token_urlsafe(8),
        args.pdf.name,
        now + timedelta(minutes=settings.result_ttl_minutes),
    )

    suffix = ".json" if args.json else ".csv" if args.csv else ".xlsx"
    target = args.output or args.pdf.with_suffix(suffix)
    written = [target]
    if args.json:
        target.write_text(exporters.to_json(batch) + "\n", encoding="utf-8")
    elif args.csv:
        target.write_bytes(exporters.to_invoices_csv(batch))
        items = target.with_name(f"{target.stem}.line_items{target.suffix}")
        items.write_bytes(exporters.to_line_items_csv(batch))
        written.append(items)
    else:
        target.write_bytes(exporters.to_xlsx(batch, processed_at=now))

    if recorder:
        written.append(recorder.save(extraction, args.pdf.name, settings.pages_per_chunk))

    s = batch.summary
    print(
        f"Read {batch.page_count} pages: {s.documents} documents "
        f"({s.ok} ok, {s.review} review, {s.error} error) in {batch.duration_ms / 1000:.1f} s.",
        file=out,
    )
    for path in written:
        print(f"Wrote {path}", file=out)
    return 0


def main(
    argv: Sequence[str] | None = None,
    llm_factory: LLMFactory | None = None,
    now: datetime | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_extract(args, llm_factory, now or datetime.now(UTC))
    except ResiboError as exc:
        print(f"{exc.code.value}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
