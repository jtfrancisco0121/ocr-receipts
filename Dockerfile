FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime dependencies only (no dev extra: no reportlab or Pillow).
COPY pyproject.toml README.md ./
COPY resibo/ resibo/
RUN pip install .

# The app finds samples/ from the working directory (/app/samples), never from site-packages.
# Copying the directory keeps the build working while samples/sample-result.json is not
# recorded yet (a missing single file would fail COPY).
COPY samples/ samples/

RUN useradd --create-home --uid 10001 resibo && chown -R resibo:resibo /app
USER resibo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'))"]

# Shell form so ${PORT} expands; exec so SIGTERM reaches uvicorn. One worker: the store, the
# rate limiter and the budget are per process. No --proxy-headers: ratelimit.client_ip reads
# X-Forwarded-For itself and never trusts the client-written leftmost entry.
CMD exec uvicorn resibo.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
