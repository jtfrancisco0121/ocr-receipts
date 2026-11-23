"""Build samples/sample-invoices.pdf and samples/expected.json (SPEC.md section 14).

Fictional businesses and fake TINs only. The output is deterministic: reportlab runs with
``invariant=1``, dates are offsets from a fixed anchor date, and the random generator is seeded.

This script never reads or writes samples/sample-result.json: that file comes only from a real
``--record`` run.

    python scripts/make_samples.py [--out samples]
"""

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

ANCHOR = date(2026, 11, 23)  # the build date these samples are written against
SEED = 20261123
PAGE_W, PAGE_H = A4
TWO = Decimal("0.01")


# --- documents ------------------------------------------------------------------------------


@dataclass
class Item:
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    amount: Decimal


@dataclass
class Doc:
    label: str
    pages: list[int]
    document_type: str
    title: str
    seller_name: str
    seller_address: str
    number: str
    day: date
    items: list[Item]
    total: Decimal
    seller_tin: str | None = None
    buyer_name: str | None = None
    buyer_tin: str | None = None
    buyer_address: str | None = None
    due: date | None = None
    terms: str | None = None
    vatable: Decimal | None = None
    vat: Decimal | None = None
    discount: Decimal | None = None
    status: str = "OK"
    status_optional: bool = False
    required_flags: list[str] = field(default_factory=list)
    optional_flags: list[str] = field(default_factory=list)

    @property
    def has_vat_block(self) -> bool:
        return self.vatable is not None


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"  # the month is a word, so the date is never ambiguous


def item(desc: str, qty: int, unit: str, price: str) -> Item:
    q, p = Decimal(qty), Decimal(price)
    return Item(desc, q, unit, p, (q * p).quantize(TWO))


def vat_split(vat_inclusive: Decimal) -> tuple[Decimal, Decimal]:
    vatable = (vat_inclusive / Decimal("1.12")).quantize(TWO, rounding=ROUND_HALF_UP)
    return vatable, vat_inclusive - vatable


BUYER = dict(
    buyer_name="Sampaguita Trading Corporation",
    buyer_tin="205-318-774-00000",
    buyer_address="45 Katipunan Ave., Quezon City 1108",
)

SERVICE_ROWS = [
    ("Network cabling, Floor 2", "1000.00"),
    ("Network cabling, Floor 3", "1000.00"),
    ("Wi-Fi access point installation", "1200.00"),
    ("Firewall configuration", "1500.00"),
    ("Server rack setup", "1200.00"),
    ("Backup system setup", "1500.00"),
    ("Email migration", "800.00"),
    ("User account provisioning", "800.00"),
    ("Workstation imaging", "1000.00"),
    ("Printer deployment", "800.00"),
    ("VPN configuration", "1500.00"),
    ("Security patching", "1200.00"),
    ("Antivirus rollout", "1000.00"),
    ("Staff orientation, IT policy", "1200.00"),
    ("Helpdesk support, week 1", "800.00"),
    ("Helpdesk support, week 2", "800.00"),
    ("Documentation and handover", "1000.00"),
    ("Project management", "1500.00"),
]


def build_documents(anchor: date) -> list[Doc]:
    rng = random.Random(SEED)
    ago = lambda days: anchor - timedelta(days=days)  # noqa: E731

    doc1_items = [
        item("Bond paper A4, 70gsm", 20, "ream", "280.00"),
        item("Ballpen, black, box of 12", 10, "box", "120.00"),
        item("Folder, long, assorted", 100, "pcs", "22.00"),
        item("Printer ink cartridge", 4, "pcs", "550.00"),
    ]
    doc1_total = sum((i.amount for i in doc1_items), Decimal(0))
    doc1_vatable, doc1_vat = vat_split(doc1_total)
    doc1 = Doc(
        label="1",
        pages=[1],
        document_type="sales_invoice",
        title="SALES INVOICE",
        seller_name="Mabuhay Office Supply Co.",
        seller_tin="201-555-014-00000",
        seller_address="123 Mabini St., Brgy. San Antonio, Pasig City 1605",
        number="SI-004217",
        day=ago(80),
        items=doc1_items,
        vatable=doc1_vatable,
        vat=doc1_vat,
        total=doc1_total,
        **BUYER,
    )

    service_items = []
    for desc, rate in SERVICE_ROWS:
        service_items.append(item(desc, rng.randint(2, 12), "hrs", rate))
    doc2_total = sum((i.amount for i in service_items), Decimal(0))
    doc2_vatable, doc2_vat = vat_split(doc2_total)
    doc2 = Doc(
        label="2",
        pages=[2, 3],
        document_type="service_invoice",
        title="SERVICE INVOICE",
        seller_name="Pinoy Tech Solutions Inc.",
        seller_tin="204-777-310-00000",
        seller_address="8F Aurora Tower, Cubao, Quezon City 1109",
        number="SV-001893",
        day=ago(39),
        due=ago(39) + timedelta(days=30),
        terms="Net 30",
        items=service_items,
        vatable=doc2_vatable,
        vat=doc2_vat,
        total=doc2_total,
        **BUYER,
    )

    gross = Decimal("1500.00")
    discount = Decimal("150.00")
    net = gross - discount
    doc3_vatable, doc3_vat = vat_split(net)  # VATable Sales is printed net of the discount
    doc3 = Doc(
        label="3",
        pages=[4],
        document_type="official_receipt",
        title="OFFICIAL RECEIPT",
        seller_name="Ngiti Dental Clinic",
        seller_tin="207-418-662-00000",
        seller_address="27 Rizal St., Brgy. Poblacion, Makati City 1210",
        number="OR-000731",
        day=ago(27),
        items=[
            item("Dental cleaning (prophylaxis)", 1, "svc", "1000.00"),
            item("Fluoride treatment", 1, "svc", "500.00"),
        ],
        vatable=doc3_vatable,
        vat=doc3_vat,
        discount=discount,
        total=net,
        buyer_name="Maria Dela Cruz",
    )

    doc4a = Doc(
        label="4a",
        pages=[5],
        document_type="cash_receipt",
        title="CASH RECEIPT",
        seller_name="Aling Nena's Sari-Sari Store",
        seller_address="Purok 3, Brgy. Maligaya, Antipolo City 1870",
        number="000457",
        day=ago(21),
        items=[
            item("Rice, 5 kg", 2, "sack", "245.00"),
            item("Cooking oil, 1 L", 1, "btl", "110.00"),
            item("Sugar, 1 kg", 2, "pack", "85.00"),
        ],
        total=Decimal("770.00"),
    )
    doc4b = Doc(
        label="4b",
        pages=[5],
        document_type="cash_receipt",
        title="CASH RECEIPT",
        seller_name="Kuya Jun's Hardware",
        seller_address="Km 18 Marcos Highway, Cainta, Rizal 1900",
        number="001123",
        day=ago(14),
        items=[
            item("Concrete nails, 3 in", 2, "kg", "95.00"),
            item("Paint brush, 4 in", 3, "pcs", "120.00"),
            item("Masking tape", 4, "roll", "52.50"),
        ],
        total=Decimal("760.00"),
    )

    doc5_items = [
        item("Laptop bag, 15 in", 4, "pcs", "850.00"),
        item("Wireless mouse", 8, "pcs", "320.00"),
        item("USB-C hub", 4, "pcs", "750.00"),
    ]
    doc5_sum = sum((i.amount for i in doc5_items), Decimal(0))
    doc5_vatable, doc5_vat = vat_split(doc5_sum)
    doc5 = Doc(
        label="5",
        pages=[6],
        document_type="sales_invoice",
        title="SALES INVOICE",
        seller_name="Datu Computer Mart",
        seller_tin="209-632-155-00000",
        seller_address="Unit 12, Gilmore IT Center, San Juan City 1500",
        number="SI-009120",
        day=ago(49),
        items=doc5_items,
        vatable=doc5_vatable,
        vat=doc5_vat,
        total=doc5_sum + Decimal("300.00"),  # printed total is P300 higher than the math
        status="ERROR",
        required_flags=["TOTAL_MISMATCH"],
        **BUYER,
    )

    doc6 = Doc(
        label="6",
        pages=[7],
        document_type=doc1.document_type,
        title=doc1.title,
        seller_name=doc1.seller_name,
        seller_tin=doc1.seller_tin,
        seller_address=doc1.seller_address,
        number=doc1.number,
        day=doc1.day,
        items=doc1.items,
        vatable=doc1.vatable,
        vat=doc1.vat,
        total=doc1.total,
        status="ERROR",
        required_flags=["DUPLICATE"],
        buyer_name=doc1.buyer_name,
        buyer_tin=doc1.buyer_tin,
        buyer_address=doc1.buyer_address,
    )

    doc7_items = [
        item("Portland cement, 40 kg", 6, "bag", "280.00"),
        item("Hollow blocks, 4 in", 100, "pcs", "8.40"),
        item("GI wire, #16", 8, "kg", "105.00"),
    ]
    doc7_total = sum((i.amount for i in doc7_items), Decimal(0))
    doc7_vatable, doc7_vat = vat_split(doc7_total)
    doc7 = Doc(
        label="7",
        pages=[8],
        document_type="sales_invoice",
        title="SALES INVOICE",
        seller_name="Bayanihan Hardware Trading",
        seller_tin="203-904-218-00000",
        seller_address="55 Quirino Highway, Novaliches, Quezon City 1123",
        number="SI-002764",
        day=ago(94),
        items=doc7_items,
        vatable=doc7_vatable,
        vat=doc7_vat,
        total=doc7_total,
        status="REVIEW",
        status_optional=True,
        optional_flags=["LOW_CONFIDENCE"],
        **BUYER,
    )
    return [doc1, doc2, doc3, doc4a, doc4b, doc5, doc6, doc7]


# --- drawing backends -----------------------------------------------------------------------


class PdfCanvas:
    """Draws in PDF points, origin bottom-left."""

    def __init__(self, c: rl_canvas.Canvas):
        self.c = c

    def text(self, x, y, s, size=10, bold=False, align="l"):
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        draw = {"l": self.c.drawString, "r": self.c.drawRightString, "c": self.c.drawCentredString}
        draw[align](x, y, s)

    def line(self, x1, y1, x2, y2, width=0.6):
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def rect(self, x, y, w, h, width=0.6):
        self.c.setLineWidth(width)
        self.c.rect(x, y, w, h)


class ImageCanvas:
    """The same drawing calls onto a Pillow image, for the 'scanned' page."""

    SCALE = 150 / 72  # 150 dpi

    def __init__(self):
        self.w, self.h = int(PAGE_W * self.SCALE), int(PAGE_H * self.SCALE)
        self.img = Image.new("RGB", (self.w, self.h), "white")
        self.d = ImageDraw.Draw(self.img)

    def _xy(self, x, y):
        return x * self.SCALE, (PAGE_H - y) * self.SCALE

    def text(self, x, y, s, size=10, bold=False, align="l"):
        font = ImageFont.load_default(size=int(size * self.SCALE * 1.05))
        anchor = {"l": "ls", "r": "rs", "c": "ms"}[align]
        px, py = self._xy(x, y)
        self.d.text((px, py), s, fill="black", font=font, anchor=anchor)
        if bold:
            self.d.text((px + 1, py), s, fill="black", font=font, anchor=anchor)

    def line(self, x1, y1, x2, y2, width=0.6):
        self.d.line(
            [self._xy(x1, y1), self._xy(x2, y2)], fill="black", width=max(1, int(width * 2))
        )

    def rect(self, x, y, w, h, width=0.6):
        x0, y0 = self._xy(x, y + h)
        x1, y1 = self._xy(x + w, y)
        self.d.rectangle([x0, y0, x1, y1], outline="black", width=max(1, int(width * 2)))


# --- layout ---------------------------------------------------------------------------------

LEFT, RIGHT = 50, PAGE_W - 50
COLS = {"desc": 54, "qty": 330, "unit": 345, "price": 450, "amount": RIGHT - 4}
ROW_H = 18


def draw_header(r, d: Doc, y: float, continued: str | None) -> float:
    r.text(PAGE_W / 2, y, d.seller_name, 16, bold=True, align="c")
    y -= 16
    if d.seller_tin:
        r.text(PAGE_W / 2, y, f"VAT Reg. TIN: {d.seller_tin}", 10, align="c")
        y -= 13
    r.text(PAGE_W / 2, y, d.seller_address, 10, align="c")
    y -= 30
    r.text(PAGE_W / 2, y, d.title, 15, bold=True, align="c")
    r.text(RIGHT, y, f"No. {d.number}", 11, bold=True, align="r")
    y -= 16
    r.text(RIGHT, y, f"Date: {long_date(d.day)}", 10, align="r")
    if continued:
        r.text(LEFT, y, continued, 10, bold=True)
    y -= 14
    if d.due:
        r.text(RIGHT, y, f"Due Date: {long_date(d.due)}", 10, align="r")
    if d.terms:
        r.text(RIGHT, y - 13, f"Terms: {d.terms}", 10, align="r")
    if d.buyer_name and not continued:
        r.text(LEFT, y, f"Sold to: {d.buyer_name}", 10)
        if d.buyer_tin:
            r.text(LEFT, y - 13, f"TIN: {d.buyer_tin}", 10)
        if d.buyer_address:
            r.text(LEFT, y - 26, f"Address: {d.buyer_address}", 10)
        y -= 26
    return y - 24


def draw_items(r, items: list[Item], y: float) -> float:
    top = y
    r.text(COLS["desc"], y - 13, "Description", 10, bold=True)
    r.text(COLS["qty"], y - 13, "Qty", 10, bold=True, align="r")
    r.text(COLS["unit"], y - 13, "Unit", 10, bold=True)
    r.text(COLS["price"], y - 13, "Unit Price", 10, bold=True, align="r")
    r.text(COLS["amount"], y - 13, "Amount (PHP)", 10, bold=True, align="r")
    y -= ROW_H
    r.line(LEFT, y, RIGHT, y)
    for it in items:
        y -= ROW_H
        r.text(COLS["desc"], y + 5, it.description, 10)
        r.text(COLS["qty"], y + 5, f"{it.quantity.normalize():f}", 10, align="r")
        r.text(COLS["unit"], y + 5, it.unit, 10)
        r.text(COLS["price"], y + 5, money(it.unit_price), 10, align="r")
        r.text(COLS["amount"], y + 5, money(it.amount), 10, align="r")
    r.rect(LEFT, y, RIGHT - LEFT, top - y)
    return y - 12


def draw_totals(r, d: Doc, y: float) -> float:
    zero = Decimal("0.00")
    rows: list[tuple[str, str, bool]] = []
    if d.has_vat_block:
        rows += [
            ("VATable Sales", money(d.vatable), False),
            ("VAT-Exempt Sales", money(zero), False),
            ("Zero-Rated Sales", money(zero), False),
        ]
        if d.discount is not None:
            rows.append(("Less: Discount", money(d.discount), False))
        rows.append(("VAT Amount (12%)", money(d.vat), False))
    rows.append(("Total Amount Due", money(d.total), True))
    for label, value, bold in rows:
        r.text(430, y, label, 10, bold=bold, align="r")
        r.text(RIGHT - 4, y, value, 10, bold=bold, align="r")
        y -= 15
    return y


def draw_invoice(r, d: Doc, items: list[Item], *, continued=None, totals=True, page_note=None):
    y = draw_header(r, d, PAGE_H - 70, continued)
    y = draw_items(r, items, y)
    if totals:
        y = draw_totals(r, d, y - 4)
    if page_note:
        r.text(PAGE_W / 2, 40, page_note, 9, align="c")
    return y


def draw_receipt(r, d: Doc, top: float):
    height = 220
    r.rect(LEFT + 30, top - height, RIGHT - LEFT - 60, height, width=1)
    cx = PAGE_W / 2
    y = top - 28
    r.text(cx, y, d.seller_name, 14, bold=True, align="c")
    r.text(cx, y - 14, d.seller_address, 9, align="c")
    r.text(cx, y - 40, d.title, 12, bold=True, align="c")
    r.text(LEFT + 45, y - 62, f"Receipt No. {d.number}", 10)
    r.text(RIGHT - 45, y - 62, long_date(d.day), 10, align="r")
    row = y - 90
    for it in d.items:
        r.text(LEFT + 45, row, f"{it.quantity.normalize():f} {it.unit}  {it.description}", 10)
        r.text(RIGHT - 45, row, money(it.amount), 10, align="r")
        row -= 16
    r.line(LEFT + 45, row + 6, RIGHT - 45, row + 6)
    r.text(LEFT + 45, row - 12, "TOTAL (cash)", 11, bold=True)
    r.text(RIGHT - 45, row - 12, money(d.total), 11, bold=True, align="r")
    r.text(cx, top - height + 18, "Thank you! Walang palitan ang walang resibo.", 9, align="c")


def scanned_page(d: Doc) -> Image.Image:
    """Page 8: render, rotate about 2 degrees, lower the contrast, stamp over the total."""
    canvas = ImageCanvas()
    draw_invoice(canvas, d, d.items)
    img = canvas.img
    # a stamp near the "Total Amount Due" figure (found by re-running the layout positions)
    stamp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    total_y = (PAGE_H - total_row_y(d)) * ImageCanvas.SCALE
    x0, y0 = int((RIGHT - 4) * ImageCanvas.SCALE) - 250, int(total_y - 55)
    font = ImageFont.load_default(size=44)
    sd.rounded_rectangle(
        [x0, y0, x0 + 300, y0 + 80], radius=10, outline=(185, 40, 40, 215), width=5
    )
    sd.text((x0 + 150, y0 + 42), "PAID", fill=(185, 40, 40, 215), font=font, anchor="mm")
    stamp = stamp.rotate(-8, resample=Image.BICUBIC)
    img = Image.alpha_composite(img.convert("RGBA"), stamp).convert("RGB")
    img = img.rotate(2, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    img = ImageEnhance.Contrast(img).enhance(0.62)
    img = ImageEnhance.Brightness(img).enhance(0.96)
    return img.filter(ImageFilter.GaussianBlur(0.7))


def total_row_y(d: Doc) -> float:
    """The y (PDF points) of the 'Total Amount Due' row, by replaying the layout arithmetic."""

    class Probe:
        last: float = 0.0

        def text(self, x, y, s, size=10, bold=False, align="l"):
            if s == "Total Amount Due":
                Probe.last = y

        def line(self, *a, **k):
            pass

        def rect(self, *a, **k):
            pass

    draw_invoice(Probe(), d, d.items)
    return Probe.last


# --- output ---------------------------------------------------------------------------------


def expected_entry(d: Doc) -> dict:
    fields: dict = {
        "document_type": d.document_type,
        "seller_name": d.seller_name,
        "seller_tin": d.seller_tin,
        "seller_address": d.seller_address,
        "buyer_name": d.buyer_name,
        "buyer_tin": d.buyer_tin,
        "buyer_address": d.buyer_address,
        "invoice_number": d.number,
        "invoice_date": d.day.isoformat(),
        "due_date": d.due.isoformat() if d.due else None,
        "payment_terms": d.terms,
        "currency": "PHP",
    }
    if d.has_vat_block:
        fields.update(
            vatable_sales=float(d.vatable),
            vat_exempt_sales=0.0,
            zero_rated_sales=0.0,
            vat_amount=float(d.vat),
            discount=float(d.discount) if d.discount is not None else None,
        )
    fields["total_amount"] = float(d.total)
    return {
        "label": d.label,
        "source_pages": d.pages,
        "expected_status": d.status,
        "status_optional": d.status_optional,
        "required_flags": d.required_flags,
        "optional_flags": d.optional_flags,
        "fields": {k: v for k, v in fields.items() if v is not None},
        "line_items": [
            {
                "description": i.description,
                "quantity": float(i.quantity),
                "unit": i.unit,
                "unit_price": float(i.unit_price),
                "amount": float(i.amount),
            }
            for i in d.items
        ],
    }


def build(out_dir: Path, anchor: date = ANCHOR) -> tuple[Path, Path]:
    """Write sample-invoices.pdf and expected.json into ``out_dir`` (nothing else)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    docs = {d.label: d for d in build_documents(anchor)}
    pdf_path = out_dir / "sample-invoices.pdf"

    c = rl_canvas.Canvas(str(pdf_path), pagesize=A4, invariant=1, pageCompression=0)
    c.setTitle("Resibo sample invoices")
    c.setAuthor("Resibo sample generator")
    r = PdfCanvas(c)

    draw_invoice(r, docs["1"], docs["1"].items)
    c.showPage()

    d2 = docs["2"]
    draw_invoice(r, d2, d2.items[:10], totals=False, page_note="Page 1 of 2, continued overleaf")
    c.showPage()
    draw_invoice(
        r,
        d2,
        d2.items[10:],
        continued=f"(continued from the previous page) Invoice {d2.number}",
        page_note="Page 2 of 2",
    )
    c.showPage()

    draw_invoice(r, docs["3"], docs["3"].items)
    c.showPage()

    draw_receipt(r, docs["4a"], PAGE_H - 60)
    draw_receipt(r, docs["4b"], PAGE_H - 60 - 260)
    c.showPage()

    draw_invoice(r, docs["5"], docs["5"].items)
    c.showPage()
    draw_invoice(r, docs["6"], docs["6"].items)
    c.showPage()

    buffer = BytesIO()
    scanned_page(docs["7"]).save(buffer, format="PNG")
    buffer.seek(0)
    c.drawImage(ImageReader(buffer), 0, 0, width=PAGE_W, height=PAGE_H)
    c.showPage()
    c.save()

    expected = {
        "format_version": 1,
        "anchor_date": anchor.isoformat(),
        "seed": SEED,
        "page_count": 8,
        "documents": [expected_entry(d) for d in docs.values()],
    }
    expected_path = out_dir / "expected.json"
    expected_path.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return pdf_path, expected_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_out = Path(__file__).resolve().parent.parent / "samples"
    parser.add_argument("--out", type=Path, default=default_out, help="output folder")
    args = parser.parse_args()
    pdf_path, expected_path = build(args.out)
    print(f"Wrote {pdf_path} and {expected_path}")


if __name__ == "__main__":
    main()
