"""FastAPI shell: routes, auth, limits, error envelope and security headers (SPEC.md section 11).

The shell owns I/O, clocks and state; extraction itself is the one ``extract_batch`` call (AD-1).
Run with ONE worker: the store, the limiters and the budget are per process (AD-20).
"""

import json
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from resibo import config, exporters
from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import AnthropicLLM, InvoiceLLM
from resibo.models import Batch, ErrorCode, ResiboError, build_batch
from resibo.ratelimit import DailyBudget, SlidingWindowLimiter, budget_hooks, client_ip
from resibo.sample import SampleService
from resibo.store import BatchStore
from resibo.validation import manila_today

log = logging.getLogger("resibo.api")

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_TYPE = "text/csv; charset=utf-8"
UI_CSP = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


def envelope(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


@dataclass
class AppState:
    settings: Settings
    llm: InvoiceLLM
    clock: Callable[[], datetime]
    store: BatchStore
    extract_limiter: SlidingWindowLimiter
    sample_limiter: SlidingWindowLimiter
    budget: DailyBudget
    sample: SampleService


# --- middleware (pure ASGI) -----------------------------------------------------------------


class SecurityHeadersMiddleware:
    """``nosniff`` on every response; the strict CSP and ``Cache-Control: no-cache`` only on the UI
    (``/``, ``/app`` and ``/static/*``) so FastAPI's ``/docs`` page, which needs an inline script
    and CDN assets, keeps working. ``no-cache`` makes a returning visitor revalidate the pages and
    scripts together after a deploy instead of mixing old and new files."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        is_ui = path in ("/", "/app") or path.startswith("/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                if is_ui:
                    headers.append((b"content-security-policy", UI_CSP.encode()))
                    headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def too_large_message(limit: int) -> str:
    return f"This file is too large. The limit is {limit / (1024 * 1024):g} MB."


class UploadTooLarge(FastAPIHTTPException):
    """Raised from the body stream once the limit is passed. It subclasses FastAPI's
    HTTPException because FastAPI turns any other exception from body parsing into a 400."""

    def __init__(self, limit: int):
        super().__init__(status_code=413, detail=too_large_message(limit))


class UploadLimitMiddleware:
    """Enforces MAX_UPLOAD_MB on ``POST /api/extract``: rejects on Content-Length when present,
    otherwise counts the body as it streams in and aborts once over the limit (AD-13)."""

    def __init__(self, app, max_bytes: Callable[[], int]):
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/api/extract"
        ):
            await self.app(scope, receive, send)
            return
        limit = self._max_bytes()
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                response = JSONResponse(
                    envelope(ErrorCode.FILE_TOO_LARGE.value, too_large_message(limit)),
                    status_code=413,
                )
                await response(scope, receive, send)
                return
        seen = 0

        async def counting_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise UploadTooLarge(limit)
            return message

        await self.app(scope, counting_receive, send)


# --- app factory ----------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    llm: InvoiceLLM | None = None,
    samples_dir: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    clock = clock or (lambda: datetime.now(UTC))
    samples_dir = Path(samples_dir) if samples_dir else Path.cwd() / "samples"
    injected = llm is not None
    logging.getLogger("resibo").setLevel(settings.log_level.upper())
    if not logging.getLogger("resibo").handlers and not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")

    store = BatchStore(clock)
    state = AppState(
        settings=settings,
        llm=llm or AnthropicLLM(settings),  # builds its client lazily: no call at startup
        clock=clock,
        store=store,
        extract_limiter=SlidingWindowLimiter(
            settings.public_rate_limit_per_hour,
            3600,
            clock,
            message="You have reached the live-run limit for this hour.",
        ),
        sample_limiter=SlidingWindowLimiter(30, 60, clock, message="Too many sample requests."),
        budget=DailyBudget(settings.daily_budget_usd, clock),
        sample=SampleService(samples_dir, settings, store, clock),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not injected and not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it in the environment before starting Resibo."
            )
        if not config.is_priced(settings.claude_model):
            log.warning(
                "CLAUDE_MODEL %r is not in the price table: costs show as null and the daily "
                "budget charges it at the highest known price.",
                settings.claude_model,
            )
        yield

    app = FastAPI(title="Resibo", description="AI invoice and receipt extractor", lifespan=lifespan)
    app.state.resibo = state
    app.add_middleware(
        UploadLimitMiddleware, max_bytes=lambda: settings.max_upload_mb * 1024 * 1024
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # --- errors -----------------------------------------------------------------------------

    @app.exception_handler(ResiboError)
    async def resibo_error_handler(request: Request, exc: ResiboError):
        return JSONResponse(
            envelope(exc.code.value, exc.message), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        error = ResiboError(
            ErrorCode.NOT_A_PDF, "No PDF was received. Send it as the multipart field 'file'."
        )
        return await resibo_error_handler(request, error)

    @app.exception_handler(UploadTooLarge)
    async def too_large_handler(request: Request, exc: UploadTooLarge):
        return JSONResponse(
            envelope(ErrorCode.FILE_TOO_LARGE.value, str(exc.detail)), status_code=413
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        names = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        code = names.get(exc.status_code, "INTERNAL" if exc.status_code >= 500 else "BAD_REQUEST")
        return JSONResponse(
            envelope(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        log.error("unhandled error type=%s path=%s", type(exc).__name__, request.url.path)
        return JSONResponse(
            envelope(
                ErrorCode.INTERNAL.value, "Something went wrong on our side. Please try again."
            ),
            status_code=500,
        )

    # --- helpers ----------------------------------------------------------------------------

    def ip_of(request: Request) -> str:
        peer = request.client.host if request.client else None
        return client_ip(request.headers, peer, settings.trusted_proxy_hops)

    def is_keyed(request: Request) -> bool:
        """True for a valid X-API-Key; a wrong or unexpected key is 401 (never public)."""
        supplied = request.headers.get("x-api-key")
        if supplied is None:
            return False
        expected = settings.resibo_api_key
        if expected and secrets.compare_digest(supplied.encode(), expected.encode()):
            return True
        raise ResiboError(ErrorCode.UNAUTHORIZED, "The API key is missing or not valid.")

    def processed_at(batch: Batch) -> datetime:
        return batch.expires_at - timedelta(minutes=settings.result_ttl_minutes)

    def log_extraction(batch: Batch, extraction, public: bool) -> None:
        s = batch.summary
        log.info(
            json.dumps(
                {
                    "event": "extraction",
                    "batch_id": batch.batch_id,
                    "pages": batch.page_count,
                    "chunks": extraction.chunks,
                    "model": batch.model,
                    "duration_ms": batch.duration_ms,
                    "input_tokens": batch.usage.input_tokens,
                    "output_tokens": batch.usage.output_tokens,
                    "estimated_cost_usd": batch.usage.estimated_cost_usd,
                    "documents": s.documents,
                    "ok": s.ok,
                    "review": s.review,
                    "error": s.error,
                    "repair_retries": extraction.repair_retries,
                    "halvings": extraction.halvings,
                    "cached": batch.cached,
                    "public": public,
                    "budget_spent_today_usd": float(state.budget.spent),
                }
            )
        )

    def attachment(name: str) -> dict[str, str]:
        return {"Content-Disposition": f'attachment; filename="{name}"'}

    # --- routes -----------------------------------------------------------------------------

    def page(name: str) -> FileResponse:
        path = STATIC_DIR / name
        if not path.is_file():
            raise StarletteHTTPException(404, "The web page is not installed.")
        return FileResponse(path, media_type="text/html")

    @app.get("/", include_in_schema=False)
    @app.head("/", include_in_schema=False)
    def landing():
        return page("landing.html")

    @app.get("/app", include_in_schema=False)
    @app.head("/app", include_in_schema=False)
    def tool():
        return page("index.html")

    @app.get("/health")
    @app.head("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "model": settings.claude_model}

    @app.get("/api/sample")
    @app.head("/api/sample", include_in_schema=False)
    def sample_pdf():
        path = state.sample.pdf_path
        if not path.is_file():
            raise ResiboError(ErrorCode.INTERNAL, "The sample PDF is missing on this server.")
        return FileResponse(path, media_type="application/pdf", filename="sample-invoices.pdf")

    @app.get("/api/sample/result")
    def sample_result(request: Request):
        state.sample_limiter.hit(ip_of(request))
        return JSONResponse(state.sample.get_batch().model_dump(mode="json"))

    @app.post("/api/extract")
    def extract(request: Request, file: UploadFile = File(...), format: str = "json"):
        keyed = is_keyed(request)
        data = file.file.read()
        file_name = file.filename or "upload.pdf"
        today = manila_today(state.clock())
        if keyed:
            policy = CallPolicy(max_pages=settings.max_pages)
        else:
            ip = ip_of(request)
            reserve, settle = budget_hooks(state.budget, settings)
            policy = CallPolicy(
                max_halving_depth=1,
                max_repairs=1,
                reserve=reserve,
                settle=settle,
                max_pages=settings.public_max_pages,
                public=True,
                # counted once the PDF checks pass, just before the first Claude call
                on_checked=lambda pages: state.extract_limiter.hit(ip),
            )
        extraction = extract_batch(data, file_name, state.llm, settings, policy, today)
        now = state.clock()
        batch = build_batch(
            extraction,
            secrets.token_urlsafe(8),
            file_name,
            now + timedelta(minutes=settings.result_ttl_minutes),
        )
        state.store.put(batch)
        log_extraction(batch, extraction, public=not keyed)
        if format == "xlsx":
            return Response(
                exporters.to_xlsx(batch, processed_at=now),
                media_type=XLSX_TYPE,
                headers=attachment(f"resibo-{batch.batch_id}.xlsx"),
            )
        if format == "csv":
            return Response(
                exporters.to_invoices_csv(batch),
                media_type=CSV_TYPE,
                headers=attachment(f"resibo-{batch.batch_id}-invoices.csv"),
            )
        return JSONResponse(batch.model_dump(mode="json"))  # json, and any unknown format

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str):
        return JSONResponse(state.store.get(batch_id).model_dump(mode="json"))

    @app.get("/api/batches/{batch_id}/export.xlsx")
    def export_xlsx(batch_id: str):
        batch = state.store.get(batch_id)
        return Response(
            exporters.to_xlsx(batch, processed_at=processed_at(batch)),
            media_type=XLSX_TYPE,
            headers=attachment(f"resibo-{batch_id}.xlsx"),
        )

    @app.get("/api/batches/{batch_id}/invoices.csv")
    def export_invoices_csv(batch_id: str):
        return Response(
            exporters.to_invoices_csv(state.store.get(batch_id)),
            media_type=CSV_TYPE,
            headers=attachment(f"resibo-{batch_id}-invoices.csv"),
        )

    @app.get("/api/batches/{batch_id}/line_items.csv")
    def export_line_items_csv(batch_id: str):
        return Response(
            exporters.to_line_items_csv(state.store.get(batch_id)),
            media_type=CSV_TYPE,
            headers=attachment(f"resibo-{batch_id}-line-items.csv"),
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return app


app = create_app()
