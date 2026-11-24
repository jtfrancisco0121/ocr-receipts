# Resibo architecture

Functional core, imperative shell. The core is synchronous Python: PDF bytes in, typed records
and file bytes out. Claude is reached only through the `InvoiceLLM` port, so every test runs
offline with `FakeLLM` or `ReplayLLM`. Shells (HTTP API, CLI, eval, the cached sample) own I/O,
clocks, limits and state, and all call one entry point, `extractor.extract_batch`.

```mermaid
flowchart LR
    subgraph Inputs
        U[Web UI upload]
        N[n8n workflow<br/>Google Drive trigger]
        C[CLI]
    end
    U --> API
    N -->|POST /api/extract + X-API-Key| API
    C --> CORE
    subgraph Resibo [Resibo: FastAPI on Railway, Docker]
        API[FastAPI routes<br/>limits, auth, rate limit] --> CORE
        CORE[extract_batch] --> SPLIT[PDF check and chunking<br/>pypdf]
        SPLIT --> LLM[InvoiceLLM port<br/>Claude call, structured output]
        LLM --> PARSE[Pydantic parse<br/>+ one repair retry]
        PARSE --> VAL[Business validation<br/>totals, VAT, duplicates, TIN, dates]
        VAL --> STORE[(In-memory batch store<br/>TTL 60 min)]
        STORE --> EXP[Exporters<br/>xlsx / csv / json]
    end
    LLM <--> ANT[(Anthropic API)]
    EXP --> OUT[Downloads / JSON response]
    N --> GS[(Google Sheet)]
    N --> MAIL[Summary email]
```

## Layers

| Layer | Modules |
|---|---|
| Core: data | `resibo/models.py`, `resibo/schema.py`, `resibo/prompts.py`, `resibo/config.py` |
| Core: pipeline | `resibo/pdf_utils.py`, `resibo/extractor.py`, `resibo/validation.py`, `resibo/exporters.py` |
| Port and adapters | `InvoiceLLM` and `AnthropicLLM`, `RecordingLLM` in `resibo/llm.py`; `ReplayLLM` in `resibo/sample.py`; `FakeLLM` in `tests/helpers.py` |
| Shell: service | `resibo/api.py`, `resibo/store.py`, `resibo/ratelimit.py`, `resibo/sample.py`, `resibo/static/` |
| Shell: tools | `resibo/__main__.py`, `scripts/make_samples.py`, `scripts/eval.py` |

Rules a test enforces (`tests/test_architecture.py`): core modules never import `fastapi`,
`starlette`, `anthropic` or a shell module; only `llm.py` imports `anthropic`; only `config.py`
reads the environment.

## One extraction

```mermaid
sequenceDiagram
    participant S as Shell (api / cli / sample / eval)
    participant E as extractor
    participant P as CallPolicy
    participant L as InvoiceLLM
    S->>E: extract_batch(pdf, file_name, llm, settings, policy, today)
    E->>E: PDF checks, chunk queue
    loop each page range
        E->>P: reserve(pages)
        E->>L: extract(bytes, first, last, repair_errors, total_pages, file_name)
        L-->>E: LLMResult
        E->>P: settle(token, in, out)
        E->>E: stop reason, parse, repair once, normalize, stitch
    end
    E->>E: validate(records, today), status
    E-->>S: Extraction (the shell calls build_batch)
```

Halves (after truncation) and repairs go through the same `reserve`/`settle` pair, so the daily
budget covers every Claude call. The public policy allows one halving level and one repair.

## Decisions that are easy to miss

- **Structured outputs, not forced tool calls**: `claude-sonnet-5-5` rejects `tool_choice` and
  non-default `temperature`, so the request uses `output_config.format` with a fixed schema.
  The schema in `resibo/schema.py` equals SPEC.md section 8.7 and is never generated.
- **Recording format v1** (`RecordingLLM`): one JSON file holding the raw model output per
  chunk. The cached sample replays it through the normal pipeline, so flags always reflect the
  current rules. Only a real `--record` run may write `samples/sample-result.json`.
- **Per-call context on the port**: `total_pages` and `file_name` are arguments of `extract`,
  not state on the adapter, because one `AnthropicLLM` serves concurrent requests.
- **Budget pricing**: a model missing from the price table is reserved at the highest known
  price, so the public cap cannot be disabled by a configuration typo.
- **CSP scope**: the strict content security policy applies to `/`, `/app` and `/static/*` only;
  FastAPI's `/docs` page needs an inline script and CDN assets.
- **Sample files** are found from the working directory (`samples/`; `/app/samples` in the
  image), never from the installed package location.
