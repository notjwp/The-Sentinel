# M8 — Tier 1 Production Correctness

## Context

M0–M7 shipped a complete review pipeline (478 tests green, ruff clean, 91% branch coverage), but a
code read of the shipped milestones surfaced six gaps that only appear once a real GitHub App is
installed and pointed at the service. None of them is a test failure — they are behaviors no test
currently exercises:

1. **No event filtering.** Nothing reads `X-GitHub-Event` (verified by grep). Every subscribed
   event — `issue_comment`, `push`, `label`, `pull_request.closed` — enqueues a full review job.
   A single PR conversation would re-review on every comment.
2. **A fetch failure posts a green review.** `_fetch_pr_data` swallows the exception, `_process_one`
   then assesses `code=""`, and `_post_review` posts "No security issues detected" plus a `success`
   check run. A GitHub outage is indistinguishable from a clean PR.
3. **A fixed 2 s sleep per job.** `asyncio.sleep(2)` sits in the loop body rather than the error
   path, so a backlog of 50 PRs pays 100 s of pure idle.
4. **Redis jobs retry forever.** No attempt counter and no dead-letter list: a job that kills the
   process is re-queued by `recover_pending()` at every restart, indefinitely.
5. **Shutdown drops the in-flight job.** `worker_task.cancel()` with no await; the `to_thread`
   pipeline keeps running and never acks.
6. **The toy fallback corpus can force a false HIGH.** With no owner or a failed corpus fetch, the
   PR is compared against `def add(a, b)` / `def subtract(a, b)`; crossing 0.9 similarity yields a
   semantic HIGH, which `_overall_severity` turns into a `failure` check run.

**Outcome:** Sentinel reviews only what it should, never reports a pass it did not verify, drains a
backlog at queue speed, and gives up on a poison job instead of looping on it forever.

**Decisions taken** (from clarification):
- Fetch failure → **neutral check run, no comment.**
- Redis scope → **attempts + dead-letter only**; no visibility-timeout sweep this milestone.
- Delivery → **one branch `feat/M8-tier1`, one commit (with its tests) per item.**

## Global constraints

- **Non-breaking is hard.** All 478 existing tests must stay green unchanged. The binding
  constraint found during exploration: **no test sends an `X-GitHub-Event` header**, so event
  filtering must be gated on the header being *present* — exactly the precedent M0 set for
  `GITHUB_WEBHOOK_SECRET` (`sentinel/api/webhook_security.py:295-308`).
- Config only via `get_settings()`. Failure-safe edges: instrumentation and GitHub calls never
  raise into a review. Module-level `logger = get_logger(__name__)`.
- Ruff must stay at zero violations; coverage gate `--cov-fail-under=85`.

---

## Commit 1 — Filter GitHub events (item 1)

**Create `sentinel/api/webhook_events.py`** — a pure, dependency-free helper next to
`webhook_security.py` (same route-concern pattern):

```python
REVIEWABLE_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})

def should_process(event: str | None, raw_payload: dict) -> tuple[bool, str]:
    """(process?, reason). Gated on the header being present — no header, no filtering."""
```

Contract:

| `X-GitHub-Event` | action | result |
|---|---|---|
| absent / empty | — | `(True, "unfiltered")` — preserves every existing test and manual `curl` |
| `ping` | — | `(False, "ping")` |
| anything but `pull_request` | — | `(False, f"event:{event}")` |
| `pull_request` | in `REVIEWABLE_ACTIONS` | `(True, "pull_request")` |
| `pull_request` | `closed` / `labeled` / `edited` / … | `(False, f"action:{action}")` |
| `pull_request` | missing or non-str | `(True, ...)` — stay permissive on malformed payloads |

**Modify `sentinel/api/webhook_controller.py`** (`webhook()`, ~line 495):
Read the body once at the top of the route (Starlette caches it; `verify_webhook_signature` already
calls `await request.body()`), then apply the filter **before** the dedup check so an ignored event
never consumes a dedup slot or a Redis write. Return HTTP **200** (GitHub marks non-2xx as a failed
delivery) with `{"status": "ignored", "event": event}` and
`metrics.counter_inc("sentinel_webhooks_total", {"mode": "ignored"})`.

**Tests — `sentinel/tests/test_webhook_events.py` (new):** no header → processed (regression guard
for the whole existing suite); each reviewable action → processed; `closed`/`labeled`/`edited` →
`{"status": "ignored"}` with the orchestrator never called; `issue_comment`/`push`/`star` → ignored;
`ping` → ignored; the `mode="ignored"` counter increments.

## Commit 2 — Drop the toy semantic corpus (item 6)

**Modify `sentinel/workers/background_worker.py`:** delete the `EXISTING_CODE_LIST` constant
(lines 21-24) and its fallback in `_assess` (lines 124-126). Pass `[]` when the job carries no real
corpus — `SemanticService.detect_duplicates` already short-circuits on an empty list
(`sentinel/domain/services/semantic_service.py:321`), so semantic analysis simply no-ops. Log one
line when it is skipped so the absence is visible rather than silent.

*Verified safe:* no test references `EXISTING_CODE_LIST`; the two tests asserting
`semantic_findings_count` (`test_stabilization_hardening.py:105,120`) call `assess_resilient`
directly with an explicit `existing_code_list`.

**Test:** an owner-less job yields `semantic_findings_count == 0` and never fabricates a HIGH.

## Commit 3 — Never report a pass we did not verify (item 2)

**Modify `sentinel/workers/background_worker.py`:**

*Key nuance:* **empty code ≠ fetch failure.** A docs-only PR legitimately produces `code == ""` and
must still be reviewed for documentation findings. The signal must be an explicit failure flag, not
an emptiness check.

1. **Reorder `_fetch_pr_data`** so `get_pull_request_refs` runs **first**. Today `head_sha` is
   stashed only after the files fetch, so a fetch failure leaves no SHA to attach a check run to.
   New order: refs (stash `head_sha`/`base_sha`) → PR files → corpus from `base_sha`.
2. **Return `bool`** ("fetch OK"): `True` when there was nothing to fetch (no client, code already
   present, owner-less job — `test_worker_skips_github_when_no_owner` depends on this still being
   assessed); `False` only when `get_pull_request_data` raises. A corpus failure stays best-effort
   and does not flip the flag.
3. **Branch in `_process_one`:** on `False`, skip the assessment, the `sentinel_reviews_total`
   counter, and `_post_review` entirely. Print a distinct line (`PR #<n> Risk: SKIPPED (fetch
   failed)`) instead of a misleading `LOW`, and call a new `_post_fetch_failure`.
4. **`_post_fetch_failure`** — guarded by owner/repo/pr, `ENABLE_CHECKS`, and a stashed `head_sha`;
   reuses `GitHubClient.create_check_run` unchanged:
   ```python
   conclusion="neutral",  # visible in the merge box, never blocks a merge
   title="Review skipped",
   summary="Sentinel could not fetch this pull request's contents from the GitHub API. "
           "No review was performed.",
   ```
   Metrics: `sentinel_github_posts_total{kind="check_run",outcome="skipped"}` and a new
   `sentinel_reviews_skipped_total{reason="fetch_failed"}` (the one worth alerting on).

**Tests (new, in `sentinel/tests/test_worker_github_loop.py`):** a fake whose
`get_pull_request_data` raises → no comment posted, exactly one `neutral` check run, both counters
set, no `sentinel_reviews_total`. A docs-only PR (empty `code`, `files=["README.md"]`) → still
comments **and** posts a normal check run, proving empty code is not treated as failure.

## Commit 4 — Remove the per-job sleep (item 3)

**Modify `background_worker.py:374-389`:** move `await asyncio.sleep(...)` out of the loop body and
into the `except` branch only, behind a named `ERROR_BACKOFF_SECONDS = 2.0` class constant. Both
queues already block or poll inside `dequeue()` (`asyncio.Queue.get()`; `RedisJobQueue`'s
`_poll_interval`), so the hot path needs no pacing — the sleep exists solely to stop a tight spin
when `dequeue()` itself keeps raising.

*Safe:* every worker test monkeypatches `bw_module.asyncio.sleep` with a no-op already
(`hardening/test_worker_lifecycle.py`, `test_worker_github_loop.py:_drive_one_job`).

**Tests:** five queued jobs all complete inside a wall-clock budget the old 2 s/job could not meet;
a `dequeue()` that raises once still backs off (assert the sleep is awaited).

## Commit 5 — Finish the in-flight job on shutdown (item 5)

**Modify `background_worker.py` `start()`:** extract the process-and-ack pair into
`_run_and_ack(job, ...)` and shield it, awaiting the shielded future to completion before
re-raising, so a cancel mid-job still acks and leaves no orphan task:

```python
inner = asyncio.ensure_future(self._run_and_ack(job, risk_engine, orchestrator, github_client))
try:
    await asyncio.shield(inner)
except asyncio.CancelledError:
    with contextlib.suppress(Exception):
        await inner          # let the in-flight job finish and ack
    raise
```

A bare `shield` alone would exit while `inner` is still running and break
`hardening/test_worker_lifecycle.py::test_no_orphan_tasks_after_worker_shutdown`; awaiting it in the
handler keeps that test green.

**Modify `main.py:355-360`** (lifespan `finally`): bound the wait rather than firing and forgetting.

```python
SHUTDOWN_GRACE_SECONDS = 10.0
worker_task.cancel()
with contextlib.suppress(asyncio.CancelledError, TimeoutError):
    await asyncio.wait_for(worker_task, timeout=SHUTDOWN_GRACE_SECONDS)
```

No separate stop flag is needed — the shield makes cancellation wait for the current job.

**Tests:** cancel a worker while a deliberately slow job is in flight; assert the job was acked and
`processed_count` incremented before the task completed, and that no pending tasks remain.

## Commit 6 — Attempts + dead-letter for Redis (item 4)

**Modify `sentinel/infrastructure/redis/redis_job_queue.py`:**

- Add `DEAD_KEY = "sentinel:jobs:dead"` and `MAX_ATTEMPTS = 3` class constants.
- Add `"attempts": 0` to the envelope in `enqueue` — **in the envelope, never in the job dict.**
  `test_crash_recovery_requeues_unacked_job` asserts `recovered == job`, so the recovered dict must
  stay byte-identical.
- Add `_parse_envelope(raw) -> dict | None` and reduce `_parse_job` to a thin wrapper over it.
- Rewrite `recover_pending()`: pop each raw from the processing list, parse the envelope, bump
  `attempts`; `attempts >= MAX_ATTEMPTS` → `LPUSH DEAD_KEY` + `metrics.counter_inc(
  "sentinel_jobs_deadlettered_total")`; otherwise re-serialize with the bumped count and
  `LPUSH QUEUE_KEY`. Keep the `-> int` return as the **re-queued** count so
  `sentinel/workers/job_queue.py`'s in-memory no-op keeps interface parity, and log the
  dead-lettered count separately.

*Not in scope this milestone:* the visibility-timeout sweep. A **hung** (not crashed) worker still
strands its job until the next restart — record this in `DECISIONS.md` as a known limitation.

**Tests (`sentinel/tests/test_redis_job_queue.py`):** `attempts` survives a round trip; a job
recovered `MAX_ATTEMPTS` times lands in `sentinel:jobs:dead` and is gone from `sentinel:jobs`; the
dead-letter counter increments; the existing `recover_pending() == 1` assertion still holds.

## Commit 7 — Documentation

- **`CLAUDE.md`** — the "Request pipeline" section documents `EXISTING_CODE_LIST` as the corpus
  fallback (now removed) and describes `_fetch_pr_data` as always posting. Add the event filter,
  the neutral-check-run-on-fetch-failure rule, the dead-letter key, and `ERROR_BACKOFF_SECONDS`.
  Also correct the stale "actual ≈88%" coverage figure — it is 91%.
- **`README.md`** — note which events Sentinel acts on (a user-visible contract).
- **`DECISIONS.md`** — one ADR entry for "attempts + dead-letter instead of a visibility timeout",
  and strike weakness #4 from the honest-weaknesses list where it now overstates the problem.

---

## Verification

Run from the repo root with `.venv` active. Each commit must be green before the next.

```bash
# Per commit
pytest -q                                   # all 478 + new tests
ruff check sentinel main.py                 # must stay at zero

# Before merge
pytest --cov=sentinel --cov-branch --cov-report=term-missing --cov-fail-under=85
pytest sentinel/tests/hardening/ -v         # lifecycle/concurrency are the ones commits 4-5 risk
docker build -t sentinel:dev .              # CI's third job
```

**Manual end-to-end** (no GitHub App required — the webhook accepts unsigned requests while
`GITHUB_WEBHOOK_SECRET` is unset):

```bash
uvicorn main:app --reload

# 1. Ignored: wrong event -> {"status":"ignored"}
curl -s -X POST localhost:8000/webhook -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: issue_comment' -d '{"repo":"o/r","pr_number":1}'

# 2. Ignored: right event, wrong action
curl -s -X POST localhost:8000/webhook -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: pull_request' -d '{"action":"closed","repo":"o/r","pr_number":1}'

# 3. Accepted -> {"status":"queued"}
curl -s -X POST localhost:8000/webhook -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: pull_request' -d '{"action":"opened","repo":"o/r","pr_number":1}'

# 4. Back-compat: no event header still behaves exactly as today
curl -s -X POST localhost:8000/webhook -H 'Content-Type: application/json' \
  -d '{"repo":"o/r","pr_number":1,"code":"password = \"hunter2\""}'

# 5. Counters reflect all of the above
curl -s localhost:8000/metrics | grep sentinel_webhooks_total
```

**Backlog drain (item 3)** — enqueue 10 jobs via step 3 and confirm from the log timestamps that
they process back-to-back rather than ~2 s apart.

**Dead-letter (item 6)** — with `REDIS_URL` set: `LPUSH sentinel:jobs:processing` a raw envelope,
restart the app three times, then `LLEN sentinel:jobs:dead` → `1` and `LLEN sentinel:jobs` → `0`.

## Notes

- The repo keeps implementation plans in `docs/superpowers/plans/` (see
  `2026-07-13-m0-production-guardrails.md`). Copy this file there as
  `2026-08-23-m8-tier1-production-correctness.md` when work starts.
- Tier 2–4 findings (HTTP-layer duplication, `GitHubClient` having zero logging, the invalid
  Prometheus summary series, the webhook controller's ~60 duplicated lines, the AST security
  engine) are deliberately out of scope here.
