"""Test helpers: canned raw model output and the FakeLLM adapter."""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from resibo.llm import LLMResult


def raw_invoice(**overrides: Any) -> dict[str, Any]:
    """A complete raw invoice (all-string scalars) as the model would return it."""
    base: dict[str, Any] = {
        "document_type": "sales_invoice",
        "seller_name": "Mabuhay Office Supply",
        "seller_tin": "123-456-789-00000",
        "seller_address": "12 Rizal Ave, Manila",
        "buyer_name": "Sampaguita Trading",
        "buyer_tin": "",
        "buyer_address": "",
        "invoice_number": "SI-0001",
        "invoice_date": "2026-09-04",
        "due_date": "",
        "payment_terms": "",
        "payment_method": "",
        "currency": "PHP",
        "vatable_sales": "10000.00",
        "vat_exempt_sales": "",
        "zero_rated_sales": "",
        "subtotal": "",
        "vat_amount": "1200.00",
        "discount": "",
        "withholding_tax": "",
        "total_amount": "11200.00",
        "line_items": [],
        "source_pages": [1],
        "is_complete": True,
        "uncertain_fields": [],
        "extraction_notes": "",
    }
    base.update(overrides)
    return base


def output(*invoices: dict[str, Any]) -> dict[str, Any]:
    return {"invoices": list(invoices)}


def result(*invoices: dict[str, Any], stop_reason: str = "end_turn") -> LLMResult:
    return LLMResult(output(*invoices), 100, 50, stop_reason)


class FakeLLM:
    """InvoiceLLM returning canned results keyed by page range.

    ``responses[(first, last)]`` is a result or a list of results used in call order; the last
    one repeats. Every call is recorded in ``calls``.
    """

    def __init__(self, responses: dict[tuple[int, int], LLMResult | Sequence[LLMResult]]):
        self.responses = {
            key: [value] if isinstance(value, LLMResult) else list(value)
            for key, value in responses.items()
        }
        self.calls: list[dict[str, Any]] = []

    def extract(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None = None,
        *,
        total_pages: int,
        file_name: str,
    ) -> LLMResult:
        self.calls.append(
            {
                "first": first_page,
                "last": last_page,
                "repair_errors": repair_errors,
                "total_pages": total_pages,
                "file_name": file_name,
                "pdf_bytes": pdf_bytes,
            }
        )
        queue = self.responses.get((first_page, last_page))
        if not queue:
            raise AssertionError(f"FakeLLM has no response for pages {first_page}-{last_page}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return deepcopy(item)
