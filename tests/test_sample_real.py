"""Replays the human's real recording (step 7) against samples/expected.json.

Skipped while samples/sample-result.json is absent. Nothing here creates or edits that file.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from resibo.config import Settings
from resibo.sample import load_recording, replay_extraction

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
RECORDING = SAMPLES / "sample-result.json"


@pytest.mark.skipif(
    not RECORDING.exists(), reason="samples/sample-result.json is recorded by the human at step 7"
)
def test_real_recording_matches_expected_documents_and_required_flags():
    expected = json.loads((SAMPLES / "expected.json").read_text())
    extraction = replay_extraction(
        load_recording(RECORDING),
        (SAMPLES / "sample-invoices.pdf").read_bytes(),
        Settings(),
        date.fromisoformat(expected["anchor_date"]),
    )
    assert len(extraction.invoices) == len(expected["documents"])
    for doc, invoice in zip(expected["documents"], extraction.invoices, strict=True):
        codes = {f.code for f in invoice.flags}
        assert set(doc["required_flags"]) <= codes, doc["label"]
        if doc["status_optional"]:
            # only judged when its optional flag actually fired
            if set(doc["optional_flags"]) & codes:
                assert invoice.status.value == doc["expected_status"], doc["label"]
        else:
            assert invoice.status.value == doc["expected_status"], doc["label"]
