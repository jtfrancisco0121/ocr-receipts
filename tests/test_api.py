import asyncio
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfWriter

from resibo.api import UI_CSP, create_app
from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import LLMResult, RecordingLLM
from resibo.models import ErrorCode, ResiboError
from tests.helpers import FakeLLM, raw_invoice, result


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def two_docs_llm() -> FakeLLM:
    return FakeLLM(
        {
            (1, 2): result(
                raw_invoice(source_pages=[1], invoice_number="A"),
                raw_invoice(
                    source_pages=[2],
                    invoice_number="B",
                    seller_tin="999-999-999-00000",
                    total_amount="99999.00",
                ),
            )
        }
    )


class Env:
    def __init__(self, tmp_path, llm=None, clock=None, **settings):
        self.settings = Settings(**settings)
        self.clock = clock or Clock()
        self.samples = tmp_path / "samples"
        self.samples.mkdir(exist_ok=True)
        self.llm = llm or two_docs_llm()
        self.app = create_app(self.settings, self.llm, self.samples, self.clock)
        self.client = TestClient(self.app)
        self.state = self.app.state.resibo

    def upload(self, data=None, name="inv.pdf", headers=None, **params):
        data = pdf(2) if data is None else data
        return self.client.post(
            "/api/extract",
            files={"file": (name, data, "application/pdf")},
            headers=headers or {},
            params=params,
        )

    def record_sample(self):
        """A temporary recording built from FakeLLM output (never samples/sample-result.json)."""
        data = pdf(2)
        (self.samples / "sample-invoices.pdf").write_bytes(data)
        recorder = RecordingLLM(two_docs_llm(), self.samples / "sample-result.json")
        settings = self.settings.model_copy(update={"pages_per_chunk": 10})
        extraction = extract_batch(
            data, "sample-invoices.pdf", recorder, settings, CallPolicy(), self.clock().date()
        )
        recorder.save(extraction, "sample-invoices.pdf", 10)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def error_of(response):
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return body["error"]["code"]


# --- happy paths ----------------------------------------------------------------------------


def test_extract_happy_path_json_and_downloads(env):
    r = env.upload()
    assert r.status_code == 200
    batch = r.json()
    assert batch["cached"] is False and batch["file_name"] == "inv.pdf"
    assert batch["summary"]["documents"] == 2 and batch["summary"]["error"] == 1
    assert [i["id"] for i in batch["invoices"]] == ["INV-001", "INV-002"]
    assert isinstance(batch["invoices"][0]["total_amount"], float)
    assert batch["invoices"][1]["flags"][0]["code"] == "TOTAL_MISMATCH"
    assert batch["recorded_at"] is None
    assert env.client.get(f"/api/batches/{batch['batch_id']}").json() == batch

    xlsx = env.client.get(batch["downloads"]["xlsx"])
    assert xlsx.status_code == 200 and "spreadsheetml" in xlsx.headers["content-type"]
    assert load_workbook(BytesIO(xlsx.content)).sheetnames == ["Invoices", "Line Items", "Summary"]
    for key in ("invoices_csv", "line_items_csv"):
        csv_response = env.client.get(batch["downloads"][key])
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert csv_response.content.startswith(b"\xef\xbb\xbf")


def test_extract_format_xlsx_and_csv(env):
    r = env.upload(format="xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert load_workbook(BytesIO(r.content)).sheetnames[0] == "Invoices"
    r = env.upload(format="csv")
    assert r.headers["content-type"].startswith("text/csv") and r.content.startswith(
        b"\xef\xbb\xbf"
    )


def test_health_and_docs_and_headers(env):
    r = env.client.get("/health")
    assert r.json() == {"status": "ok", "model": "claude-sonnet-5-5"}
    assert env.client.head("/health").status_code == 200
    docs = env.client.get("/docs")
    assert docs.status_code == 200
    assert "default-src" not in docs.headers.get("content-security-policy", "")
    assert env.client.get("/openapi.json").status_code == 200
    assert "/api/extract" in env.client.get("/openapi.json").json()["paths"]
    for path in ("/health", "/docs", "/openapi.json", "/api/batches/nope"):
        assert env.client.get(path).headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in env.client.get("/health").headers


def test_ui_routes_carry_the_strict_csp_when_present(env, tmp_path, monkeypatch):
    from resibo import api

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>")
    (static / "app.js").write_text("// js")
    monkeypatch.setattr(api, "STATIC_DIR", static)
    local = Env(tmp_path)
    from starlette.staticfiles import StaticFiles

    local.app.router.routes[:] = [r for r in local.app.router.routes if r.path != "/static"]
    local.app.mount("/static", StaticFiles(directory=static), name="static")
    for path in ("/", "/static/app.js"):
        r = local.client.get(path)
        assert r.status_code == 200
        assert r.headers["content-security-policy"] == UI_CSP
        assert r.headers["x-content-type-options"] == "nosniff"


# --- errors ---------------------------------------------------------------------------------


def test_not_a_pdf_and_missing_field(env):
    r = env.upload(b"hello", name="x.pdf")
    assert r.status_code == 400 and error_of(r) == "NOT_A_PDF"
    r = env.client.post("/api/extract", data={"other": "1"})
    assert r.status_code == 400 and error_of(r) == "NOT_A_PDF"


def test_pdf_problems_map_to_their_codes(env):
    assert error_of(env.upload(b"%PDF-1.4 junk")) == "PDF_UNREADABLE"
    empty = PdfWriter()
    buf = BytesIO()
    empty.write(buf)
    assert error_of(env.upload(buf.getvalue())) == "PDF_EMPTY"


def test_unmatched_route_uses_the_envelope(env):
    r = env.client.get("/api/nope")
    assert r.status_code == 404 and error_of(r) == "NOT_FOUND"


def test_unhandled_error_is_internal_envelope(tmp_path):
    class Boom:
        def extract(self, *a, **k):
            raise RuntimeError("secret detail must not leak")

    local = Env(tmp_path, llm=Boom())
    client = TestClient(local.app, raise_server_exceptions=False)
    r = client.post("/api/extract", files={"file": ("a.pdf", pdf(1), "application/pdf")})
    assert r.status_code == 500 and error_of(r) == "INTERNAL"
    assert "secret" not in r.text


def test_upstream_error_envelope(tmp_path):
    class Down:
        def extract(self, *a, **k):
            raise ResiboError(ErrorCode.UPSTREAM_ERROR, "The AI service is down.")

    local = Env(tmp_path, llm=Down())
    r = local.upload()
    assert r.status_code == 502 and error_of(r) == "UPSTREAM_ERROR"


# --- size limits ----------------------------------------------------------------------------


def test_413_by_content_length(tmp_path):
    local = Env(tmp_path, max_upload_mb=1)
    r = local.upload(b"%PDF-" + b"0" * (1024 * 1024 + 10))
    assert r.status_code == 413 and error_of(r) == "FILE_TOO_LARGE"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert local.llm.calls == []


def test_413_for_chunked_upload_without_content_length(tmp_path):
    local = Env(tmp_path, max_upload_mb=1)

    def body():
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n\r\n'
        for _ in range(20):
            yield b"%PDF-" + b"0" * 100_000
        yield b"\r\n--b--\r\n"

    r = local.client.post(
        "/api/extract", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert r.status_code == 413 and error_of(r) == "FILE_TOO_LARGE"


def test_413_when_the_body_is_larger_than_the_declared_length(tmp_path):
    local = Env(tmp_path, max_upload_mb=1)
    sent = []
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n\r\n%PDF-'
    chunks = [head + b"x" * 600_000, b"x" * 600_000, b"x" * 600_000]

    async def run():
        queue = list(chunks)

        async def receive():
            if queue:
                return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/extract",
            "raw_path": b"/api/extract",
            "query_string": b"",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=b"),
                (b"content-length", b"100"),  # a lie
            ],
            "client": ("1.2.3.4", 5),
            "server": ("test", 80),
        }
        await local.app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert start["status"] == 413
    assert json.loads(body)["error"]["code"] == "FILE_TOO_LARGE"


# --- page limits and auth -------------------------------------------------------------------


def big_llm(pages, **kw):
    return FakeLLM({(1, pages): result(raw_invoice(source_pages=[1]))})


def test_422_public_too_many_pages_but_allowed_with_api_key(tmp_path):
    local = Env(tmp_path, llm=big_llm(16), resibo_api_key="s3cret", pages_per_chunk=20)
    r = local.upload(pdf(16))
    assert r.status_code == 422 and error_of(r) == "TOO_MANY_PAGES"
    assert r.json()["error"]["message"] == "This demo accepts up to 15 pages. Your file has 16."
    ok = local.upload(pdf(16), headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


def test_401_for_a_bad_key_and_keyed_requests_skip_limits(tmp_path):
    local = Env(tmp_path, resibo_api_key="s3cret", public_rate_limit_per_hour=1)
    r = local.upload(headers={"X-API-Key": "wrong"})
    assert r.status_code == 401 and error_of(r) == "UNAUTHORIZED"
    for _ in range(4):
        assert local.upload(headers={"X-API-Key": "s3cret"}).status_code == 200
    assert local.state.budget.spent == 0  # keyed requests are not charged to the public budget


def test_a_key_header_without_a_configured_key_is_401(env):
    assert env.upload(headers={"X-API-Key": "anything"}).status_code == 401


# --- rate limit -----------------------------------------------------------------------------


def test_429_with_retry_after_and_rejected_files_do_not_count(tmp_path):
    local = Env(tmp_path, public_rate_limit_per_hour=2)
    for _ in range(3):
        assert local.upload(b"not a pdf").status_code == 400  # rejected before the limiter
    assert local.upload().status_code == 200
    assert local.upload().status_code == 200
    r = local.upload()
    assert r.status_code == 429 and error_of(r) == "RATE_LIMITED"
    assert int(r.headers["retry-after"]) > 0
    local.clock.advance(hours=1, seconds=1)
    assert local.upload().status_code == 200


def test_spoofed_leftmost_forwarded_for_does_not_reset_the_limit(tmp_path):
    local = Env(tmp_path, public_rate_limit_per_hour=2)
    for n in range(2):
        headers = {"X-Forwarded-For": f"6.6.6.{n}, 34.1.2.3"}
        assert local.upload(headers=headers).status_code == 200
    spoofed = {"X-Forwarded-For": "7.7.7.7, 34.1.2.3"}
    assert local.upload(headers=spoofed).status_code == 429
    other_client = {"X-Forwarded-For": "7.7.7.7, 34.9.9.9"}
    assert local.upload(headers=other_client).status_code == 200


# --- budget ---------------------------------------------------------------------------------


def test_503_when_the_budget_is_used_up_while_sample_and_keyed_still_work(tmp_path):
    local = Env(tmp_path, daily_budget_usd="0.01", resibo_api_key="k")
    local.record_sample()
    r = local.upload()
    assert r.status_code == 503 and error_of(r) == "DEMO_BUDGET_EXHAUSTED"
    assert "Try the sample" in r.json()["error"]["message"]
    assert local.llm.calls == []
    assert local.client.get("/api/sample/result").status_code == 200
    assert local.upload(headers={"X-API-Key": "k"}).status_code == 200


def test_503_mid_request_when_a_later_chunks_reservation_fails(tmp_path):
    heavy = LLMResult({"invoices": [raw_invoice(source_pages=[1])]}, 50_000, 0, "end_turn")
    llm = FakeLLM({(1, 1): heavy, (2, 2): result(raw_invoice(source_pages=[2]))})
    # one call reserves about $0.166; the first call really costs $0.10, leaving too little
    local = Env(tmp_path, llm=llm, daily_budget_usd="0.17", pages_per_chunk=1, resibo_api_key="k")
    local.record_sample()
    r = local.upload()
    assert r.status_code == 503 and error_of(r) == "DEMO_BUDGET_EXHAUSTED"
    assert len(llm.calls) == 1
    assert local.client.get("/api/sample/result").status_code == 200
    assert local.upload(headers={"X-API-Key": "k"}).status_code == 200


# --- cached sample --------------------------------------------------------------------------


def test_sample_result_replays_a_recording_and_is_shared(env):
    env.record_sample()
    first = env.client.get("/api/sample/result").json()
    assert first["cached"] is True and first["summary"]["documents"] == 2
    assert first["recorded_at"] is not None and first["file_name"] == "sample-invoices.pdf"
    assert first["invoices"][1]["flags"][0]["code"] == "TOTAL_MISMATCH"
    assert env.client.get(first["downloads"]["xlsx"]).status_code == 200
    assert env.client.get(first["downloads"]["invoices_csv"]).status_code == 200
    again = env.client.get("/api/sample/result").json()
    assert again["batch_id"] == first["batch_id"]
    assert len(env.state.store) == 1  # repeated calls do not grow the store
    assert env.state.budget.spent == 0  # not counted against the budget


def test_sample_batch_is_rebuilt_after_it_is_evicted_from_the_store(env):
    env.record_sample()
    first = env.client.get("/api/sample/result").json()
    template = env.state.store.get(first["batch_id"])
    for n in range(500):
        env.state.store.put(template.model_copy(update={"batch_id": f"filler{n}"}))
    assert not env.state.store.contains(first["batch_id"])
    second = env.client.get("/api/sample/result").json()
    assert second["batch_id"] != first["batch_id"]
    assert env.client.get(second["downloads"]["xlsx"]).status_code == 200


def test_sample_batch_is_rebuilt_after_it_expires(env):
    env.record_sample()
    first = env.client.get("/api/sample/result").json()
    env.clock.advance(minutes=61)
    assert env.client.get("/api/sample/result").json()["batch_id"] != first["batch_id"]


def test_sample_result_not_recorded_is_404(env):
    r = env.client.get("/api/sample/result")
    assert r.status_code == 404 and error_of(r) == "SAMPLE_NOT_RECORDED"
    assert r.json()["error"]["message"].startswith("Sample result not recorded yet")
    assert not (env.samples / "sample-result.json").exists()  # the app never writes it


def test_sample_result_has_its_own_per_ip_limit_of_30_a_minute(env):
    env.record_sample()
    for _ in range(30):
        assert env.client.get("/api/sample/result").status_code == 200
    r = env.client.get("/api/sample/result")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert env.upload().status_code == 200  # the extract limiter is separate
    other = env.client.get("/api/sample/result", headers={"X-Forwarded-For": "1.1.1.1, 8.8.8.8"})
    assert other.status_code == 200
    env.clock.advance(seconds=61)
    assert env.client.get("/api/sample/result").status_code == 200


def test_sample_pdf_is_served_from_samples_dir(env):
    env.record_sample()
    r = env.client.get("/api/sample")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content == (env.samples / "sample-invoices.pdf").read_bytes()
    assert env.client.head("/api/sample").status_code == 200


def test_sample_pdf_missing_is_an_envelope_error(tmp_path):
    local = Env(tmp_path)
    r = local.client.get("/api/sample")
    assert r.status_code == 500 and error_of(r) == "INTERNAL"


def test_default_samples_dir_is_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "sample-invoices.pdf").write_bytes(pdf(1))
    client = TestClient(create_app(Settings(), two_docs_llm()))
    assert client.get("/api/sample").status_code == 200


# --- store ----------------------------------------------------------------------------------


def test_expired_batch_is_404(env):
    batch = env.upload().json()
    assert env.client.get(f"/api/batches/{batch['batch_id']}").status_code == 200
    env.clock.advance(minutes=61)
    for path in (
        f"/api/batches/{batch['batch_id']}",
        batch["downloads"]["xlsx"],
        batch["downloads"]["invoices_csv"],
        batch["downloads"]["line_items_csv"],
    ):
        r = env.client.get(path)
        assert r.status_code == 404 and error_of(r) == "BATCH_NOT_FOUND"


def test_store_evicts_the_oldest_beyond_500(env):
    first = env.upload().json()
    template = env.state.store.get(first["batch_id"])
    for n in range(500):
        env.state.store.put(template.model_copy(update={"batch_id": f"b{n}"}))
    assert len(env.state.store) == 500
    assert env.client.get(f"/api/batches/{first['batch_id']}").status_code == 404
    assert env.client.get("/api/batches/b499").status_code == 200


# --- startup --------------------------------------------------------------------------------


def test_startup_fails_fast_without_a_key_unless_an_llm_is_injected():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        with TestClient(create_app(Settings())):
            pass
    with TestClient(create_app(Settings(), two_docs_llm())) as client:
        assert client.get("/health").status_code == 200


def test_startup_with_a_dummy_key_makes_no_outbound_call():
    with TestClient(create_app(Settings(anthropic_api_key="dummy"))) as client:
        assert client.get("/health").json()["status"] == "ok"  # sockets are blocked in tests


def test_unpriced_model_logs_a_warning_at_startup(caplog):
    caplog.set_level("WARNING", logger="resibo.api")
    with TestClient(create_app(Settings(claude_model="mystery", anthropic_api_key="d"))):
        pass
    assert "mystery" in caplog.text


def test_extraction_logs_one_json_line_without_content(env, caplog):
    caplog.set_level("INFO", logger="resibo.api")
    env.upload()
    line = next(r.message for r in caplog.records if '"event": "extraction"' in r.message)
    data = json.loads(line)
    assert data["documents"] == 2 and data["public"] is True and data["cached"] is False
    assert "Mabuhay" not in caplog.text and "inv.pdf" not in caplog.text
