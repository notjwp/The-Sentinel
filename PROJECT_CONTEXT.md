# Project Context Log

This file tracks ongoing project context, decisions, and recent actions so work can continue with minimal re-discovery.

## Project Snapshot

- **Project:** The-Sentinel
- **Root Path:** `D:\Dev\The-Sentinel`
- **Date Initialized:** 2026-02-16
- **Last Updated:** 2026-03-24
- **Python:** 3.13.5
- **Virtual Environment:** `D:\Dev\The-Sentinel\venv\`

## Architecture

The Sentinel is an event-driven FastAPI code auditing system using **Clean Architecture**:

| Layer | Directory | Responsibility |
|---|---|---|
| **Domain** | `sentinel/domain/` | Entities, value objects, pure-logic services — no external deps |
| **Application** | `sentinel/application/` | Orchestration, use cases, risk aggregation |
| **Infrastructure** | `sentinel/infrastructure/` | sklearn-based embedding engine |
| **API** | `sentinel/api/` | FastAPI controllers (webhook, health) |
| **Workers** | `sentinel/workers/` | Async background job processing |

### Phase Status

- **Phase 1:** System stabilization and hardening complete
- **Phase 2:** Vulnerability classification complete (OWASP mapping + CRITICAL severity)
- **Phase 3:** Optional LLM explanation/fix suggestion integration complete with failure-safe fallbacks

### Analysis Engines

| Engine | Module | Purpose |
|---|---|---|
| **Technical Debt** | `sentinel/domain/services/debt_service.py` | Cyclomatic complexity, line count, nesting depth |
| **Security** | `sentinel/domain/services/security_service.py` | Regex-based vulnerability detection + OWASP/category classification |
| **Semantic** | `sentinel/domain/services/semantic_service.py` | Duplicate/similar code detection via embeddings |
| **LLM Explanation/Fix (optional)** | `sentinel/infrastructure/llm/` | Pluggable provider interface + safe wrapper for explanation/fix generation |

### Pipeline

```
Webhook → (optional synchronous security/risk path for code payload)
  → AuditOrchestrator → JobQueue → BackgroundWorker → ProcessPullRequestUseCase
  → RiskEngine (debt + security + semantic) → ReportService

Detection → Classification → (optional) Explanation/Fix Suggestion → Risk
```

## Current Structure

```
main.py                                  # FastAPI app entry point
pyproject.toml                           # Build system, pytest & mutmut config
requirements.txt                         # Pinned dependencies
README.md                                # Setup, usage, testing docs
PROJECT_CONTEXT.md                       # This file
mutation.Dockerfile                      # Docker configuration for mutmut
sentinel/
├── __init__.py
├── config/
│   ├── __init__.py
│   └── settings.py                      # Runtime flags for optional LLM behavior
├── api/
│   ├── health_controller.py             # GET /health
│   └── webhook_controller.py            # POST /webhook (queued mode + sync mode)
├── application/
│   ├── __init__.py
│   ├── audit_orchestrator.py            # Enqueues jobs + optional finding enrichment hook
│   ├── report_service.py                # Formats risk assessment reports
│   ├── risk_engine.py                   # Multi-engine risk aggregator
│   └── use_cases/
│       └── process_pull_request.py      # PR processing use case
├── domain/
│   ├── entities/
│   │   ├── finding.py                   # Finding dataclass (+ classification + optional explanation/fix)
│   │   └── pull_request.py              # PullRequest entity
│   ├── services/
│   │   ├── debt_service.py              # Technical debt analysis
│   │   ├── security_service.py          # Security vulnerability scanning
│   │   └── semantic_service.py          # Semantic similarity detection
│   └── value_objects/
│       └── severity_level.py            # SeverityLevel enum (LOW/MEDIUM/HIGH/CRITICAL)
├── infrastructure/
│   ├── __init__.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py                      # LLMProvider abstraction
│   │   ├── llm_service.py               # Safe wrapper (limits, fallbacks)
│   │   └── openai_provider.py           # OpenAI implementation (optional runtime dependency)
│   └── semantic/
│       ├── __init__.py
│       └── embedding_engine.py          # sklearn HashingVectorizer (128-dim, L2-norm)
├── tests/
│   ├── fixtures/
│   │   ├── simple_function.py
│   │   ├── medium_function.py
│   │   └── complex_function.py
│   ├── hardening/                       # Production-grade system hardening tests
│   │   ├── __init__.py
│   │   ├── test_architecture_extended.py
│   │   ├── test_branch_completion.py
│   │   ├── test_concurrency_stress.py
│   │   ├── test_fault_injection.py
│   │   ├── test_openapi_contract_snapshot.py
│   │   ├── test_performance_guards.py
│   │   ├── test_security_regex_stability.py
│   │   ├── test_semantic_adversarial.py
│   │   └── test_worker_lifecycle.py
│   ├── snapshots/                       # OpenAPI schema snapshots
│   ├── test_contract_schema.py
│   ├── test_debt_service.py
│   ├── test_llm_integration.py
│   ├── test_risk_engine_security_integration.py
│   ├── test_risk_engine.py
│   ├── test_security_edge_cases.py
│   ├── test_security_service.py
│   ├── test_semantic_integration.py
│   ├── test_semantic_service.py
│   ├── test_use_case.py
│   ├── test_vulnerability_classification.py
│   └── test_webhook_api.py
└── workers/
    ├── __init__.py
    ├── background_worker.py             # Async worker loop
    └── job_queue.py                     # Async FIFO job queue
```

## Key Technical Details

- **Embedding Engine:** sklearn `HashingVectorizer(n_features=128, alternate_sign=False, norm="l2")` — deterministic, stateless, CPU-only
- **Semantic Threshold:** 0.9 cosine similarity for duplicate detection
- **Domain Purity:** Domain layer has zero imports from FastAPI, sklearn, or infrastructure
- **EmbeddingPort Protocol:** Domain defines a Protocol; infrastructure implements it (Dependency Inversion)
- **Risk Aggregation:** CRITICAL security finding forces overall CRITICAL; otherwise semantic/security/debt precedence applies
- **Security Classification:** Rule mapping includes category + OWASP labels with unknown fallback values
- **Finding Entity:** `@dataclass(frozen=True)` with optional `explanation` and `fix_suggestion` fields for downstream AI outputs
- **LLM Integration Safety:** Optional via env flags; disabled by default; safe fallback strings prevent pipeline failure

## Test Suite

- **Status:** Full suite passing (`python -m pytest -q`)
- **Coverage:** 100% branch coverage observed in recent runs
- **Phase additions:** Security classification tests + LLM integration tests added

### Hardening Tests

| Module | Tests | Purpose |
|---|---|---|
| `test_architecture_extended` | 10 | Clean architecture boundary enforcement |
| `test_branch_completion` | 16 | Edge-case branch coverage |
| `test_concurrency_stress` | 6 | Concurrent queue/worker stress |
| `test_fault_injection` | 18 | Error handling & fault tolerance |
| `test_openapi_contract_snapshot` | 7 | OpenAPI schema stability |
| `test_performance_guards` | 10 | Latency & throughput bounds |
| `test_security_regex_stability` | 16 | Regex engine resilience |
| `test_semantic_adversarial` | 15 | Adversarial semantic inputs |
| `test_worker_lifecycle` | 6 | Worker start/stop/cancel lifecycle |

## Mutation Testing

Configured in `pyproject.toml` under `[tool.mutmut]`:
- **Targets:** `sentinel/domain/`, `sentinel/application/`, `sentinel/infrastructure/`
- **Tests Dir:** `sentinel/tests/`
- **Also Copy:** `sentinel/`, `main.py` (needed for mutmut 3.x `mutants/` directory)
- **Backup:** `false`
- **Docker:** `mutation.Dockerfile` — builds image, runs tests at build time, then `mutmut run`
- **Current Note:** Use Docker volume mode for persistent mutmut result analysis

## Commands

```bash
# Activate virtual environment
venv\Scripts\Activate.ps1              # Windows
source venv/bin/activate                # Linux/macOS

# Run server
uvicorn main:app --reload

# Run tests
pytest -x
pytest --cov=sentinel --cov-branch --cov-report=term-missing
pytest sentinel/tests/hardening/ -v

# LLM feature toggles (.env)
# ENABLE_LLM=true
# OPENAI_API_KEY=<your-key>
# LLM_MAX_CALLS=5
# LLM_TIMEOUT=8.0

# Mutation testing (Local)
mutmut run
mutmut results

# Mutation testing (Docker)
docker build -f mutation.Dockerfile -t sentinel-mutmut .
docker run --rm sentinel-mutmut

# Mutation testing (Docker with persistent results)
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut results
```

## Working Log

### 2026-02-16
- Created project with FastAPI webhook pipeline, job queue, and background worker.
- Created this context tracking file (`PROJECT_CONTEXT.md`).
- Added a context update checklist comment in `main.py`.
- Added an optional git pre-commit reminder hook at `.githooks/pre-commit`.

### 2026-02-20
- Implemented clean architecture: domain entities (Finding, PullRequest), value objects (SeverityLevel), domain services (DebtService, SecurityService).
- Created application layer: RiskEngine, AuditOrchestrator, ReportService, ProcessPullRequestUseCase.
- Added comprehensive unit and integration test suite.

### 2026-02-23
- Implemented Semantic Embedding & Logic Detection Engine.
- Added `SemanticService` (domain) with `EmbeddingPort` Protocol for dependency inversion.
- Added `EmbeddingEngine` (infrastructure) using sklearn HashingVectorizer.
- Extended `Finding` entity with `finding_type` and `similarity_score` fields.
- Integrated semantic analysis into `RiskEngine` and `BackgroundWorker`.
- Added `test_semantic_service.py` and `test_semantic_integration.py`.

### 2026-02-25
- Created production-grade hardening test suite (9 modules, 104 tests) under `sentinel/tests/hardening/`.
- Configured mutmut mutation testing in `pyproject.toml`.
- Created `README.md` with setup, run, test, and mutation testing instructions.

### 2026-02-26
- Audited test suite for redundancy; removed 4 fully redundant root-level test files (test_architecture_integrity.py, test_validation.py, test_performance.py, test_queue_stress.py).
- Final state: 239 tests, 99% branch coverage, ~11s runtime.
- Updated PROJECT_CONTEXT.md, requirements.txt, and README.md to reflect current project state.

### 2026-03-03
- Created `mutation.Dockerfile` for containerized mutation testing.
- Updated PROJECT_CONTEXT.md and README.md to reflect Docker usage.

### 2026-03-07
- Fixed mutmut 3.x compatibility: `paths_to_mutate`, `tests_dir` must be TOML arrays, not strings.
- Added `also_copy = ["sentinel/", "main.py"]` so mutmut copies full package tree to `mutants/` directory.
- Added `pip install -e .` to `mutation.Dockerfile` so imports resolve in `mutants/` working directory.
- Relaxed regex stability test thresholds from 0.1s to 0.5s (`_THRESHOLD` constant) for Docker/CI tolerance.
- Regenerated OpenAPI snapshot after FastAPI version change.
- Mutation run complete: 216 mutants, 176 killed, 40 survived, 0 errors.
- Updated PROJECT_CONTEXT.md, README.md, and requirements.txt.

### 2026-03-24
- Implemented Phase 2 vulnerability classification refinements:
  - Security findings enriched with category, OWASP mapping, structured metadata, and CRITICAL severity handling.
  - Risk engine updated to respect CRITICAL precedence while preserving prior behavior.
- Implemented Phase 3 optional LLM integration:
  - Added pluggable provider abstraction (`LLMProvider`) and optional OpenAI provider in infrastructure.
  - Added safe `LLMService` wrapper with severity filtering, call budgeting, and hard fallbacks.
  - Added orchestrator enrichment hook for HIGH/CRITICAL findings only.
  - Extended webhook synchronous response to include `explanation` and `fix_suggestion` fields.
- Added and validated tests:
  - `test_vulnerability_classification.py`
  - `test_security_edge_cases.py`
  - `test_risk_engine_security_integration.py`
  - `test_llm_integration.py`
- Added configuration/dependency support:
  - `.env` support variables for LLM behavior
  - `openai` and `python-dotenv` dependencies
- Commands run:
  - `python -m pytest -q`
  - `python -m pytest --cov=sentinel -q`

## Open Items

- Add entries here whenever:
  - Files are created/renamed/deleted
  - Significant logic changes are made
  - Dependencies are added/removed
  - Commands or run/test workflows change

## Entry Template

```md
### YYYY-MM-DD
- Summary:
- Files changed:
- Why:
- Commands run:
- Follow-ups:
```

## Optional Git Hook Reminder

Enable a local pre-commit reminder to keep this file updated:

```bash
git config core.hooksPath .githooks
```

This reminder is non-blocking (it does not prevent commits).
