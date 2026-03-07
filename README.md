# The Sentinel

Event-driven FastAPI code auditing system with multi-engine risk analysis.

## Architecture

Clean Architecture with strict layer boundaries:

```
API (FastAPI) → Application (Orchestration) → Domain (Pure Logic) ← Infrastructure (sklearn)
                         ↕
                   Workers (Async)
```

### Analysis Engines

| Engine | Purpose |
|---|---|
| **Technical Debt** | Cyclomatic complexity, line count, nesting depth analysis |
| **Security** | Regex-based vulnerability pattern detection (SQL injection, XSS, hardcoded secrets, etc.) |
| **Semantic** | Duplicate/similar code detection via embedding cosine similarity |

### Request Pipeline

```
POST /webhook → AuditOrchestrator → JobQueue → BackgroundWorker
    → ProcessPullRequestUseCase → RiskEngine → ReportService
```

## Project Structure

```
main.py
pyproject.toml
requirements.txt
mutation.Dockerfile
sentinel/
├── api/
│   ├── health_controller.py             # GET /health
│   └── webhook_controller.py            # POST /webhook
├── application/
│   ├── audit_orchestrator.py            # Enqueues jobs from webhook events
│   ├── report_service.py                # Formats risk assessment reports
│   ├── risk_engine.py                   # Multi-engine risk aggregator
│   └── use_cases/
│       └── process_pull_request.py      # PR processing use case
├── domain/
│   ├── entities/
│   │   ├── finding.py                   # Finding dataclass (security + semantic)
│   │   └── pull_request.py              # PullRequest entity
│   ├── services/
│   │   ├── debt_service.py              # Technical debt scoring
│   │   ├── security_service.py          # Security vulnerability scanning
│   │   └── semantic_service.py          # Semantic similarity detection
│   └── value_objects/
│       └── severity_level.py            # SeverityLevel enum
├── infrastructure/
│   └── semantic/
│       └── embedding_engine.py          # sklearn HashingVectorizer (128-dim)
├── tests/                               # 239 tests, 99% branch coverage
│   ├── fixtures/                        # Sample code files for debt analysis
│   ├── hardening/                       # 9 production-grade hardening modules
│   └── snapshots/                       # OpenAPI schema snapshots
└── workers/
    ├── background_worker.py             # Async worker loop
    └── job_queue.py                     # Async FIFO job queue
```

## Setup

```bash
python -m venv venv
venv\Scripts\Activate.ps1   # Windows
source venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --reload
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check — confirms the service is running |
| `GET` | `/health` | Detailed health status |
| `POST` | `/webhook` | Receives PR events for auditing |

## Test

```bash
# Run all tests
pytest -x

# Run with coverage
pytest --cov=sentinel --cov-branch --cov-report=term-missing

# Run hardening tests only
pytest sentinel/tests/hardening/ -v
```

### Test Suite Summary

| Category | Modules | Tests | Purpose |
|---|---|---|---|
| **Core** | 8 | 135 | Unit & integration tests for all engines and API |
| **Hardening** | 9 | 104 | Architecture, concurrency, fault injection, performance, adversarial inputs |
| **Total** | 17 | 239 | ~11s runtime, 99% branch coverage |

## Mutation Testing

Mutation testing can be run locally or via the provided Dockerfile.

### Using Docker (Recommended)
```bash
docker build -f mutation.Dockerfile -t sentinel-mutmut .
docker run --rm sentinel-mutmut
```

To persist results across container runs, mount a volume:
```bash
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut
docker run --rm -v sentinel-mutmut-data:/app sentinel-mutmut results
```

### Local Workstation
```bash
pip install mutmut
mutmut run
mutmut results
```

Configuration is in `pyproject.toml` under `[tool.mutmut]`.
Targets: `sentinel/domain/`, `sentinel/application/`, `sentinel/infrastructure/`.
