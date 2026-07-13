# The Sentinel

Event-driven FastAPI code auditing system with multi-engine risk analysis, optional
LLM enrichment, GitHub-native PR commenting, documentation review, and report translation.

## Architecture

Clean Architecture with strict layer boundaries. The dependency rule (the domain imports
nothing from FastAPI, sklearn, or infrastructure) is **enforced by tests** in
`sentinel/tests/hardening/test_architecture_extended.py`.

```
API (FastAPI) → Application (Orchestration) → Domain (Pure Logic) ← Infrastructure (sklearn / LLM / GitHub)
                         ↕
                   Workers (Async)
```

### Analysis Engines

| Engine | Layer | Purpose |
|---|---|---|
| **Technical Debt** | domain | Cyclomatic complexity, line count, nesting depth analysis |
| **Security** | domain | Regex-based vulnerability detection + category/OWASP classification (SQL injection, XSS, hardcoded secrets, etc.) |
| **Semantic** | domain + infra | Duplicate/similar code detection via embedding cosine similarity |
| **Documentation** | domain | Rule-based docs/docstring/comment checks for `.md`/`.txt` files and code strings |
| **LLM Enrichment** (optional) | infra | Explanation + fix suggestions for HIGH/CRITICAL security findings |

`RiskEngine` aggregates debt + security + semantic into an overall `SeverityLevel`
(a CRITICAL security finding forces overall CRITICAL). `AuditOrchestrator` layers on LLM
enrichment, documentation findings, markdown report building, and optional translation.

### Request Pipeline — the webhook has two modes

`POST /webhook` branches on payload contents:

1. **Queued (async) mode** — payload has repo/pr_number/author/files but **no `code`**.
   Enqueues a job; `BackgroundWorker` later runs `RiskEngine.assess_resilient`. This is the
   only path where the **semantic engine is wired in**.
2. **Synchronous mode** — payload has `code`. Runs `RiskEngine.assess` +
   `AuditOrchestrator.run_full_review` inline and returns findings plus a markdown report in
   the HTTP response. If GitHub is configured, the report is also posted as a PR comment.

`assess` raises on engine failure; `assess_resilient` swallows failures and returns safe
defaults. All LLM/GitHub calls are failure-safe — on any error they fall back to static
strings or skip, never crashing the request.

## Project Structure

```
main.py                                     # FastAPI app entry point + background worker lifespan
pyproject.toml                              # Build system, pytest & mutmut config
requirements.txt                            # Pinned dependencies
mutation.Dockerfile                         # Containerized mutation testing
sentinel/
├── config/
│   └── settings.py                         # Feature flags + credentials (read via get_settings())
├── api/
│   ├── health_controller.py                # GET /health
│   └── webhook_controller.py               # POST /webhook (queued + synchronous modes)
├── application/
│   ├── audit_orchestrator.py               # LLM enrichment, doc findings, report building, translation
│   └── risk_engine.py                      # Multi-engine risk aggregator (assess / assess_resilient)
├── domain/
│   ├── entities/                           # Finding, PullRequest
│   ├── services/                           # debt / security / semantic / document services
│   └── value_objects/                      # SeverityLevel (LOW/MEDIUM/HIGH/CRITICAL)
├── infrastructure/
│   ├── llm/                                # LLMProvider abstraction, NIMProvider, safe LLMService
│   ├── github/                             # GitHub App JWT → installation token → PR comment
│   └── semantic/embedding_engine.py        # sklearn HashingVectorizer (128-dim, L2-norm)
├── monitoring/logger.py
├── workers/                                # BackgroundWorker loop + async FIFO JobQueue
└── tests/
    ├── fixtures/                           # Sample code files for debt analysis
    ├── hardening/                          # Architecture, concurrency, fault injection, adversarial inputs
    └── snapshots/                          # OpenAPI schema snapshots
```

## Setup

Python 3.13+ is required.

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1    # Windows
source .venv/bin/activate      # Linux/macOS
pip install -r requirements.txt
```

### Configuration (`.env`)

Settings are read only via `get_settings()`. Optional integrations are inert without their
credentials even when the flag is on.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_LLM` | `true` | Enable LLM enrichment (requires `NVIDIA_API_KEY`) |
| `ENABLE_GITHUB` | `true` | Enable PR comment posting (requires GitHub App vars) |
| `ENABLE_DOC_REVIEW` | `true` | Enable documentation findings |
| `ENABLE_TRANSLATION` | `false` | Append translated report sections (requires LLM) |
| `LLM_MAX_CALLS` | `1` | Per-request LLM call budget |
| `LLM_TIMEOUT` | `5.0` | LLM request timeout (seconds) |
| `NVIDIA_API_KEY` | — | Credential for the NVIDIA NIM provider |
| `GITHUB_APP_ID` / `GITHUB_INSTALLATION_ID` / `GITHUB_PRIVATE_KEY` | — | GitHub App auth (RS256) |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub API base URL |

The LLM provider is **NVIDIA NIM**: the `openai` SDK pointed at
`https://integrate.api.nvidia.com/v1` running `meta/llama-3.3-70b-instruct`.

## Run

```bash
uvicorn main:app --reload
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Root health message |
| `GET` | `/health` | Health status |
| `POST` | `/webhook` | Receives PR events for auditing (queued or synchronous) |

## Test

```bash
# Run all tests (config in pyproject.toml, testpaths = sentinel/tests)
pytest

# Single file / test
pytest sentinel/tests/test_risk_engine.py
pytest sentinel/tests/test_risk_engine.py::test_name -q

# Coverage (branch)
pytest --cov=sentinel --cov-branch --cov-report=term-missing

# Hardening tests only
pytest sentinel/tests/hardening/ -v
```

## Mutation Testing

Configuration is in `pyproject.toml` under `[tool.mutmut]`
(targets: `sentinel/domain/`, `sentinel/application/`, `sentinel/infrastructure/`).

```bash
# Local
mutmut run && mutmut results

# Docker (recommended)
docker build -f mutation.Dockerfile -t sentinel-mutmut .
docker run --rm sentinel-mutmut

# Docker with persistent results
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut results
```
