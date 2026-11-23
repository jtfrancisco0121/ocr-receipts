"""The InvoiceLLM port and its adapters. The only module that imports ``anthropic`` (AD-1)."""

from typing import NamedTuple, Protocol


class LLMResult(NamedTuple):
    raw_output: dict
    input_tokens: int
    output_tokens: int
    stop_reason: str


class InvoiceLLM(Protocol):
    def extract(
        self,
        pdf_bytes: bytes,
        first_page: int,
        last_page: int,
        repair_errors: str | None = None,
        *,
        total_pages: int,
        file_name: str,
    ) -> LLMResult: ...
