"""M8: the X-GitHub-Event gate — only code-changing pull_request deliveries are reviewed.

Unit tests pin the ``should_process`` contract; route tests prove the webhook
short-circuits ignorable events before dedup or enqueue, while deliveries with
no event header keep behaving exactly as they always have.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sentinel.api.webhook_controller as webhook_controller
from sentinel.api.delivery_dedup import DeliveryDeduper
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.api.webhook_events import REVIEWABLE_ACTIONS, should_process
from sentinel.monitoring.metrics import metrics

# --- should_process: the header gate ---


def test_missing_or_unusable_event_header_is_never_filtered():
    """No header -> no filtering. This is what keeps every pre-M8 test green."""
    for event in (None, "", "   ", 123):
        allowed, reason = should_process(event, {"action": "closed"})
        assert allowed is True
        assert reason == "unfiltered"


def test_ping_is_ignored():
    allowed, reason = should_process("ping", {})
    assert allowed is False
    assert reason == "ping"


def test_non_pull_request_events_are_ignored():
    for event in ("issue_comment", "push", "star", "label", "check_suite"):
        allowed, reason = should_process(event, {"action": "created"})
        assert allowed is False
        assert reason == f"event:{event}"


# --- should_process: the action gate ---


def test_every_reviewable_action_is_processed():
    for action in REVIEWABLE_ACTIONS:
        allowed, reason = should_process("pull_request", {"action": action})
        assert allowed is True, action
        assert reason == f"action:{action}"


def test_non_code_changing_actions_are_ignored():
    for action in ("closed", "labeled", "unlabeled", "edited", "assigned", "review_requested"):
        allowed, reason = should_process("pull_request", {"action": action})
        assert allowed is False, action
        assert reason == f"action:{action}"


def test_pull_request_without_a_usable_action_is_processed():
    """Permissive by design: an unexpected payload shape degrades to old behavior."""
    for payload in ({}, {"action": None}, {"action": ""}, {"action": 7}, "not-a-dict"):
        allowed, reason = should_process("pull_request", payload)
        assert allowed is True
        assert reason == "pull_request:no-action"


def test_event_and_action_matching_ignores_case_and_padding():
    assert should_process("  Pull_Request  ", {"action": " Opened "})[0] is True
    assert should_process("Pull_Request", {"action": "CLOSED"})[0] is False
    assert should_process("  PING  ", {})[0] is False


# --- Route behavior ---


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.received: list[dict] = []
        self.sync_reviews = 0

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.received.append(payload)

    def run_full_review(self, code, findings, risk, **kwargs):
        self.sync_reviews += 1
        return findings, "# Sentinel AI Code Review"


def _build_client(monkeypatch) -> tuple[TestClient, _DummyOrchestrator]:
    # _deduper is module state shared across apps; give each test a fresh one.
    monkeypatch.setattr(webhook_controller, "_deduper", DeliveryDeduper())

    app = FastAPI(title="The Sentinel Event Gate Test App")
    orchestrator = _DummyOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.include_router(webhook_router)
    return TestClient(app), orchestrator


_PR_BODY = {"action": "opened", "repo": "octo/hello", "pr_number": 1}


def test_request_without_event_header_is_queued_as_before(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    response = client.post("/webhook", json={"repo": "octo/hello", "pr_number": 1})

    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    assert len(orchestrator.received) == 1


def test_reviewable_pull_request_event_is_queued(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    response = client.post(
        "/webhook", json=_PR_BODY, headers={"X-GitHub-Event": "pull_request"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    assert len(orchestrator.received) == 1


def test_closed_pull_request_is_ignored_without_enqueueing(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    response = client.post(
        "/webhook",
        json={"action": "closed", "repo": "octo/hello", "pr_number": 1},
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 200  # GitHub must still record success
    assert response.json() == {"status": "ignored", "event": "pull_request"}
    assert orchestrator.received == []


def test_unrelated_event_is_ignored_even_with_reviewable_payload(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    response = client.post(
        "/webhook", json=_PR_BODY, headers={"X-GitHub-Event": "issue_comment"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "issue_comment"}
    assert orchestrator.received == []


def test_ping_event_is_ignored(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    response = client.post("/webhook", json={"zen": "..."}, headers={"X-GitHub-Event": "ping"})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "ping"}
    assert orchestrator.received == []


def test_sync_mode_still_runs_for_a_reviewable_event(monkeypatch):
    """The event gate sits ahead of the mode branch — it must not break sync mode."""
    client, _ = _build_client(monkeypatch)

    response = client.post(
        "/webhook",
        json={"action": "synchronize", "repo": "octo/hello", "pr_number": 1, "code": "x = 1"},
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_ignored_delivery_is_counted_and_does_not_consume_a_dedup_slot(monkeypatch):
    """An ignored event must not burn the delivery id — a later real send still works."""
    metrics.reset()
    client, orchestrator = _build_client(monkeypatch)
    headers = {"X-GitHub-Delivery": "guid-1"}

    ignored = client.post(
        "/webhook",
        json={"action": "closed", "repo": "octo/hello", "pr_number": 1},
        headers={**headers, "X-GitHub-Event": "pull_request"},
    )
    replayed = client.post("/webhook", json=_PR_BODY, headers=headers)

    assert ignored.json()["status"] == "ignored"
    assert replayed.json() == {"status": "queued"}
    assert len(orchestrator.received) == 1

    counters = metrics.snapshot()["counters"]
    assert counters['sentinel_webhooks_total{mode="ignored"}'] == 1
    assert counters['sentinel_webhooks_total{mode="queued"}'] == 1
