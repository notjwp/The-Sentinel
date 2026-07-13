# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.13+ is required. The virtualenv lives in `.venv/` (activate with `.venv\Scripts\Activate.ps1` on Windows).

```bash
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

# Mutation testing (targets domain/application/infrastructure)
mutmut run && mutmut results
# or containerized:
docker build -f mutation.Dockerfile -t sentinel-mutmut . && docker run --rm sentinel-mutmut
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

### Feature flags (`sentinel/config/settings.py`, read from `.env`)

Read settings only via `get_settings()`. Defaults: `ENABLE_LLM=true`, `ENABLE_GITHUB=true`, `ENABLE_DOC_REVIEW=true`, `ENABLE_TRANSLATION=false`, `LLM_MAX_CALLS=1`, `LLM_TIMEOUT=5.0`. GitHub/LLM are effectively inert without their credentials even when flag is on (e.g. LLM requires `NVIDIA_API_KEY`; translation requires the LLM service to be present).

## Project convention

`PROJECT_CONTEXT.md` is a hand-maintained working log; the `main.py` module docstring and the `.githooks/pre-commit` reminder both ask that it get a dated entry after meaningful code/config/dependency changes. It is currently **stale relative to the working tree** — treat the actual code as source of truth, not its structure diagrams.
