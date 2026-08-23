"""Which GitHub webhook deliveries are worth a review (X-GitHub-Event gate).

A GitHub App receives every event its subscription covers — `issue_comment`,
`push`, `label`, `pull_request.closed` — and without a filter each one enqueues
a full review job, so a single PR conversation re-reviews on every comment.

Gated on the header being **present**, mirroring the webhook-secret gate in
``webhook_security``: no ``X-GitHub-Event`` means no filtering, so manual
``curl`` calls and the test suite behave exactly as they did before. Real
GitHub traffic always carries the header.

Pure and dependency-free: no settings, no I/O, no FastAPI types.
"""

from typing import Any

# Actions that change the code under review. `edited` (title/body), `labeled`,
# `closed`, `assigned` and friends deliberately do not — re-reviewing on those
# is the noise this module exists to stop.
REVIEWABLE_ACTIONS = frozenset(
    {"opened", "synchronize", "reopened", "ready_for_review"}
)

PULL_REQUEST_EVENT = "pull_request"


def should_process(event: str | None, raw_payload: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether a delivery should be reviewed. Returns (process?, reason).

    The reason is a short machine-ish tag ("action:closed", "event:push") meant
    for logs, not for users. Permissive by design: anything this function cannot
    positively identify as ignorable is processed, so a payload shape we did not
    anticipate degrades to today's behavior rather than silently dropping work.
    """
    if not isinstance(event, str) or not event.strip():
        return True, "unfiltered"

    normalized_event = event.strip().lower()

    if normalized_event == "ping":
        return False, "ping"

    if normalized_event != PULL_REQUEST_EVENT:
        return False, f"event:{normalized_event}"

    action = raw_payload.get("action") if isinstance(raw_payload, dict) else None
    if not isinstance(action, str) or not action.strip():
        return True, "pull_request:no-action"

    normalized_action = action.strip().lower()
    if normalized_action in REVIEWABLE_ACTIONS:
        return True, f"action:{normalized_action}"

    return False, f"action:{normalized_action}"
