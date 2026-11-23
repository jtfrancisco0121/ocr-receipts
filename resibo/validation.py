"""Business rules: pure functions that turn an Invoice into flags and a status.

No I/O and no clock: ``today`` is a parameter (AD-9). Money math is ``Decimal`` only (AD-7).
"""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import product
from zoneinfo import ZoneInfo

from resibo.config import Settings
from resibo.models import (
    MONEY_FIELDS,
    DocumentType,
    Flag,
    Invoice,
    Severity,
    Status,
)

SPLIT_ACROSS_CHUNKS = "SPLIT_ACROSS_CHUNKS"
MANILA = ZoneInfo("Asia/Manila")
ZERO = Decimal(0)

_FULL_DOCS = {
    DocumentType.SALES_INVOICE,
    DocumentType.SERVICE_INVOICE,
    DocumentType.OFFICIAL_RECEIPT,
    DocumentType.BILLING_STATEMENT,
}
_RECEIPT_DOCS = {DocumentType.CASH_RECEIPT, DocumentType.OTHER}


def manila_today(now: datetime) -> date:
    """The business date in Asia/Manila for an aware ``now``."""
    return (
        now.astimezone(MANILA).date()
        if now.tzinfo
        else now.replace(tzinfo=UTC).astimezone(MANILA).date()
    )


def money_text(amount: Decimal, currency: str | None) -> str:
    sign = "₱" if currency in (None, "PHP") else f"{currency} "
    return f"{sign}{amount:,.2f}"


def _flag(code: str, severity: Severity, field: str | None, message: str) -> Flag:
    return Flag(code=code, severity=severity, field=field, message=message)


def _v(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


# --- single-invoice rules -------------------------------------------------------------------


def required_fields(doc_type: DocumentType) -> list[tuple[str, Severity]]:
    err, warn = Severity.ERROR, Severity.WARNING
    if doc_type in _FULL_DOCS:
        return [
            ("seller_name", err),
            ("invoice_number", err),
            ("invoice_date", err),
            ("total_amount", err),
        ]
    if doc_type in _RECEIPT_DOCS:
        return [
            ("seller_name", err),
            ("total_amount", err),
            ("invoice_number", warn),
            ("invoice_date", warn),
        ]
    return [  # delivery_receipt
        ("seller_name", err),
        ("invoice_number", err),
        ("invoice_date", warn),
    ]


def check_required(inv: Invoice) -> list[Flag]:
    flags = []
    for name, severity in required_fields(inv.document_type):
        if getattr(inv, name) is None:
            flags.append(
                _flag("MISSING_REQUIRED", severity, name, f"Missing {name.replace('_', ' ')}.")
            )
    return flags


def total_candidates(inv: Invoice) -> list[Decimal] | None:
    """Every total the document may legitimately print, or None when there is nothing to check."""
    sales = [inv.vatable_sales, inv.vat_exempt_sales, inv.zero_rated_sales]
    bases: list[Decimal] = []
    if any(s is not None for s in sales):
        bases.append(sum((_v(s) for s in sales), ZERO) + _v(inv.vat_amount))
        if inv.subtotal is not None:
            bases.append(inv.subtotal)
    elif inv.subtotal is not None:
        bases.append(inv.subtotal + _v(inv.vat_amount))
        bases.append(inv.subtotal)
    else:
        return None
    candidates: list[Decimal] = []
    for base, drop_discount, drop_withholding in product(bases, (False, True), (False, True)):
        value = base
        if drop_discount:
            value -= _v(inv.discount)
        if drop_withholding:
            value -= _v(inv.withholding_tax)
        if value not in candidates:
            candidates.append(value)
    return candidates


def check_total(inv: Invoice, tol: Decimal) -> list[Flag]:
    if inv.total_amount is None:
        return []
    candidates = total_candidates(inv)
    if not candidates:
        return []
    if any(abs(inv.total_amount - c) <= tol for c in candidates):
        return []
    closest = min(candidates, key=lambda c: abs(inv.total_amount - c))
    return [
        _flag(
            "TOTAL_MISMATCH",
            Severity.ERROR,
            "total_amount",
            f"Computed {money_text(closest, inv.currency)} but document says "
            f"{money_text(inv.total_amount, inv.currency)}.",
        )
    ]


def check_vat(inv: Invoice, tol: Decimal, vat_rate: Decimal) -> list[Flag]:
    if inv.vatable_sales is None or inv.vat_amount is None:
        return []
    expected = inv.vatable_sales * vat_rate
    if abs(inv.vat_amount - expected) > tol:
        return [
            _flag(
                "VAT_MISMATCH",
                Severity.WARNING,
                "vat_amount",
                f"VAT is {money_text(inv.vat_amount, inv.currency)} but "
                f"{vat_rate * 100:.0f}% of VATable sales is {money_text(expected, inv.currency)}.",
            )
        ]
    return []


def check_line_items_sum(inv: Invoice, tol: Decimal) -> list[Flag]:
    amounts = [li.amount for li in inv.line_items if li.amount is not None]
    if not amounts:
        return []
    line_sum = sum(amounts, ZERO)
    references: list[Decimal] = []
    if inv.subtotal is not None:
        references.append(inv.subtotal)
    sales = [inv.vatable_sales, inv.vat_exempt_sales, inv.zero_rated_sales]
    if any(s is not None for s in sales):
        references.append(sum((_v(s) for s in sales), ZERO) + _v(inv.vat_amount))
    if inv.total_amount is not None:
        references.append(inv.total_amount + _v(inv.discount))
    if not references or any(abs(line_sum - r) <= tol for r in references):
        return []
    return [
        _flag(
            "LINE_ITEMS_MISMATCH",
            Severity.WARNING,
            "line_items",
            f"Line items add up to {money_text(line_sum, inv.currency)}, which does not match "
            f"the document figures ({money_text(references[0], inv.currency)}).",
        )
    ]


def check_line_item_math(inv: Invoice, tol: Decimal) -> list[Flag]:
    flags = []
    for index, li in enumerate(inv.line_items, start=1):
        if li.quantity is None or li.unit_price is None or li.amount is None:
            continue
        computed = li.quantity * li.unit_price
        if abs(computed - li.amount) > tol:
            flags.append(
                _flag(
                    "LINE_ITEM_MATH",
                    Severity.WARNING,
                    f"line_items[{index}]",
                    f"Line {index}: {li.quantity.normalize():f} x "
                    f"{money_text(li.unit_price, inv.currency)} is "
                    f"{money_text(computed, inv.currency)} but the line says "
                    f"{money_text(li.amount, inv.currency)}.",
                )
            )
    return flags


def tin_digits(tin: str | None) -> str:
    return re.sub(r"\D", "", tin or "")


def check_tin(inv: Invoice) -> list[Flag]:
    flags = []
    for name in ("seller_tin", "buyer_tin"):
        value = getattr(inv, name)
        if value is None:
            continue
        digits = len(tin_digits(value))
        if digits not in (9, 12, 14):
            flags.append(
                _flag(
                    "TIN_FORMAT",
                    Severity.WARNING,
                    name,
                    f"{name.replace('_', ' ').capitalize()} '{value}' has {digits} digits; "
                    "expected 9, 12 or 14.",
                )
            )
    return flags


def _five_years_before(today: date) -> date:
    try:
        return today.replace(year=today.year - 5)
    except ValueError:  # 29 February
        return today.replace(year=today.year - 5, day=28)


def check_dates(inv: Invoice, today: date) -> list[Flag]:
    flags = []
    if inv.invoice_date is not None:
        if inv.invoice_date > today:
            flags.append(
                _flag(
                    "FUTURE_DATE",
                    Severity.WARNING,
                    "invoice_date",
                    f"Invoice date {inv.invoice_date.isoformat()} is in the future.",
                )
            )
        if inv.invoice_date < _five_years_before(today):
            flags.append(
                _flag(
                    "OLD_DATE",
                    Severity.WARNING,
                    "invoice_date",
                    f"Invoice date {inv.invoice_date.isoformat()} is more than 5 years ago.",
                )
            )
    if inv.invoice_date and inv.due_date and inv.due_date < inv.invoice_date:
        flags.append(
            _flag(
                "DUE_BEFORE_ISSUE",
                Severity.WARNING,
                "due_date",
                f"Due date {inv.due_date.isoformat()} is before the invoice date "
                f"{inv.invoice_date.isoformat()}.",
            )
        )
    return flags


def check_negative(inv: Invoice) -> list[Flag]:
    flags = []
    for name in MONEY_FIELDS:
        value = getattr(inv, name)
        if value is not None and value < 0:
            flags.append(
                _flag(
                    "NEGATIVE_AMOUNT",
                    Severity.WARNING,
                    name,
                    f"{name.replace('_', ' ').capitalize()} is negative "
                    f"({money_text(value, inv.currency)}).",
                )
            )
    return flags


def check_confidence(inv: Invoice) -> list[Flag]:
    if not inv.uncertain_fields:
        return []
    return [
        _flag(
            "LOW_CONFIDENCE",
            Severity.WARNING,
            None,
            "The model was not confident about: " + ", ".join(inv.uncertain_fields) + ".",
        )
    ]


def check_complete(inv: Invoice) -> list[Flag]:
    if inv.is_complete:
        return []
    return [
        _flag(
            "INCOMPLETE_DOCUMENT",
            Severity.WARNING,
            None,
            "The document seems to continue past the pages that were read.",
        )
    ]


# --- batch rules ----------------------------------------------------------------------------


def normalize_number(number: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (number or "").lower())


def _name_key(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def same_seller(a: Invoice, b: Invoice) -> bool:
    tin_a, tin_b = tin_digits(a.seller_tin), tin_digits(b.seller_tin)
    if tin_a and tin_b:
        return tin_a == tin_b
    name_a, name_b = _name_key(a.seller_name), _name_key(b.seller_name)
    return bool(name_a) and name_a == name_b


def find_duplicates(invoices: list[Invoice]) -> dict[int, list[Flag]]:
    """``DUPLICATE`` and ``POSSIBLE_DUPLICATE`` flags by index into ``invoices``."""
    result: dict[int, list[Flag]] = {}
    for index, inv in enumerate(invoices):
        number = normalize_number(inv.invoice_number)
        duplicate_of: Invoice | None = None
        if number:
            for earlier in invoices[:index]:
                if normalize_number(earlier.invoice_number) == number and same_seller(earlier, inv):
                    duplicate_of = earlier
                    break
        if duplicate_of is not None:
            result.setdefault(index, []).append(
                _flag(
                    "DUPLICATE",
                    Severity.ERROR,
                    "invoice_number",
                    f"Same invoice number and seller as {duplicate_of.id}.",
                )
            )
            continue
        if inv.invoice_date is None or inv.total_amount is None:
            continue
        for earlier in invoices[:index]:
            if (
                same_seller(earlier, inv)
                and earlier.invoice_date == inv.invoice_date
                and earlier.total_amount == inv.total_amount
            ):
                result.setdefault(index, []).append(
                    _flag(
                        "POSSIBLE_DUPLICATE",
                        Severity.WARNING,
                        None,
                        f"Same seller, date and total as {earlier.id}.",
                    )
                )
                break
    return result


def derive_status(flags: list[Flag]) -> Status:
    if any(f.severity == Severity.ERROR for f in flags):
        return Status.ERROR
    if any(f.severity == Severity.WARNING for f in flags):
        return Status.REVIEW
    return Status.OK


def check_invoice(inv: Invoice, settings: Settings, today: date) -> list[Flag]:
    """All single-invoice flags, in rule order."""
    tol = settings.amount_tolerance
    return [
        *check_required(inv),
        *check_total(inv, tol),
        *check_vat(inv, tol, settings.vat_rate),
        *check_line_items_sum(inv, tol),
        *check_line_item_math(inv, tol),
        *check_tin(inv),
        *check_dates(inv, today),
        *check_negative(inv),
        *check_confidence(inv),
        *check_complete(inv),
    ]


def validate_invoices(invoices: list[Invoice], settings: Settings, today: date) -> list[Invoice]:
    """Return copies of ``invoices`` with flags and status set. Invoices need their ids already.
    A ``SPLIT_ACROSS_CHUNKS`` flag set earlier by the extractor is kept."""
    duplicates = find_duplicates(invoices)
    validated = []
    for index, inv in enumerate(invoices):
        kept = [f for f in inv.flags if f.code == SPLIT_ACROSS_CHUNKS]
        flags = [*kept, *check_invoice(inv, settings, today), *duplicates.get(index, [])]
        validated.append(inv.model_copy(update={"flags": flags, "status": derive_status(flags)}))
    return validated
