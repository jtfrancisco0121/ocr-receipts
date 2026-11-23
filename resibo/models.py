"""Data types: Invoice, LineItem, Flag, Batch, the raw model output, errors and normalization.

Money is ``Decimal`` everywhere and serialized as a JSON number (AD-7).
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from resibo import config

Money = Annotated[
    Decimal,
    PlainSerializer(lambda d: float(round(d, 2)), return_type=float, when_used="json"),
]
Quantity = Annotated[
    Decimal,
    PlainSerializer(lambda d: float(d), return_type=float, when_used="json"),
]

TWO_PLACES = Decimal("0.01")


# --- errors ---------------------------------------------------------------------------------


class ErrorCode(StrEnum):
    NOT_A_PDF = "NOT_A_PDF"
    PDF_EMPTY = "PDF_EMPTY"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_UNREADABLE = "PDF_UNREADABLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    RATE_LIMITED = "RATE_LIMITED"
    BATCH_NOT_FOUND = "BATCH_NOT_FOUND"
    SAMPLE_NOT_RECORDED = "SAMPLE_NOT_RECORDED"
    EXTRACTION_TRUNCATED = "EXTRACTION_TRUNCATED"
    EXTRACTION_INVALID = "EXTRACTION_INVALID"
    UPSTREAM_REFUSED = "UPSTREAM_REFUSED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    DEMO_BUDGET_EXHAUSTED = "DEMO_BUDGET_EXHAUSTED"
    INTERNAL = "INTERNAL"

    @property
    def http_status(self) -> int:
        return ERROR_STATUS[self]


ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NOT_A_PDF: 400,
    ErrorCode.PDF_EMPTY: 400,
    ErrorCode.PDF_ENCRYPTED: 400,
    ErrorCode.PDF_UNREADABLE: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.TOO_MANY_PAGES: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.BATCH_NOT_FOUND: 404,
    ErrorCode.SAMPLE_NOT_RECORDED: 404,
    ErrorCode.EXTRACTION_TRUNCATED: 502,
    ErrorCode.EXTRACTION_INVALID: 502,
    ErrorCode.UPSTREAM_REFUSED: 502,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.DEMO_BUDGET_EXHAUSTED: 503,
    ErrorCode.INTERNAL: 500,
}


class ResiboError(Exception):
    """The one error type raised by the core and the adapters (AD-6)."""

    def __init__(self, code: ErrorCode, message: str, headers: dict[str, str] | None = None):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.headers = headers or {}

    @property
    def status_code(self) -> int:
        return self.code.http_status


# --- enums ----------------------------------------------------------------------------------


class DocumentType(StrEnum):
    SALES_INVOICE = "sales_invoice"
    SERVICE_INVOICE = "service_invoice"
    OFFICIAL_RECEIPT = "official_receipt"
    CASH_RECEIPT = "cash_receipt"
    DELIVERY_RECEIPT = "delivery_receipt"
    BILLING_STATEMENT = "billing_statement"
    OTHER = "other"


class Status(StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


# --- typed records --------------------------------------------------------------------------


class LineItem(BaseModel):
    description: str = ""
    quantity: Quantity | None = None
    unit: str | None = None
    unit_price: Money | None = None
    amount: Money | None = None


class Flag(BaseModel):
    code: str
    severity: Severity
    field: str | None = None
    message: str


class Invoice(BaseModel):
    id: str = ""
    status: Status = Status.OK
    flags: list[Flag] = Field(default_factory=list)
    document_type: DocumentType = DocumentType.OTHER
    seller_name: str | None = None
    seller_tin: str | None = None
    seller_address: str | None = None
    buyer_name: str | None = None
    buyer_tin: str | None = None
    buyer_address: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = None
    payment_method: str | None = None
    currency: str | None = "PHP"
    vatable_sales: Money | None = None
    vat_exempt_sales: Money | None = None
    zero_rated_sales: Money | None = None
    subtotal: Money | None = None
    vat_amount: Money | None = None
    discount: Money | None = None
    withholding_tax: Money | None = None
    total_amount: Money | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    is_complete: bool = True
    uncertain_fields: list[str] = Field(default_factory=list)
    extraction_notes: str | None = None


MONEY_FIELDS = (
    "vatable_sales",
    "vat_exempt_sales",
    "zero_rated_sales",
    "subtotal",
    "vat_amount",
    "discount",
    "withholding_tax",
    "total_amount",
)


# --- raw model output (all scalars are str, mirroring the section 8.7 schema) ---------------


class RawLineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = ""
    quantity: str = ""
    unit: str = ""
    unit_price: str = ""
    amount: str = ""


class RawInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: str = "other"
    seller_name: str = ""
    seller_tin: str = ""
    seller_address: str = ""
    buyer_name: str = ""
    buyer_tin: str = ""
    buyer_address: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    payment_terms: str = ""
    payment_method: str = ""
    currency: str = ""
    vatable_sales: str = ""
    vat_exempt_sales: str = ""
    zero_rated_sales: str = ""
    subtotal: str = ""
    vat_amount: str = ""
    discount: str = ""
    withholding_tax: str = ""
    total_amount: str = ""
    line_items: list[RawLineItem] = Field(default_factory=list)
    source_pages: list[int] = Field(min_length=1)
    is_complete: bool = True
    uncertain_fields: list[str] = Field(default_factory=list)
    extraction_notes: str = ""


class RawOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoices: list[RawInvoice]


# --- normalization (section 8.5) ------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_ALIASES = {"₱": "PHP", "P": "PHP", "PHP": "PHP", "PESO": "PHP", "PESOS": "PHP"}


def clean_text(value: str, collapse: bool = False) -> str | None:
    text = value.strip()
    if collapse:
        text = " ".join(text.split())
    return text or None


def parse_amount(value: str, places: Decimal | None = TWO_PLACES) -> Decimal | None:
    """Parse a printed amount; None when empty or unparseable."""
    text = value.strip()
    if not text:
        return None
    text = re.sub(r"(?i)php|₱", "", text)
    text = text.replace(",", "").replace(" ", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number.quantize(places, rounding=ROUND_HALF_UP) if places else number


def parse_date(value: str) -> date | None:
    text = value.strip()
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_currency(value: str) -> str | None:
    """Uppercase and map peso spellings to PHP; empty is PHP; an unknown code is None."""
    text = value.strip().upper()
    if not text:
        return "PHP"
    if text in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[text]
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    return None


def normalize_invoice(raw: RawInvoice) -> Invoice:
    """Turn the all-str raw invoice into the typed Invoice. Value problems never raise: the
    value becomes None and the field name is added to ``uncertain_fields``."""
    uncertain = list(raw.uncertain_fields)

    def mark(name: str) -> None:
        if name not in uncertain:
            uncertain.append(name)

    money: dict[str, Decimal | None] = {}
    for name in MONEY_FIELDS:
        text = getattr(raw, name)
        parsed = parse_amount(text)
        if parsed is None and text.strip():
            mark(name)
        money[name] = parsed

    dates: dict[str, date | None] = {}
    for name in ("invoice_date", "due_date"):
        text = getattr(raw, name)
        parsed_date = parse_date(text)
        if parsed_date is None and text.strip():
            mark(name)
        dates[name] = parsed_date

    currency = normalize_currency(raw.currency)
    if currency is None:
        mark("currency")

    try:
        doc_type = DocumentType(raw.document_type.strip())
    except ValueError:
        doc_type = DocumentType.OTHER

    items: list[LineItem] = []
    for item in raw.line_items:
        quantity = parse_amount(item.quantity, places=None)
        unit_price = parse_amount(item.unit_price)
        amount = parse_amount(item.amount)
        for text, parsed in (
            (item.quantity, quantity),
            (item.unit_price, unit_price),
            (item.amount, amount),
        ):
            if parsed is None and text.strip():
                mark("line_items")
        items.append(
            LineItem(
                description=" ".join(item.description.split()),
                quantity=quantity,
                unit=clean_text(item.unit),
                unit_price=unit_price,
                amount=amount,
            )
        )

    return Invoice(
        document_type=doc_type,
        seller_name=clean_text(raw.seller_name, collapse=True),
        seller_tin=clean_text(raw.seller_tin),
        seller_address=clean_text(raw.seller_address, collapse=True),
        buyer_name=clean_text(raw.buyer_name, collapse=True),
        buyer_tin=clean_text(raw.buyer_tin),
        buyer_address=clean_text(raw.buyer_address, collapse=True),
        invoice_number=clean_text(raw.invoice_number),
        invoice_date=dates["invoice_date"],
        due_date=dates["due_date"],
        payment_terms=clean_text(raw.payment_terms),
        payment_method=clean_text(raw.payment_method),
        currency=currency,
        line_items=items,
        source_pages=sorted(set(raw.source_pages)),
        is_complete=raw.is_complete,
        uncertain_fields=uncertain,
        extraction_notes=clean_text(raw.extraction_notes),
        **money,
    )


# --- extraction result and Batch ------------------------------------------------------------


@dataclass
class Extraction:
    """What ``extractor.extract_batch`` returns; shells turn it into a Batch (AD-21)."""

    invoices: list[Invoice]
    page_count: int
    model: str
    chunks: int = 0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    repair_retries: int = 0
    halvings: int = 0
    llm_calls: int = 0


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None


class Summary(BaseModel):
    documents: int = 0
    ok: int = 0
    review: int = 0
    error: int = 0
    total_amount_by_currency: dict[str, Money] = Field(default_factory=dict)


class Downloads(BaseModel):
    xlsx: str
    invoices_csv: str
    line_items_csv: str


class Batch(BaseModel):
    batch_id: str
    file_name: str
    page_count: int
    model: str
    duration_ms: int
    usage: Usage
    cached: bool = False
    summary: Summary
    invoices: list[Invoice]
    downloads: Downloads
    expires_at: datetime
    recorded_at: datetime | None = None  # only set on the cached sample (original run time)


def summarize(invoices: list[Invoice]) -> Summary:
    totals: dict[str, Decimal] = {}
    for inv in invoices:
        if inv.total_amount is not None and inv.currency:
            totals[inv.currency] = totals.get(inv.currency, Decimal(0)) + inv.total_amount
    return Summary(
        documents=len(invoices),
        ok=sum(1 for i in invoices if i.status == Status.OK),
        review=sum(1 for i in invoices if i.status == Status.REVIEW),
        error=sum(1 for i in invoices if i.status == Status.ERROR),
        total_amount_by_currency=totals,
    )


def build_batch(
    extraction: Extraction,
    batch_id: str,
    file_name: str,
    expires_at: datetime,
    cached: bool = False,
    recorded_at: datetime | None = None,
) -> Batch:
    """The only place a Batch is assembled: summary, usage cost and download paths."""
    cost = config.estimate_cost(extraction.model, extraction.input_tokens, extraction.output_tokens)
    base = f"/api/batches/{batch_id}"
    return Batch(
        batch_id=batch_id,
        file_name=file_name,
        page_count=extraction.page_count,
        model=extraction.model,
        duration_ms=extraction.duration_ms,
        usage=Usage(
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            estimated_cost_usd=float(cost) if cost is not None else None,
        ),
        cached=cached,
        summary=summarize(extraction.invoices),
        invoices=extraction.invoices,
        downloads=Downloads(
            xlsx=f"{base}/export.xlsx",
            invoices_csv=f"{base}/invoices.csv",
            line_items_csv=f"{base}/line_items.csv",
        ),
        expires_at=expires_at.astimezone(UTC).replace(microsecond=0),
        recorded_at=recorded_at.astimezone(UTC).replace(microsecond=0) if recorded_at else None,
    )
