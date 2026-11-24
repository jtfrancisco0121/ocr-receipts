import importlib.util
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from resibo.config import Settings
from resibo.models import Invoice, LineItem
from resibo.validation import validate_invoices

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="module")
def make_samples():
    spec = importlib.util.spec_from_file_location(
        "resibo_make_samples", ROOT / "scripts" / "make_samples.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(make_samples, tmp_path_factory):
    first = tmp_path_factory.mktemp("first")
    second = tmp_path_factory.mktemp("second")
    make_samples.build(first)
    make_samples.build(second)
    return first, second


def page_texts(path: Path) -> list[str]:
    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def test_generator_writes_only_the_pdf_and_expected_json(built):
    first, _ = built
    assert sorted(p.name for p in first.iterdir()) == ["expected.json", "sample-invoices.pdf"]
    assert not (first / "sample-result.json").exists()


def test_running_twice_is_identical(built):
    first, second = built
    assert (first / "expected.json").read_bytes() == (second / "expected.json").read_bytes()
    assert len(PdfReader(str(first / "sample-invoices.pdf")).pages) == 8
    assert page_texts(first / "sample-invoices.pdf") == page_texts(second / "sample-invoices.pdf")


def test_committed_files_match_a_fresh_run(built):
    first, _ = built
    assert (SAMPLES / "expected.json").read_bytes() == (first / "expected.json").read_bytes()
    assert page_texts(SAMPLES / "sample-invoices.pdf") == page_texts(first / "sample-invoices.pdf")
    assert len(PdfReader(str(SAMPLES / "sample-invoices.pdf")).pages) == 8


def test_only_the_pdf_expected_json_and_recording_are_committed():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "samples/"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    # the recording from a real live run was committed on purpose (43dc651); nothing else may be
    assert sorted(tracked) == [
        "samples/expected.json",
        "samples/sample-invoices.pdf",
        "samples/sample-result.json",
    ]


def expected():
    return json.loads((SAMPLES / "expected.json").read_text())


def test_expected_json_shape_v1():
    data = expected()
    assert data["format_version"] == 1 and data["page_count"] == 8
    assert data["anchor_date"] == "2026-11-23" and isinstance(data["seed"], int)
    docs = data["documents"]
    assert [d["label"] for d in docs] == ["1", "2", "3", "4a", "4b", "5", "6", "7"]
    assert [d["source_pages"] for d in docs] == [[1], [2, 3], [4], [5], [5], [6], [7], [8]]
    assert [d["status_optional"] for d in docs] == [False] * 7 + [True]
    assert docs[5]["required_flags"] == ["TOTAL_MISMATCH"]
    assert docs[6]["required_flags"] == ["DUPLICATE"]
    assert docs[7]["optional_flags"] == ["LOW_CONFIDENCE"] and docs[7]["required_flags"] == []
    assert len(docs[1]["line_items"]) == 18 and len(docs[0]["line_items"]) == 4


def test_dates_are_unambiguous_recent_and_not_in_the_future():
    anchor = date(2026, 11, 23)
    for doc in expected()["documents"]:
        day = date.fromisoformat(doc["fields"]["invoice_date"])
        assert anchor - timedelta(days=183) <= day <= anchor
    text = " ".join(page_texts(SAMPLES / "sample-invoices.pdf"))
    for month in ("September 4, 2026", "October 15, 2026", "October 27, 2026", "November 2, 2026",
                  "November 9, 2026", "October 5, 2026"):  # fmt: skip
        assert month in text


def test_ground_truth_agrees_with_the_validation_rules():
    """The documents are built so that the rules flag exactly what expected.json promises."""
    settings = Settings()
    invoices = []
    for number, doc in enumerate(expected()["documents"], start=1):
        f = doc["fields"]
        money_fields = (
            "vatable_sales",
            "vat_exempt_sales",
            "zero_rated_sales",
            "vat_amount",
            "discount",
            "total_amount",
        )
        money = {k: Decimal(str(f[k])) for k in money_fields if k in f}
        items = [
            LineItem(
                description=i["description"],
                quantity=Decimal(str(i["quantity"])),
                unit=i["unit"],
                unit_price=Decimal(str(i["unit_price"])),
                amount=Decimal(str(i["amount"])),
            )
            for i in doc["line_items"]
        ]
        invoices.append(
            Invoice(
                id=f"INV-{number:03d}",
                document_type=f["document_type"],
                seller_name=f["seller_name"],
                seller_tin=f.get("seller_tin"),
                invoice_number=f["invoice_number"],
                invoice_date=date.fromisoformat(f["invoice_date"]),
                due_date=date.fromisoformat(f["due_date"]) if "due_date" in f else None,
                line_items=items,
                source_pages=doc["source_pages"],
                **money,
            )
        )
    for doc, result in zip(
        expected()["documents"],
        validate_invoices(invoices, settings, date(2026, 11, 23)),
        strict=True,
    ):
        codes = {fl.code for fl in result.flags}
        assert set(doc["required_flags"]) <= codes, doc["label"]
        assert codes <= set(doc["required_flags"]) | set(doc["optional_flags"]), (
            doc["label"],
            codes,
        )
        if not doc["status_optional"]:
            assert result.status.value == doc["expected_status"], doc["label"]
