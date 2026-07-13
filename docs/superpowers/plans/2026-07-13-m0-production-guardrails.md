# M0 — Production Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make The Sentinel safe to expose publicly and reproducible to build/test — GitHub webhook signature verification, a production container, and a CI pipeline — without changing any existing behavior.

**Architecture:** Additive and opt-in. A new `webhook_security` module provides HMAC verification wired to the `/webhook` route as a FastAPI dependency that *only enforces when a secret is configured* (so the existing 362 tests, which set no secret, stay green). Containerization and CI are pure additions (Dockerfile, compose, GitHub Actions) that touch no runtime code. Nothing in `domain/`, `application/`, or the engines changes.

**Tech Stack:** Python 3.13, FastAPI, `hmac`/`hashlib` (stdlib), Docker, GitHub Actions, Ruff.

## Global Constraints

- **Python 3.13+** — image base `python:3.13-slim`; CI matrix pinned to `3.13`.
- **Non-breaking is a hard requirement.** All existing tests must still pass unchanged; new behavior is gated on configuration that defaults to today's behavior.
- **Config only via `get_settings()`** (`sentinel/config/settings.py`). Never read `os.getenv` outside that module.
- **Follow existing patterns:** module-level `logger = get_logger(__name__)`; failure-safe edges; `Settings` is a `frozen=True` dataclass populated with keyword args in `get_settings()`.
- **Secret handling:** never log secret values; never commit secrets; `.env` stays git-ignored.
- **Frequent commits:** one commit per task minimum, at each green step where noted.

## Design decisions & assumptions

1. **Signature enforcement is "on when a secret exists."** If `GITHUB_WEBHOOK_SECRET` is unset, verification is skipped (preserves current tests + local dev). Production **must** set the secret; a loud startup warning fires if GitHub is enabled without one. A later milestone can flip this to fail-closed once all callers are signed.
2. **The synchronous `code`-in-body path is left intact in M0.** Once signatures are enforced it is only reachable by the secret holder, and real GitHub webhooks never carry `code`, so it is effectively unreachable by GitHub traffic. It gets refactored/removed in M1 when the real fetch→analyze→Check-Run loop replaces it. Not gating it here keeps the controller from growing another branch.
3. **Coverage gate starts at `--cov-fail-under=85`** to avoid a flaky first CI run; raise it after observing the real number locally.
4. **Ruff runs advisory (`continue-on-error`) in M0** — the codebase has never been linted, so it should not block until violations are triaged.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `sentinel/config/settings.py` | Add `GITHUB_WEBHOOK_SECRET` field + parsing | Modify |
| `sentinel/api/webhook_security.py` | HMAC compute/verify + FastAPI dependency | **Create** |
| `sentinel/api/webhook_controller.py` | Attach verification dependency to the route | Modify (1 line) |
| `main.py` | Startup warning if GitHub on but no webhook secret | Modify |
| `sentinel/tests/test_webhook_signature.py` | Tests for verification | **Create** |
| `Dockerfile` | Production runtime image | **Create** |
| `.dockerignore` | Keep build context lean | **Create** |
| `docker-compose.yml` | One-command local run | **Create** |
| `.github/workflows/ci.yml` | Test + coverage + image build (+ advisory lint) | **Create** |
| `pyproject.toml` | Ruff config | Modify |

---

## Task 1: GitHub webhook signature verification

**Files:**
- Modify: `sentinel/config/settings.py`
- Create: `sentinel/api/webhook_security.py`
- Modify: `sentinel/api/webhook_controller.py` (route decorator only)
- Modify: `main.py`
- Test: `sentinel/tests/test_webhook_signature.py`

**Interfaces:**
- Produces: `compute_signature(secret: str, body: bytes) -> str` (returns `"sha256=<hex>"`); `is_valid_signature(secret: str, body: bytes, signature_header: str | None) -> bool`; `async verify_webhook_signature(request: Request) -> None` (FastAPI dependency, raises `HTTPException(401)` on failure, returns `None` when no secret configured).
- Consumes: `get_settings().GITHUB_WEBHOOK_SECRET: str | None` (new field).

- [ ] **Step 1: Add the setting field and parsing**

In `sentinel/config/settings.py`, add the field to the `Settings` dataclass (after `GITHUB_API_BASE_URL`):

```python
    GITHUB_API_BASE_URL: str
    GITHUB_WEBHOOK_SECRET: str | None
```

In `get_settings()`, add parsing next to the other GitHub vars:

```python
    raw_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    github_webhook_secret = raw_webhook_secret.strip() if raw_webhook_secret is not None else None
    if github_webhook_secret == "":
        github_webhook_secret = None
```

And pass it in the `return Settings(...)` call:

```python
        GITHUB_API_BASE_URL=github_api_base_url,
        GITHUB_WEBHOOK_SECRET=github_webhook_secret,
    )
```

- [ ] **Step 2: Write the failing test**

Create `sentinel/tests/test_webhook_signature.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.api.webhook_security import compute_signature, is_valid_signature


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.received.append(payload)


def _client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_orchestrator] = lambda: _DummyOrchestrator()
    app.include_router(webhook_router)
    app.include_router(health_router)
    return TestClient(app)


def test_is_valid_signature_roundtrip():
    body = b'{"repo":"r","pr_number":1}'
    sig = compute_signature("s3cret", body)
    assert is_valid_signature("s3cret", body, sig) is True
    assert is_valid_signature("s3cret", body, "sha256=deadbeef") is False
    assert is_valid_signature("s3cret", body, None) is False
    assert is_valid_signature("", body, sig) is False


def test_webhook_without_secret_skips_verification(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    resp = _client().post("/webhook", json={"repo": "r", "pr_number": 1})
    assert resp.status_code == 200


def test_webhook_with_secret_rejects_missing_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    resp = _client().post("/webhook", json={"repo": "r", "pr_number": 1})
    assert resp.status_code == 401


def test_webhook_with_secret_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    resp = _client().post(
        "/webhook",
        content=b'{"repo":"r","pr_number":1}',
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert resp.status_code == 401


def test_webhook_with_secret_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = b'{"repo":"r","pr_number":1}'
    sig = compute_signature("s3cret", body)
    resp = _client().post(
        "/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert resp.status_code == 200
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest sentinel/tests/test_webhook_signature.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.api.webhook_security'`.

- [ ] **Step 4: Implement the verification module**

Create `sentinel/api/webhook_security.py`:

```python
import hashlib
import hmac

from fastapi import HTTPException, Request

from sentinel.config.settings import get_settings
from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)

_SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def is_valid_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret:
        return False
    if not signature_header or not signature_header.startswith(_PREFIX):
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature_header)


async def verify_webhook_signature(request: Request) -> None:
    """FastAPI dependency. Enforces HMAC only when a secret is configured.

    No secret configured -> verification is skipped (dev/test/local). Production
    must set GITHUB_WEBHOOK_SECRET; see the startup warning in main.py.
    """
    secret = get_settings().GITHUB_WEBHOOK_SECRET
    if not secret:
        return
    body = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER)
    if not is_valid_signature(secret, body, signature):
        logger.warning("Rejected webhook: invalid or missing %s", _SIGNATURE_HEADER)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
```

- [ ] **Step 5: Wire the dependency into the route**

In `sentinel/api/webhook_controller.py`, add the import near the other local imports:

```python
from sentinel.api.webhook_security import verify_webhook_signature
```

Change the route decorator (currently `@router.post("/webhook")`) to attach the dependency:

```python
@router.post("/webhook", dependencies=[Depends(verify_webhook_signature)])
```

`Depends` is already imported in this file. Do not change the handler body.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest sentinel/tests/test_webhook_signature.py -q`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the full suite to confirm nothing broke**

Run: `pytest -q`
Expected: PASS — all pre-existing tests plus the 5 new ones. (Existing webhook tests set no secret, so verification is skipped for them.)

- [ ] **Step 8: Add the production misconfiguration warning**

In `main.py`, inside the `lifespan` function, before `yield`, add:

```python
    settings = get_settings()
    if settings.ENABLE_GITHUB and not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning(
            "ENABLE_GITHUB is on but GITHUB_WEBHOOK_SECRET is unset — "
            "webhook signature verification is DISABLED. Set it before public exposure."
        )
```

Add the import at the top of `main.py` if not present:

```python
from sentinel.config.settings import get_settings
```

- [ ] **Step 9: Run the full suite again**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add sentinel/config/settings.py sentinel/api/webhook_security.py sentinel/api/webhook_controller.py main.py sentinel/tests/test_webhook_signature.py
git commit -m "feat(security): verify GitHub webhook HMAC signatures when secret is configured"
```

---

## Task 2: Production container image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`

**Interfaces:**
- Produces: an image running `uvicorn main:app` on port `8000` as a non-root user, with a `/health` HEALTHCHECK.

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.gitignore
.venv
venv
env
__pycache__
*.py[cod]
.pytest_cache
.mutmut-cache
mutants
.coverage
.coverage.*
htmlcov
*.egg-info
.mypy_cache
.ruff_cache
.env
.env.*
docs
sentinel/tests
mutation.Dockerfile
pytest_output.txt
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer (cached until requirements change).
COPY requirements.txt pyproject.toml ./
RUN pip install -r requirements.txt

# Application source.
COPY main.py ./
COPY sentinel/ ./sentinel/
RUN pip install -e . --no-deps

# Drop privileges.
RUN useradd --create-home --uid 10001 sentinel
USER sentinel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Create `docker-compose.yml`**

```yaml
services:
  sentinel:
    build: .
    image: sentinel:dev
    ports:
      - "8000:8000"
    # Requires a local .env (git-ignored). Remove this block to run with defaults.
    env_file:
      - .env
    restart: unless-stopped
```

- [ ] **Step 4: Build the image**

Run: `docker build -t sentinel:dev .`
Expected: build completes; final line `naming to docker.io/library/sentinel:dev` (or equivalent success).

> If Docker is unavailable in this environment, mark this step blocked and verify the Dockerfile via a reviewer instead — do not fake the result.

- [ ] **Step 5: Boot the container and hit the health endpoint**

Run:
```bash
docker run -d --rm -p 8000:8000 --name sentinel_smoke sentinel:dev
sleep 3
curl -fsS http://localhost:8000/health
docker stop sentinel_smoke
```
Expected: `curl` prints `{"status":"ok"}`.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml
git commit -m "build: add production Dockerfile, .dockerignore, and docker-compose"
```

---

## Task 3: Continuous integration pipeline

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a `test` job (pytest + branch coverage gate) and a `docker` job (image build) triggered on push to `main` and on all pull requests.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [ main ]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    env:
      ENABLE_LLM: "false"
      ENABLE_GITHUB: "false"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -e .
      - name: Run tests with coverage
        run: pytest --cov=sentinel --cov-branch --cov-report=term-missing --cov-fail-under=85

  docker:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker build -t sentinel:ci .
```

- [ ] **Step 2: Validate the workflow locally**

Run: `pytest --cov=sentinel --cov-branch --cov-report=term-missing --cov-fail-under=85`
Expected: PASS and coverage ≥ 85%. If coverage is below 85%, lower the `--cov-fail-under` value in the workflow to just under the observed number and note it; do not delete the gate.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions pipeline (pytest, branch coverage, docker build)"
```

- [ ] **Step 4: Confirm the run on GitHub**

After pushing the branch and opening a PR, confirm both `test` and `docker` jobs pass in the Actions tab. Do not claim CI is green until you have seen the green check.

---

## Task 4: Advisory lint (Ruff)

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml` (add a `lint` job)

**Interfaces:**
- Produces: a non-blocking `lint` job so violations are surfaced without breaking CI while the backlog is triaged.

- [ ] **Step 1: Add Ruff configuration to `pyproject.toml`**

Append:

```toml
[tool.ruff]
target-version = "py313"
line-length = 120
extend-exclude = [".venv", "mutants", "sentinel/tests/snapshots"]

[tool.ruff.lint]
select = ["E", "F", "I"]
ignore = ["E501"]
```

- [ ] **Step 2: Run Ruff locally to see the current state**

Run: `pip install ruff && ruff check sentinel main.py`
Expected: prints any violations (informational). Do **not** fix them in this task — that is a separate cleanup pass (Low-priority roadmap item).

- [ ] **Step 3: Add the advisory lint job to `.github/workflows/ci.yml`**

Add this job under `jobs:`:

```yaml
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install ruff
      - name: Ruff (advisory)
        run: ruff check sentinel main.py
        continue-on-error: true
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml
git commit -m "ci: add advisory Ruff lint job and configuration"
```

---

## Verification (end-to-end)

1. **Full suite:** `pytest -q` → all existing tests + 5 new signature tests pass.
2. **No secret = unchanged behavior:** with `GITHUB_WEBHOOK_SECRET` unset, `POST /webhook` behaves exactly as before (proven by the untouched existing webhook tests).
3. **Secret set = enforced:** unsigned/badly-signed requests get `401`; a request signed with the secret over the exact body gets through (proven by `test_webhook_signature.py`).
4. **Container:** `docker build .` succeeds; `docker run` + `curl /health` returns `{"status":"ok"}`.
5. **CI:** `test` and `docker` jobs green on the PR; `lint` runs advisory.
6. **Startup guard:** launching with `ENABLE_GITHUB=true` and no secret logs the warning (observe stdout of `uvicorn main:app`).

## Self-review notes

- **Spec coverage:** signature verification (Task 1), container (Task 2), CI test+coverage+build (Task 3), lint (Task 4) — all four M0 items from the readiness review are covered. The sync-path gate was intentionally deferred to M1 (see Design decision #2).
- **Type consistency:** `compute_signature`/`is_valid_signature`/`verify_webhook_signature` signatures are identical between the interface block, the implementation, and the tests.
- **Non-breaking check:** the only runtime edits are a new-defaulted settings field, a new module, a one-line route decorator, and a startup log line — none change behavior when `GITHUB_WEBHOOK_SECRET` is unset.

## What M1 builds next (context, not part of M0)

The real GitHub-App loop: worker receives `{installation_id, repo, pr, sha}`, fetches the PR's changed files via the GitHub API, runs the existing `RiskEngine` on real content (replacing the hard-coded `EXISTING_CODE_LIST`), and posts a **Check Run**. M0's signature verification and container are prerequisites for exposing that loop to real GitHub deliveries.
