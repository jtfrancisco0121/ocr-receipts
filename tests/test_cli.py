import csv
import json
from datetime import UTC, date, datetime

import pytest
from openpyxl import load_workbook
from pypdf import PdfWriter

from resibo.__main__ import build_parser, main
from resibo.config import Settings
from resibo.extractor import extract_batch
from resibo.llm import RecordingLLM
from resibo.models import ErrorCode, ResiboError
from resibo.sample import ReplayLLM, load_recording, replay_extraction
from tests.helpers import FakeLLM, raw_invoice, result

NOW = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)


def make_pdf(path, pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def fake_factory():
    llm = FakeLLM(
        {
            (1, 2): result(
                raw_invoice(source_pages=[1], invoice_number="A"),
                raw_invoice(
                    source_pages=[2],
                    invoice_number="B",
                    seller_tin="999-999-999-00000",
                    total_amount="99.00",
                ),
            )
        }
    )
    return lambda settings: llm


def test_extract_writes_xlsx_by_default(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.xlsx"
    assert main(["extract", str(pdf), "-o", str(out)], fake_factory(), NOW) == 0
    wb = load_workbook(out)
    assert wb.sheetnames == ["Invoices", "Line Items", "Summary"]
    assert wb["Invoices"].max_row == 3
    assert "2 documents" in capsys.readouterr().out


def test_extract_writes_csvs(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.csv"
    assert main(["extract", str(pdf), "-o", str(out), "--csv"], fake_factory(), NOW) == 0
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert len(rows) == 3
    assert (tmp_path / "out.line_items.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_extract_writes_json(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.json"
    assert main(["extract", str(pdf), "-o", str(out), "--json"], fake_factory(), NOW) == 0
    batch = json.loads(out.read_text())
    assert batch["summary"]["documents"] == 2 and batch["cached"] is False
    assert batch["invoices"][1]["status"] == "ERROR"
    assert batch["downloads"]["xlsx"].endswith("/export.xlsx")


def test_default_output_is_next_to_the_pdf(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    assert main(["extract", str(pdf), "--json"], fake_factory(), NOW) == 0
    assert (tmp_path / "in.json").exists()


def test_record_round_trips_through_replay(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    record = tmp_path / "rec.json"
    out = tmp_path / "out.json"
    assert (
        main(
            ["extract", str(pdf), "-o", str(out), "--json", "--record", str(record)],
            fake_factory(),
            NOW,
        )
        == 0
    )
    original = json.loads(out.read_text())
    recording = load_recording(record)
    assert recording.page_count == 2 and recording.pages_per_chunk == 10
    assert len(recording.calls) == 1

    replayed = replay_extraction(recording, pdf.read_bytes(), Settings(), date(2026, 11, 23))
    assert [i.model_dump(mode="json") for i in replayed.invoices] == original["invoices"]
    assert replayed.input_tokens == original["usage"]["input_tokens"]
    assert replayed.model == original["model"]


def test_replay_llm_rejects_a_call_the_recording_lacks(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    record = tmp_path / "rec.json"
    main(["extract", str(pdf), "--json", "--record", str(record)], fake_factory(), NOW)
    replay = ReplayLLM(load_recording(record))
    with pytest.raises(ResiboError) as exc:
        replay.extract(b"", 3, 4, total_pages=4, file_name="x")
    assert exc.value.code == ErrorCode.INTERNAL


def test_failed_run_writes_no_recording(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    record = tmp_path / "rec.json"
    assert main(["extract", str(bad), "--record", str(record)], fake_factory(), NOW) == 1
    assert not record.exists()


def test_error_prints_code_and_message_and_exits_non_zero(tmp_path, capsys):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert main(["extract", str(bad)], fake_factory(), NOW) == 1
    assert capsys.readouterr().err.startswith("NOT_A_PDF: ")


def test_missing_key_without_injection_exits_with_a_clear_message(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    with pytest.raises(SystemExit) as exc:
        main(["extract", str(pdf)], None, NOW)
    assert "ANTHROPIC_API_KEY" in str(exc.value.code)


def test_missing_input_file_is_a_clear_message(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["extract", str(tmp_path / "nope.pdf")], fake_factory(), NOW)
    assert "nope.pdf" in str(exc.value.code)


def test_record_has_no_default_path():
    parser = build_parser()
    args = parser.parse_args(["extract", "x.pdf"])
    assert args.record is None
    with pytest.raises(SystemExit):
        parser.parse_args(["extract", "x.pdf", "--record"])  # a path is required
    with pytest.raises(ValueError):
        RecordingLLM(FakeLLM({}), "")


def test_model_flag_overrides_the_model(tmp_path):
    pdf = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "o.json"
    main(["extract", str(pdf), "-o", str(out), "--json", "--model", "claude-haiku-4-5"],
         fake_factory(), NOW)  # fmt: skip
    assert json.loads(out.read_text())["model"] == "claude-haiku-4-5"


def test_extract_batch_is_what_the_cli_uses():
    # the CLI has no extraction logic of its own: same entry point as the API
    from resibo import __main__

    assert __main__.extract_batch is extract_batch
