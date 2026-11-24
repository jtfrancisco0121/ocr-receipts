import csv
from datetime import UTC, date, datetime
from decimal import Decimal as D
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from resibo.exporters import (
    HEADER_FILL,
    INVOICE_COLUMNS,
    LINE_ITEM_COLUMNS,
    ROW_TINTS,
    STATUS_FILLS,
    format_pages,
    safe_text,
    to_invoices_csv,
    to_json,
    to_line_items_csv,
    to_xlsx,
)
from resibo.models import (
    Batch,
    DocumentType,
    Extraction,
    Flag,
    Invoice,
    LineItem,
    Severity,
    Status,
    build_batch,
    summarize,
)

EXPIRES = datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
PROCESSED = datetime(2026, 11, 23, 4, 30, tzinfo=UTC)


def make_batch(**overrides) -> Batch:
    first = Invoice(
        id="INV-001",
        status=Status.OK,
        document_type=DocumentType.SALES_INVOICE,
        seller_name="Mabuhay Supply",
        seller_tin="123-456-789-00000",
        invoice_number="SI-1",
        invoice_date=date(2026, 9, 4),
        vatable_sales=D("10000.00"),
        vat_amount=D("1200.00"),
        total_amount=D("11200.00"),
        line_items=[
            LineItem(
                description="Paper",
                quantity=D("2"),
                unit="ream",
                unit_price=D("5600.00"),
                amount=D("11200.00"),
            ),
            LineItem(description="Pens", amount=D("0.50")),
        ],
        source_pages=[1, 2],
    )
    second = Invoice(
        id="INV-002",
        status=Status.REVIEW,
        flags=[
            Flag(
                code="LOW_CONFIDENCE", severity=Severity.WARNING, field=None, message="Blurry total"
            )
        ],
        seller_name="Corner Store",
        total_amount=D("55.00"),
        source_pages=[3],
        uncertain_fields=["total_amount"],
        extraction_notes="Handwritten total",
    )
    third = Invoice(
        id="INV-003",
        status=Status.ERROR,
        flags=[
            Flag(
                code="TOTAL_MISMATCH",
                severity=Severity.ERROR,
                field="total_amount",
                message="Computed ₱1,000.00 but document says ₱1,300.00",
            ),
            Flag(code="LOW_CONFIDENCE", severity=Severity.WARNING, field=None, message="Stamp"),
        ],
        seller_name="Other Co",
        total_amount=D("1300.00"),
        source_pages=[4],
    )
    for key, value in overrides.items():
        setattr(first, key, value)
    extraction = Extraction(
        invoices=[first, second, third],
        page_count=4,
        model="claude-sonnet-5-5",
        duration_ms=1500,
        input_tokens=2000,
        output_tokens=300,
    )
    return build_batch(extraction, "abc123", "march.pdf", EXPIRES)


def workbook(batch=None):
    return load_workbook(BytesIO(to_xlsx(batch or make_batch(), PROCESSED)))


def test_three_sheets_and_headers_in_order():
    wb = workbook()
    assert wb.sheetnames == ["Invoices", "Line Items", "Summary"]
    headers = [c.value for c in wb["Invoices"][1]]
    assert headers == [
        "ID", "Status", "Flags", "Document Type", "Seller", "Seller TIN", "Buyer", "Buyer TIN",
        "Invoice No.", "Invoice Date", "Due Date", "Currency", "VATable Sales", "VAT-Exempt",
        "Zero-Rated", "Subtotal", "VAT", "Discount", "Withholding Tax", "Total", "Payment Terms",
        "Payment Method", "Pages", "Uncertain Fields", "Notes",
    ]  # fmt: skip
    assert [c.value for c in wb["Line Items"][1]] == [
        "Invoice ID", "Invoice No.", "Seller", "Line #", "Description", "Qty", "Unit",
        "Unit Price", "Amount",
    ]  # fmt: skip


def test_row_counts_values_and_formats():
    wb = workbook()
    ws = wb["Invoices"]
    assert ws.max_row == 4  # header + 3 invoices
    assert wb["Line Items"].max_row == 3  # header + 2 lines
    row = {ws.cell(1, c).value: ws.cell(2, c) for c in range(1, ws.max_column + 1)}
    assert row["Total"].value == 11200.0 and row["Total"].number_format == "#,##0.00"
    assert row["Invoice Date"].value == datetime(2026, 9, 4)  # a real date cell, not text
    assert row["Invoice Date"].number_format == "yyyy-mm-dd"
    assert row["Pages"].value == "1-2"
    assert ws.cell(3, 2).value == "REVIEW"
    assert ws.cell(3, 3).value == "Blurry total"
    assert ws.cell(4, 3).value == "Computed ₱1,000.00 but document says ₱1,300.00; Stamp"
    lines = wb["Line Items"]
    assert lines.cell(2, 4).value == 1 and lines.cell(3, 4).value == 2
    assert lines.cell(2, 9).number_format == "#,##0.00"


def test_status_fills_row_tints_freeze_and_filter():
    ws = workbook()["Invoices"]
    for row, status in ((2, "OK"), (3, "REVIEW"), (4, "ERROR")):
        cell = ws.cell(row, 2)
        assert cell.fill.start_color.rgb.endswith(STATUS_FILLS[status][0])
        assert cell.font.color.rgb.endswith(STATUS_FILLS[status][1])
    assert ws.cell(3, 5).fill.start_color.rgb.endswith(ROW_TINTS["REVIEW"])
    assert ws.cell(4, 5).fill.start_color.rgb.endswith(ROW_TINTS["ERROR"])
    assert ws.cell(2, 5).fill.fill_type is None
    assert ws.cell(1, 1).font.bold and ws.cell(1, 1).fill.start_color.rgb.endswith(HEADER_FILL)
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:Y4"
    assert ws.cell(3, 3).alignment.wrap_text and ws.cell(3, 25).alignment.wrap_text
    for name in ("Invoices", "Line Items"):
        assert workbook()[name].column_dimensions["A"].width <= 50


def test_summary_sheet():
    ws = workbook()["Summary"]
    cells = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert cells["File name"] == "march.pdf"
    assert cells["Processed at"] == "2026-11-23 12:30 (Asia/Manila)"
    assert cells["Documents found"] == 3 and cells["OK"] == 1
    assert cells["Review"] == 1 and cells["Error"] == 1
    assert cells["Total, excluding errors (PHP)"] == pytest.approx(11200 + 55)
    assert cells["Total, all documents (PHP)"] == pytest.approx(11200 + 55 + 1300)
    assert "Total (PHP)" not in cells
    assert (
        cells["1 error rows are kept on the Invoices sheet so you can check or delete them."]
        is None
    )
    assert cells["Input tokens"] == 2000
    header_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "Invoice ID")
    assert [c.value for c in ws[header_row]][:5] == [
        "Invoice ID", "Code", "Severity", "Field", "Message",
    ]  # fmt: skip
    assert ws.max_row - header_row == 3  # one row per flag
    assert ws.cell(header_row + 1, 2).value == "LOW_CONFIDENCE"


def _batch_without_errors() -> Batch:
    batch = make_batch()
    batch.invoices = [i for i in batch.invoices if i.status != Status.ERROR]
    batch.summary = summarize(batch.invoices)
    return batch


def test_summary_sheet_without_errors_keeps_one_total_row_and_no_note():
    ws = workbook(_batch_without_errors())["Summary"]
    labels = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
    cells = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert cells["Total (PHP)"] == pytest.approx(11200 + 55)
    assert not [label for label in labels if label and "excluding errors" in str(label)]
    assert not [label for label in labels if label and "error rows are kept" in str(label)]


def test_summary_sheet_error_only_currency_shows_zero_in_total_order():
    batch = make_batch()
    batch.invoices[2].currency = "USD"
    batch.invoices[0].currency = "PHP"
    batch.summary = summarize([batch.invoices[2], batch.invoices[0], batch.invoices[1]])
    ws = workbook(batch)["Summary"]
    rows = [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
    totals = [(label, value) for label, value in rows if label and str(label).startswith("Total")]
    assert [label for label, _ in totals] == [
        "Total, excluding errors (USD)",
        "Total, all documents (USD)",
        "Total, excluding errors (PHP)",
        "Total, all documents (PHP)",
    ]
    assert totals[0][1] == 0 and totals[1][1] == pytest.approx(1300)
    assert totals[2][1] == pytest.approx(11200 + 55)


def test_error_invoices_stay_in_every_sheet_and_both_csvs():
    batch = make_batch()
    wb = workbook(batch)
    invoices = wb["Invoices"]
    headers = [c.value for c in invoices[1]]
    error_row = next(r for r in invoices.iter_rows(min_row=2) if r[0].value == "INV-003")
    assert error_row[headers.index("Status")].value == "ERROR"
    assert "Computed ₱1,000.00" in str(error_row[headers.index("Flags")].value)
    rows = parse_csv(to_invoices_csv(batch))
    error_csv = next(r for r in rows if r[0] == "INV-003")
    assert error_csv[rows[0].index("Status")] == "ERROR"
    assert "Computed ₱1,000.00" in error_csv[rows[0].index("Flags")]
    assert any(r[0] == "INV-003" for r in rows)


def parse_csv(data: bytes):
    assert data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(StringIO(data.decode("utf-8-sig"))))


def test_csv_bom_columns_match_sheet_columns_and_plain_numbers():
    batch = make_batch()
    rows = parse_csv(to_invoices_csv(batch))
    assert rows[0] == [c.header for c in INVOICE_COLUMNS]
    assert rows[1][rows[0].index("Total")] == "11200.00"
    assert rows[1][rows[0].index("Invoice Date")] == "2026-09-04"
    assert rows[1][rows[0].index("Discount")] == ""
    assert len(rows) == 4
    lines = parse_csv(to_line_items_csv(batch))
    assert lines[0] == [c.header for c in LINE_ITEM_COLUMNS]
    assert lines[1][5] == "2" and lines[1][8] == "11200.00" and len(lines) == 3


def test_csv_keeps_peso_sign_and_filipino_names():
    batch = make_batch(seller_name="Peña’s Café ₱")
    assert "Peña’s Café ₱" in parse_csv(to_invoices_csv(batch))[1]


INJECTIONS = ["=cmd", "+1", "-1", "@x", "\tx", "\rx"]


@pytest.mark.parametrize("text", INJECTIONS)
def test_formula_injection_stays_text_in_xlsx_and_csv(text):
    batch = make_batch(seller_name=text, invoice_number=text, extraction_notes=text)
    ws = workbook(batch)["Invoices"]
    # XML storage normalizes a bare CR to LF on reload; the prefix is what matters
    stored = ws.cell(2, 5).value
    assert stored.startswith("'") and stored[1:] == text.replace("\r", "\n")
    assert ws.cell(2, 5).data_type == "s"
    rows = parse_csv(to_invoices_csv(batch))
    assert rows[1][4].startswith("'") and rows[1][8].startswith("'")
    assert to_invoices_csv(batch).decode("utf-8-sig").count("'" + text) >= 3
    lines = parse_csv(to_line_items_csv(batch))
    assert lines[1][1].startswith("'") and lines[1][2].startswith("'")


def test_formula_injection_in_line_items_and_summary():
    batch = make_batch()
    batch.invoices[0].line_items[0].description = '=HYPERLINK("http://x")'
    batch.file_name = "=evil.pdf"
    wb = workbook(batch)
    assert wb["Line Items"].cell(2, 5).value.startswith("'=HYPERLINK")
    assert wb["Summary"].cell(2, 2).value == "'=evil.pdf"
    assert parse_csv(to_line_items_csv(batch))[1][4].startswith("'=HYPERLINK")


def test_safe_text_leaves_normal_text_alone():
    assert safe_text("Mabuhay") == "Mabuhay" and safe_text("") == "" and safe_text(None) is None
    assert safe_text("1-2") == "1-2"


def test_pages_format_and_json():
    assert format_pages([3]) == "3" and format_pages([1, 2, 3, 5]) == "1-3, 5"
    data = to_json(make_batch())
    assert '"batch_id": "abc123"' in data and "11200.0" in data


def test_empty_batch_exports():
    batch = build_batch(
        Extraction(invoices=[], page_count=1, model="claude-sonnet-5-5"), "e", "e.pdf", EXPIRES
    )
    wb = load_workbook(BytesIO(to_xlsx(batch)))
    assert wb["Invoices"].max_row == 1
    assert len(parse_csv(to_invoices_csv(batch))) == 1
