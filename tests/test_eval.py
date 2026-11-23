import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfWriter

from resibo.config import Settings
from resibo.extractor import extract_batch
from resibo.sample import load_recording
from tests.helpers import FakeLLM, raw_invoice, result

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location(
        "resibo_eval_script", ROOT / "scripts" / "eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def small_pdf(path: Path, pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path.read_bytes()


EXPECTED = {
    "format_version": 1,
    "anchor_date": "2026-11-23",
    "page_count": 2,
    "documents": [
        {
            "label": "1",
            "source_pages": [1],
            "expected_status": "OK",
            "status_optional": False,
            "required_flags": [],
            "optional_flags": [],
            "fields": {
                "seller_name": "Mabuhay Office Supply",
                "invoice_number": "SI-0001",
                "invoice_date": "2026-09-04",
                "total_amount": 11200.0,
                "document_type": "sales_invoice",
            },
            "line_items": [],
        },
        {
            "label": "2",
            "source_pages": [2],
            "expected_status": "ERROR",
            "status_optional": True,
            "required_flags": ["TOTAL_MISMATCH"],
            "optional_flags": ["LOW_CONFIDENCE"],
            "fields": {"invoice_number": "SI-0002", "total_amount": 11500.0},
            "line_items": [{"description": "x"}],
        },
    ],
}


def run(tmp_path, second: dict):
    data = small_pdf(tmp_path / "x.pdf")
    llm = FakeLLM({(1, 2): result(raw_invoice(source_pages=[1]), second)})
    return extract_batch(data, "x.pdf", llm, Settings(), None, date(2026, 11, 23))


def second_doc(**kw):
    base = dict(
        source_pages=[2],
        invoice_number="SI-0002",
        seller_tin="999-999-999-00000",
        total_amount="99999.00",
    )
    base.update(kw)
    return raw_invoice(**base)


def test_perfect_run_scores_everything(ev, tmp_path):
    out = run(
        tmp_path,
        second_doc(total_amount="11500.00", vatable_sales="10000.00", vat_amount="1200.00"),
    )
    # 11500 vs 11200 computed -> TOTAL_MISMATCH raised, as expected
    score = ev.score_extraction(EXPECTED, out.invoices)
    report = ev.format_report(score)
    assert f"Fields correct: {score.correct}/{score.total} (" in report
    assert score.docs_found == 2 and score.docs_extra == 0
    assert score.correct == score.total
    assert score.missing_required == [] and score.unexpected_flags == []


def test_missing_optional_flag_does_not_reduce_the_score(ev, tmp_path):
    out = run(
        tmp_path,
        second_doc(total_amount="11500.00", vatable_sales="10000.00", vat_amount="1200.00"),
    )
    score = ev.score_extraction(EXPECTED, out.invoices)
    assert "LOW_CONFIDENCE" not in " ".join(score.optional_flags)  # it never fired
    assert score.correct == score.total


def test_optional_status_is_judged_only_when_its_optional_flag_fires(ev, tmp_path):
    expected = json.loads(json.dumps(EXPECTED))
    expected["documents"][1]["expected_status"] = "REVIEW"  # wrong on purpose
    out = run(
        tmp_path,
        second_doc(total_amount="11500.00", vatable_sales="10000.00", vat_amount="1200.00"),
    )
    score = ev.score_extraction(expected, out.invoices)
    assert not any(c.document == "2" and c.name == "status" for c in score.checks)
    fired = run(
        tmp_path,
        second_doc(
            total_amount="11500.00", vatable_sales="10000.00", vat_amount="1200.00",
            uncertain_fields=["total_amount"],
        ),
    )  # fmt: skip
    score = ev.score_extraction(expected, fired.invoices)
    status = next(c for c in score.checks if c.document == "2" and c.name == "status")
    assert not status.ok  # judged now: ERROR is not REVIEW
    assert any("LOW_CONFIDENCE" in f for f in score.optional_flags)


def test_missing_required_flag_reduces_the_score(ev, tmp_path):
    # the total matches the math here, so TOTAL_MISMATCH does not fire
    out = run(
        tmp_path,
        second_doc(total_amount="11200.00", vatable_sales="10000.00", vat_amount="1200.00"),
    )
    score = ev.score_extraction(EXPECTED, out.invoices)
    assert score.missing_required == ["2: TOTAL_MISMATCH"]
    assert score.correct < score.total
    assert "Missing required flags: 2: TOTAL_MISMATCH" in ev.format_report(score)


def test_missing_document_counts_against_the_score(ev, tmp_path):
    out = run(tmp_path, second_doc(source_pages=[2]))
    score = ev.score_extraction(EXPECTED, out.invoices[:1])
    assert score.docs_found == 1 and score.correct < score.total


def test_values_match_rules(ev):
    assert ev.values_match(11200.0, 11200.009)
    assert not ev.values_match(11200.0, 11200.02)
    assert ev.values_match("Mabuhay  Supply", "mabuhay supply")
    assert not ev.values_match("a", None)
    assert ev.values_match("2026-09-04", "2026-09-04")


def test_unexpected_flags_are_reported_not_scored(ev, tmp_path):
    out = run(
        tmp_path,
        second_doc(
            total_amount="11500.00",
            vatable_sales="10000.00",
            vat_amount="1200.00",
            is_complete=False,
        ),
    )
    score = ev.score_extraction(EXPECTED, out.invoices)
    assert score.unexpected_flags == ["2: INCOMPLETE_DOCUMENT"]


def test_main_without_a_key_exits_non_zero_and_makes_no_call(ev, capsys):
    code = ev.main([], None)
    assert code != 0
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def make_samples_dir(tmp_path: Path) -> Path:
    samples = tmp_path / "samples"
    samples.mkdir()
    small_pdf(samples / "sample-invoices.pdf")
    (samples / "expected.json").write_text(json.dumps(EXPECTED))
    return samples


def test_main_runs_with_an_injected_llm_records_and_never_edits_expected(ev, tmp_path, capsys):
    samples = make_samples_dir(tmp_path)
    before = (samples / "expected.json").read_bytes()
    llm = FakeLLM(
        {
            (1, 2): result(
                raw_invoice(source_pages=[1]),
                second_doc(total_amount="11500.00", vatable_sales="10000.00", vat_amount="1200.00"),
            )
        }
    )
    record = tmp_path / "rec.json"
    code = ev.main(["--samples", str(samples), "--record", str(record)], lambda settings: llm)
    out = capsys.readouterr().out
    assert code == 0
    assert "Fields correct:" in out and "Est. cost" in out and "Recorded to" in out
    assert (samples / "expected.json").read_bytes() == before
    assert not (samples / "sample-result.json").exists()
    assert load_recording(record).page_count == 2
