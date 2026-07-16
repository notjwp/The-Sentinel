
# The Sentinel

Event-driven FastAPI code auditing system: multi-engine risk analysis, LLM-enriched
explanations and fixes, GitHub-native PR comments **and check runs with inline annotations**,
documentation review against full file text, semantic duplicate detection against the real
repository, a durable Redis-backed job queue, Prometheus-format metrics, and optional report
translation.

## Architecture

Clean Architecture with strict layer boundaries. The dependency rule (the domain imports
nothing from FastAPI, sklearn, or infrastructure) is **enforced by tests** in
`sentinel/tests/hardening/test_architecture_extended.py`.

```
API (FastAPI) → Application (Orchestration) → Domain (Pure Logic) ← Infrastructure (sklearn / LLM / GitHub / Redis)
                         ↕
                   Workers (Async)
```

### Analysis Engines

| Engine | Layer | Purpose |
|---|---|---|
| **Technical Debt** | domain | Cyclomatic complexity, line count, nesting depth analysis |
| **Security** | domain | Regex-based vulnerability detection + OWASP classification: SQL injection, hardcoded secrets (OpenAI/AWS keys, api_key/password assignments), command injection, `eval`/`exec`, `os.system`, `subprocess shell=True` |
| **Semantic** | domain + infra | Duplicate code detection via embedding cosine similarity, compared against a **real corpus fetched from the PR's base branch** (chunked per top-level function/class) |
| **Documentation** | domain | Docs/docstring/comment checks; `.md`/`.txt` files are judged on their **full text** fetched from the PR head (patch text is only the fallback) |
| **LLM Enrichment** (optional) | infra | Explanation + fix suggestions for security findings via any OpenAI-compatible provider |

`RiskEngine` aggregates debt + security + semantic into an overall `SeverityLevel`
(a CRITICAL security finding forces overall CRITICAL; a semantic duplicate forces HIGH).
`AuditOrchestrator` layers on LLM enrichment, documentation findings, markdown report
building, check-run payloads, and optional translation.

### Request Pipeline — the webhook has two modes

`POST /webhook` branches on payload contents. Requests are verified against
`X-Hub-Signature-256` when `GITHUB_WEBHOOK_SECRET` is set, and re-sent deliveries are
deduplicated by `X-GitHub-Delivery` (HTTP 200 `{"status": "duplicate"}`, no re-processing).

1. **Queued (async) mode** — payload has repo/pr_number but **no `code`**. Enqueues a job
   (durable in Redis when `REDIS_URL` is set); the `BackgroundWorker` then fetches the PR's
   real diff from GitHub (`GitHubClient.get_pull_request_data`, paginated beyond 100 files),
   builds the semantic corpus from the base ref, assesses risk, LLM-enriches the findings,
   and posts back **both** a PR comment and a native **check run** — conclusion
   `failure`/`neutral`/`success` by severity, with annotations mapped to the exact file lines
   at the head commit (patch hunk arithmetic).
2. **Synchronous mode** — payload has `code`. Runs the same review inline and returns
   findings plus a markdown report in the HTTP response.

Both paths post comments via `GitHubClient.upsert_comment`, which tags the comment with a
hidden marker and **updates Sentinel's existing review in place** on re-runs instead of
stacking duplicates.

`assess` raises on engine failure; `assess_resilient` swallows failures and returns safe
defaults. All LLM/GitHub/Redis calls are failure-safe — on any error they fall back to static
strings, skip, or retry with exponential backoff, never crashing the request.

### Durability & Observability

- **Redis-backed queue** (opt-in via `REDIS_URL`): at-least-once delivery — jobs survive
  process crashes, an interrupted job is recovered from the processing list on restart, and
  webhook dedup state survives restarts too. Safe to re-run because posting is idempotent.
- **`GET /metrics`**: Prometheus text format, no client library — jobs processed, per-job
  duration, risk severities, webhook modes, LLM success/fallback, GitHub post outcomes,
  Redis errors, live queue depth.
- **Redis outages stay quiet**: one traceback per failing streak, a one-line summary at most
  every 60s, exponential retry backoff (capped at 5s), and a single "reconnected" line.

## Project Structure

```
main.py                                     # Composition root: queue selection, worker lifespan, routers
pyproject.toml                              # Build system, pytest & mutmut config
requirements.txt                            # Runtime dependencies (the production image installs only these)
requirements-dev.txt                        # Dev tooling (pytest/ruff/mutmut/fakeredis; includes runtime set)
Dockerfile / docker-compose.yml             # Non-root production image; compose runs app + AOF-persisted Redis
mutation.Dockerfile                         # Containerized mutation testing
sentinel/
├── config/
│   └── settings.py                         # Feature flags + credentials (read via get_settings())
├── api/
│   ├── health_controller.py                # GET /health
│   ├── metrics_controller.py               # GET /metrics (Prometheus text format)
│   ├── webhook_controller.py               # POST /webhook (queued + synchronous modes)
│   ├── webhook_security.py                 # X-Hub-Signature-256 verification (route dependency)
│   └── delivery_dedup.py                   # In-memory X-GitHub-Delivery dedup (Redis variant in infra)
├── application/
│   ├── audit_orchestrator.py               # Enrichment, doc findings, report + check payloads, translation
│   └── risk_engine.py                      # Multi-engine risk aggregator (assess / assess_resilient)
├── domain/
│   ├── entities/                           # Finding, PullRequest
│   ├── services/                           # debt / security / semantic / document services
│   └── value_objects/                      # SeverityLevel (LOW/MEDIUM/HIGH/CRITICAL)
├── infrastructure/
│   ├── llm/                                # LLMProvider abstraction + OpenAI-compatible client, safe LLMService
│   ├── github/                             # App JWT → installation token → diffs/corpus/comments/check runs
│   ├── redis/                              # RedisJobQueue (reliable queue) + RedisDeliveryDeduper
│   └── semantic/embedding_engine.py        # sklearn HashingVectorizer (128-dim, L2-norm)
├── monitoring/                             # logger + hand-rolled MetricsRegistry (stdlib only)
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
pip install -r requirements-dev.txt   # dev install (tests/lint); runtime-only: requirements.txt
```

### Configuration (`.env`)

Settings are read only via `get_settings()`. Optional integrations are inert without their
credentials even when the flag is on.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_LLM` | `true` | Enable LLM enrichment (requires a resolved `LLM_API_KEY`) |
| `ENABLE_GITHUB` | `true` | Enable PR comment/check posting (requires GitHub App vars) |
| `ENABLE_CHECKS` | `true` | Post native check runs (app needs **Checks: Read & write**) |
| `ENABLE_DOC_REVIEW` | `true` | Enable documentation findings |
| `ENABLE_TRANSLATION` | `false` | Append translated report sections (needs LLM **and** budget: set `LLM_MAX_CALLS≥3` — 1 enrichment + 1 per language) |
| `LLM_MAX_CALLS` | `1` | Per-request LLM call budget (shared by enrichment + translation) |
| `LLM_TIMEOUT` | `5.0` | LLM request timeout (seconds) |
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | LLM endpoint (any OpenAI-compatible provider) |
| `LLM_MODEL` | `deepseek-ai/deepseek-v4-flash` | Model id at that endpoint |
| `LLM_API_KEY` | — | LLM credential; **falls back to `NVIDIA_API_KEY`** when unset |
| `NVIDIA_API_KEY` | — | Back-compat credential / fallback for `LLM_API_KEY` |
| `GITHUB_APP_ID` / `GITHUB_INSTALLATION_ID` / `GITHUB_PRIVATE_KEY` | — | GitHub App auth (RS256) |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub API base URL |
| `GITHUB_WEBHOOK_SECRET` | — | When set, `POST /webhook` enforces `X-Hub-Signature-256`; set it before public exposure |
| `REDIS_URL` | — | e.g. `redis://localhost:6379/0` — durable queue + dedup; unset = in-memory (lost on restart) |

The LLM provider is **any OpenAI-compatible endpoint** (the `openai` SDK pointed at
`LLM_BASE_URL`). Defaults target NVIDIA NIM; switch to Groq, Gemini, or a local Ollama by
editing `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` — no code change. See
[`.env.example`](.env.example) for ready-to-paste provider presets.

### GitHub App setup

Create a GitHub App (Settings → Developer settings → GitHub Apps) with **Repository
permissions**: Pull requests (Read & write), Issues (Read & write), Checks (Read & write),
Contents (Read-only); webhook can stay disabled for manual triggering. Install it on the
target repo, then set `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` (from the installation URL),
and `GITHUB_PRIVATE_KEY` (the downloaded PEM, single line with `\n` separators is accepted).

Trigger a review manually — Sentinel only makes *outbound* calls, so no tunnel is needed:

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/name", "pr_number": 1}'
```

## Run

```bash
# Dev
uvicorn main:app --reload

# Production-shaped: app + AOF-persisted Redis, REDIS_URL pre-wired (expects a local .env)
docker compose up --build
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Root health message |
| `GET` | `/health` | Health status |
| `GET` | `/metrics` | Prometheus-format metrics (counters, durations, live queue depth) |
| `POST` | `/webhook` | Receives PR events for auditing (queued or synchronous) |

## Test

```bash
# Run all tests (config in pyproject.toml, testpaths = sentinel/tests)
pytest

# Single file / test
pytest sentinel/tests/test_risk_engine.py
pytest sentinel/tests/test_risk_engine.py::test_name -q

# Coverage (branch; CI enforces --cov-fail-under=85)
pytest --cov=sentinel --cov-branch --cov-report=term-missing

# Hardening tests only
pytest sentinel/tests/hardening/ -v

# Lint (enforced in CI)
ruff check sentinel main.py
```

Redis-backed tests need no server — they run against `fakeredis` injected through the
`client=` seam.

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
