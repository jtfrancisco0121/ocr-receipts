import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from resibo.config import Settings
from resibo.models import (
    ErrorCode,
    Extraction,
    RawInvoice,
    ResiboError,
    Status,
    build_batch,
    normalize_invoice,
    parse_amount,
    summarize,
)
from resibo.sample import load_recording, replay_extraction
from tests.helpers import raw_invoice

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def norm(**overrides):
    return normalize_invoice(RawInvoice.model_validate(raw_invoice(**overrides)))


def test_money_serializes_as_json_numbers():
    inv = norm()
    data = inv.model_dump(mode="json")
    assert isinstance(data["total_amount"], float)
    assert data["total_amount"] == 11200.0
    assert data["vat_amount"] == 1200.0
    assert data["discount"] is None


def test_empty_string_becomes_none():
    inv = norm(buyer_tin="", payment_terms="  ")
    assert inv.buyer_tin is None and inv.payment_terms is None
    assert inv.uncertain_fields == []


def test_impossible_date_becomes_none_and_uncertain():
    inv = norm(invoice_date="2026-02-30")
    assert inv.invoice_date is None
    assert "invoice_date" in inv.uncertain_fields


def test_peso_amount_with_commas_parses():
    inv = norm(total_amount="₱11,200.00")
    assert inv.total_amount == Decimal("11200.00")
    assert parse_amount("PHP 1,234.5") == Decimal("1234.50")


def test_unparseable_amount_is_none_and_uncertain():
    inv = norm(vat_amount="twelve")
    assert inv.vat_amount is None and "vat_amount" in inv.uncertain_fields


def test_currency_normalization():
    assert norm(currency="").currency == "PHP"
    assert norm(currency="₱").currency == "PHP"
    assert norm(currency="php").currency == "PHP"
    assert norm(currency="usd").currency == "USD"
    odd = norm(currency="PH PESO??")
    assert odd.currency is None and "currency" in odd.uncertain_fields


def test_unknown_document_type_becomes_other():
    assert norm(document_type="weird").document_type.value == "other"


def test_empty_source_pages_is_structural_error():
    with pytest.raises(ValidationError):
        RawInvoice.model_validate(raw_invoice(source_pages=[]))


def test_line_items_normalized():
    inv = norm(
        line_items=[
            {
                "description": "Bond  paper",
                "quantity": "2",
                "unit": "ream",
                "unit_price": "250.00",
                "amount": "500.00",
            }
        ]
    )
    assert inv.line_items[0].description == "Bond paper"
    assert inv.line_items[0].amount == Decimal("500.00")
    assert inv.model_dump(mode="json")["line_items"][0]["quantity"] == 2.0


def _extraction(model: str) -> Extraction:
    inv = norm()
    inv.id = "INV-001"
    return Extraction(
        invoices=[inv],
        page_count=1,
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
    )


def test_build_batch_shape_and_cost():
    expires = datetime(2026, 11, 24, 5, 0, 0, 123, tzinfo=UTC)
    batch = build_batch(_extraction("claude-sonnet-5-5"), "abc", "f.pdf", expires)
    data = batch.model_dump(mode="json")
    assert data["usage"]["estimated_cost_usd"] == 3.0
    assert data["summary"]["documents"] == 1 and data["summary"]["ok"] == 1
    assert data["summary"]["total_amount_by_currency"] == {"PHP": 11200.0}
    assert data["downloads"]["xlsx"] == "/api/batches/abc/export.xlsx"
    assert data["downloads"]["invoices_csv"] == "/api/batches/abc/invoices.csv"
    assert data["downloads"]["line_items_csv"] == "/api/batches/abc/line_items.csv"
    assert data["expires_at"] == "2026-11-24T05:00:00Z"
    assert data["cached"] is False
    assert Status.OK == "OK"


def test_unknown_model_cost_is_null():
    expires = datetime(2026, 11, 24, 5, 0, 0, tzinfo=UTC)
    batch = build_batch(_extraction("mystery-model"), "abc", "f.pdf", expires)
    assert batch.model_dump(mode="json")["usage"]["estimated_cost_usd"] is None


def test_error_codes_have_http_status():
    assert ErrorCode.RATE_LIMITED.http_status == 429
    assert ErrorCode.SAMPLE_NOT_RECORDED.http_status == 404
    assert ErrorCode.DEMO_BUDGET_EXHAUSTED.http_status == 503
    err = ResiboError(ErrorCode.NOT_A_PDF, "nope")
    assert err.status_code == 400 and err.message == "nope"


def _invoice(status, total, currency="PHP"):
    inv = norm(total_amount=total, currency=currency)
    inv.status = status
    return inv


def test_sample_replay_total_leaves_out_error_invoices():
    recording = SAMPLES / "sample-result.json"
    if not recording.exists():
        pytest.skip("samples/sample-result.json is not recorded")
    expected = json.loads((SAMPLES / "expected.json").read_text())
    extraction = replay_extraction(
        load_recording(recording),
        (SAMPLES / "sample-invoices.pdf").read_bytes(),
        Settings(),
        date.fromisoformat(expected["anchor_date"]),
    )
    batch = build_batch(extraction, "abc", "f.pdf", datetime(2026, 11, 24, 5, tzinfo=UTC))
    summary = batch.model_dump(mode="json")["summary"]
    assert summary["total_amount_by_currency"] == {"PHP": 176100.0}
    assert summary["total_excluding_errors_by_currency"] == {"PHP": 155640.0}
    assert summary["error"] == 2


def test_summary_total_equal_when_no_invoice_is_an_error():
    summary = summarize([_invoice(Status.OK, "100.00"), _invoice(Status.REVIEW, "50.50")])
    assert summary.total_amount_by_currency == summary.total_excluding_errors_by_currency
    assert summary.total_excluding_errors_by_currency == {"PHP": Decimal("150.50")}


def test_summary_total_is_empty_when_every_invoice_is_an_error():
    summary = summarize([_invoice(Status.ERROR, "100.00"), _invoice(Status.ERROR, "20.00")])
    assert summary.total_amount_by_currency == {"PHP": Decimal("120.00")}
    assert summary.total_excluding_errors_by_currency == {}


def test_summary_total_error_only_currency_is_absent_and_order_is_kept():
    summary = summarize(
        [
            _invoice(Status.ERROR, "10.00", "USD"),
            _invoice(Status.OK, "100.00", "PHP"),
            _invoice(Status.ERROR, "5.00", "PHP"),
        ]
    )
    assert list(summary.total_amount_by_currency) == ["USD", "PHP"]
    assert summary.total_excluding_errors_by_currency == {"PHP": Decimal("100.00")}
    data = summary.model_dump(mode="json")
    assert data["total_excluding_errors_by_currency"] == {"PHP": 100.0}
    assert isinstance(data["total_excluding_errors_by_currency"]["PHP"], float)
