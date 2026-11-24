"""Exports: xlsx, invoices CSV, line items CSV and JSON (SPEC.md section 10).

One ordered column spec per sheet drives both xlsx and CSV (AD-14); ``safe_text`` guards every
text cell against formula injection in both formats.
"""

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from resibo.models import Batch, Invoice, LineItem

MANILA = ZoneInfo("Asia/Manila")

# Shared with static/styles.css (AD-15): status -> (fill, text colour); tints for whole rows.
STATUS_FILLS = {
    "OK": ("C6EFCE", "006100"),
    "REVIEW": ("FFEB9C", "9C5700"),
    "ERROR": ("FFC7CE", "9C0006"),
}
ROW_TINTS = {"REVIEW": "FFF8E1", "ERROR": "FDECEE"}
HEADER_FILL = "0F766E"
MONEY_FORMAT = "#,##0.00"
QTY_FORMAT = "#,##0.00##"
DATE_FORMAT = "yyyy-mm-dd"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str | None) -> str | None:
    """Prefix ``'`` to text that a spreadsheet would read as a formula."""
    if not value:
        return value
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


@dataclass(frozen=True)
class Column:
    header: str
    kind: str  # text | money | qty | date | int | pages | flags
    get: Callable[[Any], Any]
    wrap: bool = False


def format_pages(pages: list[int]) -> str:
    ranges: list[str] = []
    ordered = sorted(set(pages))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        ranges.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ", ".join(ranges)


def _inv(name: str) -> Callable[[Invoice], Any]:
    return lambda inv: getattr(inv, name)


INVOICE_COLUMNS: list[Column] = [
    Column("ID", "text", _inv("id")),
    Column("Status", "text", lambda inv: inv.status.value),
    Column("Flags", "flags", lambda inv: "; ".join(f.message for f in inv.flags), wrap=True),
    Column("Document Type", "text", lambda inv: inv.document_type.value),
    Column("Seller", "text", _inv("seller_name")),
    Column("Seller TIN", "text", _inv("seller_tin")),
    Column("Buyer", "text", _inv("buyer_name")),
    Column("Buyer TIN", "text", _inv("buyer_tin")),
    Column("Invoice No.", "text", _inv("invoice_number")),
    Column("Invoice Date", "date", _inv("invoice_date")),
    Column("Due Date", "date", _inv("due_date")),
    Column("Currency", "text", _inv("currency")),
    Column("VATable Sales", "money", _inv("vatable_sales")),
    Column("VAT-Exempt", "money", _inv("vat_exempt_sales")),
    Column("Zero-Rated", "money", _inv("zero_rated_sales")),
    Column("Subtotal", "money", _inv("subtotal")),
    Column("VAT", "money", _inv("vat_amount")),
    Column("Discount", "money", _inv("discount")),
    Column("Withholding Tax", "money", _inv("withholding_tax")),
    Column("Total", "money", _inv("total_amount")),
    Column("Payment Terms", "text", _inv("payment_terms")),
    Column("Payment Method", "text", _inv("payment_method")),
    Column("Pages", "pages", lambda inv: format_pages(inv.source_pages)),
    Column("Uncertain Fields", "text", lambda inv: ", ".join(inv.uncertain_fields)),
    Column("Notes", "text", _inv("extraction_notes"), wrap=True),
]

# line item rows are (invoice, line number, line item)
LINE_ITEM_COLUMNS: list[Column] = [
    Column("Invoice ID", "text", lambda r: r[0].id),
    Column("Invoice No.", "text", lambda r: r[0].invoice_number),
    Column("Seller", "text", lambda r: r[0].seller_name),
    Column("Line #", "int", lambda r: r[1]),
    Column("Description", "text", lambda r: r[2].description),
    Column("Qty", "qty", lambda r: r[2].quantity),
    Column("Unit", "text", lambda r: r[2].unit),
    Column("Unit Price", "money", lambda r: r[2].unit_price),
    Column("Amount", "money", lambda r: r[2].amount),
]


def line_item_rows(batch: Batch) -> list[tuple[Invoice, int, LineItem]]:
    return [
        (inv, number, item)
        for inv in batch.invoices
        for number, item in enumerate(inv.line_items, start=1)
    ]


# --- CSV ------------------------------------------------------------------------------------


def _csv_value(column: Column, value: Any) -> str:
    if value is None or value == "":
        return ""
    if column.kind == "money":
        return f"{Decimal(value):.2f}"
    if column.kind == "qty":
        return format(Decimal(value).normalize(), "f")
    if column.kind == "date":
        return value.isoformat()
    if column.kind == "int":
        return str(value)
    return safe_text(str(value)) or ""


def _to_csv(columns: list[Column], rows: list[Any]) -> bytes:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c.header for c in columns])
    for row in rows:
        writer.writerow([_csv_value(c, c.get(row)) for c in columns])
    return buffer.getvalue().encode("utf-8-sig")


def to_invoices_csv(batch: Batch) -> bytes:
    return _to_csv(INVOICE_COLUMNS, batch.invoices)


def to_line_items_csv(batch: Batch) -> bytes:
    return _to_csv(LINE_ITEM_COLUMNS, line_item_rows(batch))


def to_json(batch: Batch) -> str:
    return json.dumps(batch.model_dump(mode="json"), indent=2, ensure_ascii=False)


# --- xlsx -----------------------------------------------------------------------------------


def _fill(hex_color: str) -> PatternFill:
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")


def _cell_value(column: Column, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column.kind in ("money", "qty"):
        return float(value)
    if column.kind in ("date", "int"):
        return value
    return safe_text(str(value))


def _style_header(ws, headers: list[str], row: int = 1) -> None:
    for index, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=index, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _fill(HEADER_FILL)
        cell.alignment = Alignment(vertical="center")


def _autosize(ws) -> None:
    for column_cells in ws.columns:
        longest = 0
        for cell in column_cells:
            if cell.value is None:
                continue
            text = cell.value.isoformat() if isinstance(cell.value, date) else str(cell.value)
            longest = max(longest, max(len(line) for line in text.split("\n")))
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(
            max(longest + 2, 8), 50
        )


def _write_table(
    ws, columns: list[Column], rows: list[Any], status_of: Callable[[Any], str | None]
) -> None:
    _style_header(ws, [c.header for c in columns])
    for row_number, row in enumerate(rows, start=2):
        status = status_of(row)
        for col_number, column in enumerate(columns, start=1):
            cell = ws.cell(row=row_number, column=col_number)
            cell.value = _cell_value(column, column.get(row))
            if column.kind == "money":
                cell.number_format = MONEY_FORMAT
            elif column.kind == "qty":
                cell.number_format = QTY_FORMAT
            elif column.kind == "date":
                cell.number_format = DATE_FORMAT
            if column.wrap:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if status in ROW_TINTS:
                cell.fill = _fill(ROW_TINTS[status])
            if column.header == "Status" and status in STATUS_FILLS:
                bg, fg = STATUS_FILLS[status]
                cell.fill = _fill(bg)
                cell.font = Font(bold=True, color=fg)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(len(rows) + 1, 1)}"
    _autosize(ws)


def _write_summary(ws, batch: Batch, processed_at: datetime) -> None:
    ws.append(["Summary"])
    ws["A1"].font = Font(bold=True, size=14)
    s, u = batch.summary, batch.usage
    local = processed_at.astimezone(MANILA).strftime("%Y-%m-%d %H:%M (Asia/Manila)")
    rows: list[tuple[str, Any, str | None]] = [
        ("File name", safe_text(batch.file_name), None),
        ("Processed at", local, None),
        ("Model", safe_text(batch.model), None),
        ("Pages", batch.page_count, None),
        ("Documents found", s.documents, None),
        ("OK", s.ok, None),
        ("Review", s.review, None),
        ("Error", s.error, None),
    ]
    for currency, total in s.total_amount_by_currency.items():
        label = safe_text(currency)
        if s.error > 0:
            # ERROR rows stay in every sheet, but the headline total leaves them out
            without_errors = s.total_excluding_errors_by_currency.get(currency, 0)
            rows.append((f"Total, excluding errors ({label})", float(without_errors), MONEY_FORMAT))
            rows.append((f"Total, all documents ({label})", float(total), MONEY_FORMAT))
        else:
            rows.append((f"Total ({label})", float(total), MONEY_FORMAT))
    if s.error > 0:
        rows.append(
            (
                f"{s.error} error rows are kept on the Invoices sheet "
                "so you can check or delete them.",
                None,
                None,
            )
        )
    rows += [
        ("Input tokens", u.input_tokens, "#,##0"),
        ("Output tokens", u.output_tokens, "#,##0"),
        (
            "Estimated cost (USD)",
            u.estimated_cost_usd if u.estimated_cost_usd is not None else "n/a",
            "0.0000" if u.estimated_cost_usd is not None else None,
        ),
    ]
    for label, value, number_format in rows:
        ws.append([label, value])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        if number_format:
            ws.cell(row=ws.max_row, column=2).number_format = number_format
        ws.cell(row=ws.max_row, column=2).alignment = Alignment(horizontal="left")
    ws.append([])
    header_row = ws.max_row + 1
    _style_header(ws, ["Invoice ID", "Code", "Severity", "Field", "Message"], row=header_row)
    for inv in batch.invoices:
        for flag in inv.flags:
            ws.append(
                [
                    safe_text(inv.id),
                    safe_text(flag.code),
                    flag.severity.value,
                    safe_text(flag.field),
                    safe_text(flag.message),
                ]
            )
    _autosize(ws)
    ws.column_dimensions["E"].width = 50


def to_xlsx(batch: Batch, processed_at: datetime | None = None) -> bytes:
    """The workbook: Invoices, Line Items, Summary. ``processed_at`` defaults to now."""
    processed_at = processed_at or datetime.now(UTC)
    wb = Workbook()
    invoices_ws = wb.active
    invoices_ws.title = "Invoices"
    _write_table(invoices_ws, INVOICE_COLUMNS, batch.invoices, lambda inv: inv.status.value)
    _write_table(
        wb.create_sheet("Line Items"),
        LINE_ITEM_COLUMNS,
        line_item_rows(batch),
        lambda row: row[0].status.value,
    )
    _write_summary(wb.create_sheet("Summary"), batch, processed_at)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
