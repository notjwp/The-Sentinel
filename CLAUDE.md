# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.13+ is required. The virtualenv lives in `.venv/` (activate with `.venv\Scripts\Activate.ps1` on Windows).

Dependencies are **split**: `requirements.txt` is runtime-only; dev/test/lint/mutation tooling lives in `requirements-dev.txt` (which pulls in the runtime set via `-r requirements.txt`). Install the dev file for development — a plain `pip install -r requirements.txt` will not have `pytest`/`ruff`/`mutmut`.

```bash
# Install dev dependencies (tests, lint, mutation — includes runtime deps)
pip install -r requirements-dev.txt

# Run the API (dev)
uvicorn main:app --reload

# Run the full test suite (config in pyproject.toml, testpaths = sentinel/tests)
pytest

# Run a single test file / test
pytest sentinel/tests/test_risk_engine.py
pytest sentinel/tests/test_risk_engine.py::test_name -q

# Coverage (branch)
pytest --cov=sentinel --cov-branch --cov-report=term-missing

# Hardening tests only
pytest sentinel/tests/hardening/ -v

# Lint (advisory in CI; run locally before pushing)
ruff check sentinel main.py

# Mutation testing (targets domain/application/infrastructure)
mutmut run && mutmut results
# or containerized:
docker build -f mutation.Dockerfile -t sentinel-mutmut . && docker run --rm sentinel-mutmut

# Build & run the production container
docker build -t sentinel:dev . && docker run --rm -p 8000:8000 sentinel:dev
# or: docker compose up --build
```

## Architecture

Clean Architecture, strictly layered. The dependency rule (domain imports nothing from
FastAPI/sklearn/infrastructure) is **enforced by tests** in `sentinel/tests/hardening/test_architecture_extended.py` — breaking it fails CI, not just review.

- **domain/** — pure logic: `Finding`/`PullRequest` entities, `SeverityLevel` value object, and the three analysis services (`debt_service`, `security_service`, `semantic_service`). Semantic depends on an `EmbeddingPort` Protocol implemented in infrastructure (dependency inversion).
- **application/** — `RiskEngine` (aggregates the three engines) and `AuditOrchestrator` (LLM enrichment, document findings, markdown report building, translation). Ports for LLM/document services are declared as `Protocol`s here, not concrete imports.
- **infrastructure/** — `semantic/embedding_engine.py` (sklearn `HashingVectorizer`, 128-dim, L2-norm, deterministic/stateless), `llm/` (provider abstraction + NVIDIA NIM provider), `github/github_client.py` (GitHub App JWT → installation token → PR comment).
- **api/** — FastAPI controllers. **workers/** — async `JobQueue` (FIFO) + `BackgroundWorker` loop.

### Request pipeline — the webhook has two modes

`POST /webhook` branches on payload contents (`sentinel/api/webhook_controller.py`):

1. **Queued (async) mode** — payload has repo/pr_number/author/files but **no `code`**. Enqueues a job; `BackgroundWorker` later runs `RiskEngine.assess_resilient`. This is the only path where the **semantic engine is wired in** (the worker constructs `RiskEngine(semantic_service=...)`).
2. **Synchronous mode** — payload has `code`. Runs `RiskEngine.assess` + `AuditOrchestrator.run_full_review` inline and returns findings + a markdown report in the HTTP response. Semantic analysis is **not** wired here.

`RiskEngine.assess` raises on engine failure; `assess_resilient` swallows failures and returns safe defaults. Use the resilient variant for anything running in the background.

### Things that will surprise you

- **Manual dependency-override resolution.** `webhook_controller` does not rely solely on FastAPI's `Depends`; it manually reads `request.app.dependency_overrides` via `_resolve_override(...)`. Tests inject fakes through `app.dependency_overrides`, and the orchestrator itself is injected via `app.dependency_overrides[get_orchestrator]` in `main.py`. When adding a dependency to the sync path, wire it through this same mechanism or overrides won't take effect.
- **Report/translation logic lives in `AuditOrchestrator`**, not a separate service. `build_report`, `append_translations`, `enrich_findings_with_llm`, and `collect_document_findings` are all methods on the orchestrator. (Older `report_service.py`, `translation/translator.py`, `llm/openai_provider.py`, and the `use_cases/process_pull_request.py` module were removed/merged — ignore references to them in README.md / PROJECT_CONTEXT.md.)
- **LLM findings are matched by object identity.** `enrich_findings_with_llm` keys the provider response on `id(finding)`. `Finding` is a `frozen=True` dataclass, so enrichment produces new instances via `dataclasses.replace` rather than mutation.
- **`Finding` uses `finding_type` but exposes a `.type` property.** Report/serialization code compares `finding.type == "security"` / `"documentation"`.
- **LLM provider is NVIDIA NIM, not OpenAI.** `NIMProvider` uses the `openai` SDK pointed at `https://integrate.api.nvidia.com/v1` with `meta/llama-3.3-70b-instruct`. The credential env var is `NVIDIA_API_KEY`. All LLM/GitHub calls are failure-safe: on any error they fall back to static strings / skip, never crash the request.
- **Webhook signature verification** lives in `sentinel/api/webhook_security.py`, wired as a route dependency on `POST /webhook`. It enforces the `X-Hub-Signature-256` HMAC **only when `GITHUB_WEBHOOK_SECRET` is set** (so the test suite, which sets no secret, is unaffected); otherwise it's skipped. It compares as **bytes** (`hmac.compare_digest` on encoded values) so a malformed/non-ASCII header returns 401 rather than raising a 500.

### Feature flags (`sentinel/config/settings.py`, read from `.env`)

Read settings only via `get_settings()`. Defaults: `ENABLE_LLM=true`, `ENABLE_GITHUB=true`, `ENABLE_DOC_REVIEW=true`, `ENABLE_TRANSLATION=false`, `LLM_MAX_CALLS=1`, `LLM_TIMEOUT=5.0`. GitHub/LLM are effectively inert without their credentials even when flag is on (e.g. LLM requires `NVIDIA_API_KEY`; translation requires the LLM service to be present). `GITHUB_WEBHOOK_SECRET` (default unset) gates webhook signature verification — see the note above; in production set it, or the endpoint accepts unsigned requests (a startup warning fires when `ENABLE_GITHUB` is on without it).

## Build, container & CI

- **Dependency split** (see Commands): `requirements.txt` runtime-only; `requirements-dev.txt` dev tooling. The production `Dockerfile` installs only `requirements.txt` (so the image excludes pytest/mutmut/ruff); `mutation.Dockerfile` and CI install `requirements-dev.txt`.
- **Container:** `Dockerfile` builds a non-root image running `uvicorn main:app` on `:8000` with a `/health` HEALTHCHECK; `docker-compose.yml` runs it locally (expects a local `.env`).
- **CI:** `.github/workflows/ci.yml` has three jobs — `test` (pytest + branch coverage, `--cov-fail-under=85`; actual ≈88%), `docker` (image build, `needs: test`), and `lint` (Ruff, **advisory / `continue-on-error`** — the repo has ~30 unaddressed violations by design). Ruff config is in `pyproject.toml` (`select = E,F,I`).

## Project convention

`PROJECT_CONTEXT.md` (a hand-maintained working log) **has been removed**. The `main.py` module docstring and the `.githooks/pre-commit` reminder still reference it — those reminders are now vestigial. Treat the actual code as the source of truth.
