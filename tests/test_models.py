from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from resibo.models import (
    ErrorCode,
    Extraction,
    RawInvoice,
    ResiboError,
    Status,
    build_batch,
    normalize_invoice,
    parse_amount,
)
from tests.helpers import raw_invoice


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
