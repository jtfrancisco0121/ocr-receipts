import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from resibo.config import Settings
from resibo.llm import AnthropicLLM, RecordingLLM
from resibo.models import ErrorCode, Extraction, ResiboError
from resibo.schema import RECORD_INVOICES_SCHEMA
from tests.helpers import FakeLLM, raw_invoice, result


class StubStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class StubClient:
    def __init__(self, message=None, error=None):
        self.requests = []
        self.message = message
        self.error = error
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return StubStream(self.message)


def message(text='{"invoices": []}', stop="end_turn", tin=11, tout=7):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=tin, output_tokens=tout),
    )


def call(llm, first=1, last=3, total=3, name="f.pdf", repair=None):
    return llm.extract(b"%PDF-fake", first, last, repair, total_pages=total, file_name=name)


def test_exact_request_keywords_for_default_model():
    client = StubClient(message())
    out = call(AnthropicLLM(Settings(), client))
    assert out.raw_output == {"invoices": []} and (out.input_tokens, out.output_tokens) == (11, 7)
    assert out.stop_reason == "end_turn"
    (req,) = client.requests
    assert set(req) == {"model", "max_tokens", "system", "output_config", "messages"}
    for forbidden in ("temperature", "tools", "tool_choice", "thinking"):
        assert forbidden not in req
    assert req["model"] == "claude-sonnet-5-5" and req["max_tokens"] == 16000
    assert req["output_config"] == {
        "format": {"type": "json_schema", "schema": RECORD_INVOICES_SCHEMA},
        "effort": "low",
    }
    block = req["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"


def test_effort_omitted_for_model_without_effort_support():
    client = StubClient(message())
    call(AnthropicLLM(Settings(claude_model="claude-haiku-4-5"), client))
    assert "effort" not in client.requests[0]["output_config"]
    assert "format" in client.requests[0]["output_config"]


def test_instruction_text_block():
    client = StubClient(message())
    call(AnthropicLLM(Settings(), client), 11, 20, 23, "march.pdf")
    text = client.requests[0]["messages"][0]["content"][1]
    assert text["type"] == "text"
    assert text["text"] == (
        'These are pages 11–20 of a 23-page file named "march.pdf".\n'
        "Extract every invoice or receipt on these pages."
    )


def test_repair_instruction_appended():
    client = StubClient(message())
    call(AnthropicLLM(Settings(), client), repair="source_pages: bad")
    text = client.requests[0]["messages"][0]["content"][1]["text"]
    assert text.endswith(
        "A previous attempt failed validation with these errors: source_pages: bad. Fix them."
    )


def test_concurrent_calls_each_send_their_own_file_name():
    client = StubClient(message())
    llm = AnthropicLLM(Settings(), client)
    barrier = threading.Barrier(2)

    def worker(name):
        barrier.wait()
        call(llm, name=name)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("alpha.pdf", "beta.pdf")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sent = sorted(r["messages"][0]["content"][1]["text"] for r in client.requests)
    assert 'named "alpha.pdf"' in sent[0] and 'named "beta.pdf"' in sent[1]
    assert not hasattr(llm, "file_name") and not hasattr(llm, "total_pages")


def test_unparseable_text_gives_empty_output():
    out = call(AnthropicLLM(Settings(), StubClient(message(text="not json"))))
    assert out.raw_output == {}
    out = call(AnthropicLLM(Settings(), StubClient(message(text="", stop="max_tokens"))))
    assert out.raw_output == {} and out.stop_reason == "max_tokens"


def test_sdk_error_becomes_upstream_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=request)
    with pytest.raises(ResiboError) as exc:
        call(AnthropicLLM(Settings(), StubClient(error=err)))
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


def test_missing_key_is_a_clear_error_and_client_is_lazy():
    llm = AnthropicLLM(Settings(anthropic_api_key=None))  # constructing makes no client
    with pytest.raises(ResiboError) as exc:
        call(llm)
    assert "ANTHROPIC_API_KEY" in exc.value.message


def test_recording_llm_writes_format_v1(tmp_path):
    path = tmp_path / "rec.json"
    inner = FakeLLM({(1, 2): result(raw_invoice(source_pages=[1]))})
    rec = RecordingLLM(inner, path, clock=lambda: datetime(2026, 11, 23, 1, 2, 3, tzinfo=UTC))
    out = rec.extract(b"x", 1, 2, total_pages=2, file_name="f.pdf")
    assert out.stop_reason == "end_turn" and not path.exists()  # nothing written until save
    extraction = Extraction(
        invoices=[],
        page_count=2,
        model="claude-sonnet-5-5",
        duration_ms=1234,
        input_tokens=100,
        output_tokens=50,
    )
    rec.save(extraction, "f.pdf", 10)
    data = json.loads(path.read_text())
    assert data["format_version"] == 1 and data["file_name"] == "f.pdf"
    assert data["page_count"] == 2 and data["pages_per_chunk"] == 10
    assert data["model"] == "claude-sonnet-5-5" and data["duration_ms"] == 1234
    assert data["recorded_at"] == "2026-11-23T01:02:03Z"
    assert data["usage"]["input_tokens"] == 100
    assert data["usage"]["estimated_cost_usd"] == pytest.approx(0.0007)
    (c,) = data["calls"]
    assert (c["first_page"], c["last_page"], c["repair"]) == (1, 2, False)
    assert c["stop_reason"] == "end_turn" and c["raw_output"]["invoices"][0]["source_pages"] == [1]


def test_recording_llm_requires_explicit_path():
    with pytest.raises(ValueError):
        RecordingLLM(FakeLLM({}), "")
