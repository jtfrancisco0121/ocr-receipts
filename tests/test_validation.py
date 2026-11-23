from datetime import UTC, date, datetime
from decimal import Decimal as D

from resibo.config import Settings
from resibo.models import DocumentType, Invoice, LineItem, Severity, Status
from resibo.validation import (
    check_invoice,
    derive_status,
    manila_today,
    validate_invoices,
)

TODAY = date(2026, 11, 23)
SETTINGS = Settings()


def inv(**kw) -> Invoice:
    base = dict(
        id="INV-001",
        document_type=DocumentType.SALES_INVOICE,
        seller_name="Mabuhay Supply",
        seller_tin="123-456-789-00000",
        invoice_number="SI-1",
        invoice_date=date(2026, 9, 4),
        vatable_sales=D("10000.00"),
        vat_amount=D("1200.00"),
        total_amount=D("11200.00"),
        source_pages=[1],
    )
    base.update(kw)
    return Invoice(**base)


def codes(invoice: Invoice, today: date = TODAY) -> list[str]:
    return [f.code for f in check_invoice(invoice, SETTINGS, today)]


def flag(invoice: Invoice, code: str):
    return next(f for f in check_invoice(invoice, SETTINGS, TODAY) if f.code == code)


def test_clean_invoice_has_no_flags():
    assert codes(inv()) == []


# MISSING_REQUIRED
def test_missing_required_error_for_sales_invoice():
    flags = check_invoice(inv(seller_name=None, invoice_number=None), SETTINGS, TODAY)
    missing = [f for f in flags if f.code == "MISSING_REQUIRED"]
    assert {f.field for f in missing} == {"seller_name", "invoice_number"}
    assert all(f.severity == Severity.ERROR for f in missing)


def test_missing_required_cash_receipt_levels():
    receipt = inv(
        document_type=DocumentType.CASH_RECEIPT,
        invoice_number=None,
        invoice_date=None,
        vatable_sales=None,
        vat_amount=None,
        total_amount=None,
    )
    levels = {f.field: f.severity for f in check_invoice(receipt, SETTINGS, TODAY)}
    assert levels["total_amount"] == Severity.ERROR
    assert levels["invoice_number"] == Severity.WARNING
    assert levels["invoice_date"] == Severity.WARNING


def test_delivery_receipt_does_not_require_total():
    d = inv(
        document_type=DocumentType.DELIVERY_RECEIPT,
        total_amount=None,
        vatable_sales=None,
        vat_amount=None,
        invoice_date=None,
    )
    flags = check_invoice(d, SETTINGS, TODAY)
    assert [(f.field, f.severity) for f in flags] == [("invoice_date", Severity.WARNING)]
    no_number = d.model_copy(update={"invoice_number": None})
    assert ("invoice_number", Severity.ERROR) in [
        (f.field, f.severity) for f in check_invoice(no_number, SETTINGS, TODAY)
    ]


# TOTAL_MISMATCH
def test_total_tolerance_edges():
    assert "TOTAL_MISMATCH" not in codes(inv(total_amount=D("11200.99")))
    assert "TOTAL_MISMATCH" not in codes(inv(total_amount=D("11201.00")))
    assert "TOTAL_MISMATCH" in codes(inv(total_amount=D("11201.01")))
    assert "TOTAL_MISMATCH" not in codes(inv(total_amount=D("11199.01")))
    assert "TOTAL_MISMATCH" in codes(inv(total_amount=D("11198.99")))


def test_total_mismatch_message_shows_closest_candidate():
    f = flag(inv(total_amount=D("11500.00")), "TOTAL_MISMATCH")
    assert f.severity == Severity.ERROR
    assert "₱11,200.00" in f.message and "₱11,500.00" in f.message


def test_total_accepts_discount_withholding_and_both():
    assert "TOTAL_MISMATCH" not in codes(inv(discount=D("500.00"), total_amount=D("10700.00")))
    assert "TOTAL_MISMATCH" not in codes(
        inv(withholding_tax=D("200.00"), total_amount=D("11000.00"))
    )
    assert "TOTAL_MISMATCH" not in codes(
        inv(discount=D("500.00"), withholding_tax=D("200.00"), total_amount=D("10500.00"))
    )


def test_total_discount_netted_out_of_vatable_sales_is_accepted():
    # VATable Sales printed net of the discount: vatable + VAT = total
    receipt = inv(
        vatable_sales=D("1000.00"),
        vat_amount=D("120.00"),
        discount=D("100.00"),
        total_amount=D("1120.00"),
    )
    assert "TOTAL_MISMATCH" not in codes(receipt)


def test_total_uses_exempt_and_zero_rated():
    receipt = inv(
        vatable_sales=D("1000.00"),
        vat_amount=D("120.00"),
        vat_exempt_sales=D("200.00"),
        zero_rated_sales=D("50.00"),
        total_amount=D("1370.00"),
    )
    assert "TOTAL_MISMATCH" not in codes(receipt)


def test_total_with_subtotal_only_and_vat_inclusive_subtotal():
    exclusive = inv(
        vatable_sales=None, subtotal=D("1000.00"), vat_amount=D("120.00"), total_amount=D("1120.00")
    )
    assert "TOTAL_MISMATCH" not in codes(exclusive)
    # "Total Sales (VAT Inclusive)" lands in subtotal and equals the total
    inclusive = inv(
        vatable_sales=D("1000.00"),
        vat_amount=D("120.00"),
        subtotal=D("1120.00"),
        total_amount=D("1120.00"),
    )
    assert "TOTAL_MISMATCH" not in codes(inclusive)
    assert "TOTAL_MISMATCH" in codes(exclusive.model_copy(update={"total_amount": D("1500.00")}))


def test_total_skipped_without_any_base():
    bare = inv(vatable_sales=None, vat_amount=None, total_amount=D("999.00"))
    assert "TOTAL_MISMATCH" not in codes(bare)


# VAT_MISMATCH
def test_vat_mismatch():
    assert "VAT_MISMATCH" not in codes(inv())
    assert "VAT_MISMATCH" not in codes(inv(vat_amount=D("1200.99"), total_amount=D("11200.99")))
    wrong = inv(vat_amount=D("1000.00"), total_amount=D("11000.00"))
    assert flag(wrong, "VAT_MISMATCH").severity == Severity.WARNING
    assert "VAT_MISMATCH" not in codes(inv(vat_amount=None, total_amount=D("10000.00")))


# LINE_ITEMS_MISMATCH
def items(*amounts) -> list[LineItem]:
    return [LineItem(description=f"item {i}", amount=D(a)) for i, a in enumerate(amounts)]


def test_line_items_match_total_or_vat_inclusive_sum():
    assert "LINE_ITEMS_MISMATCH" not in codes(inv(line_items=items("6000.00", "5200.00")))


def test_line_items_match_subtotal_and_total_plus_discount():
    with_sub = inv(subtotal=D("10000.00"), line_items=items("4000.00", "6000.00"))
    assert "LINE_ITEMS_MISMATCH" not in codes(with_sub)
    discounted = inv(
        discount=D("500.00"),
        total_amount=D("10700.00"),
        vatable_sales=None,
        vat_amount=None,
        line_items=items("5600.00", "5600.00"),
    )
    assert "LINE_ITEMS_MISMATCH" not in codes(discounted)


def test_line_items_mismatch_flagged():
    f = flag(inv(line_items=items("100.00", "200.00")), "LINE_ITEMS_MISMATCH")
    assert f.severity == Severity.WARNING
    assert "LINE_ITEMS_MISMATCH" not in codes(inv(line_items=[]))
    assert "LINE_ITEMS_MISMATCH" not in codes(
        inv(line_items=[LineItem(description="x", amount=None)])
    )


# LINE_ITEM_MATH
def test_line_item_math():
    ok = LineItem(description="a", quantity=D("2"), unit_price=D("50.00"), amount=D("100.50"))
    bad = LineItem(description="b", quantity=D("2"), unit_price=D("50.00"), amount=D("103.00"))
    only_amount = LineItem(description="c", amount=D("5.00"))
    base = dict(vatable_sales=None, vat_amount=None, total_amount=None, subtotal=None)
    assert "LINE_ITEM_MATH" not in codes(inv(line_items=[ok, only_amount], **base))
    flagged = [
        f
        for f in check_invoice(inv(line_items=[ok, bad], **base), SETTINGS, TODAY)
        if f.code == "LINE_ITEM_MATH"
    ]
    assert len(flagged) == 1 and flagged[0].field == "line_items[2]"


# DUPLICATE / POSSIBLE_DUPLICATE
def two(a: Invoice, b: Invoice) -> list[Invoice]:
    a = a.model_copy(update={"id": "INV-001"})
    b = b.model_copy(update={"id": "INV-002"})
    return validate_invoices([a, b], SETTINGS, TODAY)


def flag_codes(invoice: Invoice) -> list[str]:
    return [f.code for f in invoice.flags]


def test_duplicate_by_tin_even_with_different_seller_spelling():
    first, second = two(inv(), inv(seller_name="MABUHAY SUPPLY CORP", invoice_number="si 1"))
    assert "DUPLICATE" not in flag_codes(first)
    assert "DUPLICATE" in flag_codes(second)
    assert "INV-001" in next(f for f in second.flags if f.code == "DUPLICATE").message
    assert second.status == Status.ERROR


def test_duplicate_by_name_when_no_tin():
    a = inv(seller_tin=None)
    b = inv(seller_tin=None, seller_name="  mabuhay   SUPPLY ")
    assert "DUPLICATE" in flag_codes(two(a, b)[1])


def test_not_duplicate_for_different_seller_or_number():
    other_tin = inv(seller_tin="999-999-999-00000", seller_name="Other")
    assert "DUPLICATE" not in flag_codes(two(inv(), other_tin)[1])
    assert "DUPLICATE" not in flag_codes(two(inv(), inv(invoice_number="SI-2"))[1])


def test_possible_duplicate_and_suppression():
    same_figures = inv(invoice_number="SI-2")
    assert "POSSIBLE_DUPLICATE" in flag_codes(two(inv(), same_figures)[1])
    missing_number = inv(invoice_number=None)
    assert "POSSIBLE_DUPLICATE" in flag_codes(two(inv(), missing_number)[1])
    # suppressed when DUPLICATE fired
    dup = two(inv(), inv())[1]
    assert "DUPLICATE" in flag_codes(dup) and "POSSIBLE_DUPLICATE" not in flag_codes(dup)
    # different total: not flagged
    other_total = inv(
        invoice_number="SI-2", total_amount=D("1.00"), vat_amount=D("0.12"), vatable_sales=D("1.00")
    )
    assert "POSSIBLE_DUPLICATE" not in flag_codes(two(inv(), other_total)[1])


# TIN_FORMAT
def test_tin_format():
    for good in ("123-456-789", "123-456-789-000", "123-456-789-00000"):
        assert "TIN_FORMAT" not in codes(inv(seller_tin=good))
    assert "TIN_FORMAT" in codes(inv(seller_tin="12-34"))
    assert flag(inv(buyer_tin="123"), "TIN_FORMAT").field == "buyer_tin"
    assert "TIN_FORMAT" not in codes(inv(seller_tin=None))


# dates
def test_future_and_old_dates():
    assert "FUTURE_DATE" in codes(inv(invoice_date=date(2026, 11, 24)))
    assert "FUTURE_DATE" not in codes(inv(invoice_date=TODAY))
    assert "OLD_DATE" in codes(inv(invoice_date=date(2021, 11, 22)))
    assert "OLD_DATE" not in codes(inv(invoice_date=date(2021, 11, 23)))
    assert "OLD_DATE" not in codes(inv())


def test_due_before_issue():
    assert "DUE_BEFORE_ISSUE" in codes(inv(due_date=date(2026, 9, 1)))
    assert "DUE_BEFORE_ISSUE" not in codes(inv(due_date=date(2026, 10, 4)))
    assert "DUE_BEFORE_ISSUE" not in codes(inv(due_date=date(2026, 9, 4)))


def test_old_date_on_leap_day_today():
    assert "OLD_DATE" in codes(inv(invoice_date=date(2019, 1, 1)), today=date(2028, 2, 29))


def test_manila_today_uses_asia_manila():
    assert manila_today(datetime(2026, 11, 22, 17, 0, tzinfo=UTC)) == date(2026, 11, 23)
    assert manila_today(datetime(2026, 11, 22, 15, 59, tzinfo=UTC)) == date(2026, 11, 22)


# NEGATIVE_AMOUNT
def test_negative_amount():
    f = flag(inv(discount=D("-5.00"), total_amount=D("11205.00")), "NEGATIVE_AMOUNT")
    assert f.field == "discount"
    assert "NEGATIVE_AMOUNT" not in codes(inv())


# LOW_CONFIDENCE / INCOMPLETE_DOCUMENT
def test_low_confidence_one_flag_listing_fields():
    flags = [
        f
        for f in check_invoice(
            inv(uncertain_fields=["total_amount", "seller_name"]), SETTINGS, TODAY
        )
        if f.code == "LOW_CONFIDENCE"
    ]
    assert len(flags) == 1
    assert "total_amount" in flags[0].message and "seller_name" in flags[0].message
    assert "LOW_CONFIDENCE" not in codes(inv())


def test_incomplete_document():
    assert "INCOMPLETE_DOCUMENT" in codes(inv(is_complete=False))
    assert "INCOMPLETE_DOCUMENT" not in codes(inv())


# status and extractor-provided flag
def test_status_derivation():
    assert validate_invoices([inv()], SETTINGS, TODAY)[0].status == Status.OK
    warn = validate_invoices([inv(is_complete=False)], SETTINGS, TODAY)[0]
    assert warn.status == Status.REVIEW
    err = validate_invoices([inv(total_amount=D("1.00"), is_complete=False)], SETTINGS, TODAY)[0]
    assert err.status == Status.ERROR
    assert derive_status([]) == Status.OK


def test_split_flag_from_extractor_is_kept():
    from resibo.models import Flag

    split = Flag(code="SPLIT_ACROSS_CHUNKS", severity=Severity.WARNING, message="split")
    out = validate_invoices([inv(flags=[split])], SETTINGS, TODAY)[0]
    assert flag_codes(out) == ["SPLIT_ACROSS_CHUNKS"]
    assert out.status == Status.REVIEW


def test_validation_does_not_read_the_clock():
    import inspect

    import resibo.validation as v

    source = inspect.getsource(v)
    assert "datetime.now" not in source and "date.today" not in source
    assert "time.time" not in source
