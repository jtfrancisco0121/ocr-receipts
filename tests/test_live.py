"""Optional live smoke test: hits the real Anthropic API. Skipped unless a key is set.

ANTHROPIC_API_KEY=... pytest -m live
"""

import pytest

from resibo.config import Settings
from resibo.extractor import extract_batch
from resibo.llm import AnthropicLLM
from resibo.pdf_utils import extract_pages
from tests.test_samples import SAMPLES

KEY_SET = bool(Settings().anthropic_api_key)


@pytest.mark.live
@pytest.mark.skipif(not KEY_SET, reason="ANTHROPIC_API_KEY is not set")
def test_live_one_page_of_the_sample_is_read():
    from datetime import date

    settings = Settings()
    one_page = extract_pages((SAMPLES / "sample-invoices.pdf").read_bytes(), 1, 1)
    extraction = extract_batch(
        one_page, "page1.pdf", AnthropicLLM(settings), settings, None, date(2026, 11, 23)
    )
    assert extraction.invoices, "the live API returned no documents (schema accepted?)"
    assert extraction.invoices[0].total_amount is not None
