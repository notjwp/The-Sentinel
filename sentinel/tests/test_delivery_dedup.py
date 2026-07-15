"""M3: X-GitHub-Delivery dedup — re-sent deliveries are answered without re-processing.

Unit tests cover the DeliveryDeduper semantics (TTL, capacity, missing ids);
route tests prove the webhook short-circuits duplicates in both queued and sync
modes while requests without the header stay completely unaffected.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sentinel.api.webhook_controller as webhook_controller
from sentinel.api.delivery_dedup import DeliveryDeduper
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_first_seen_is_not_duplicate_then_repeat_is():
    deduper = DeliveryDeduper()
    assert deduper.is_duplicate("guid-1") is False
    assert deduper.is_duplicate("guid-1") is True


def test_missing_or_unusable_ids_are_never_deduped():
    deduper = DeliveryDeduper()
    for _ in range(2):  # repeats must stay False too
        assert deduper.is_duplicate(None) is False
        assert deduper.is_duplicate("") is False
        assert deduper.is_duplicate("   ") is False
        assert deduper.is_duplicate(123) is False  # type: ignore[arg-type]


def test_ttl_expiry_reallows_a_delivery():
    clock = _FakeClock()
    deduper = DeliveryDeduper(ttl_seconds=600.0, clock=clock)

    assert deduper.is_duplicate("guid-1") is False
    clock.now += 599.0
    assert deduper.is_duplicate("guid-1") is True  # still within TTL
    clock.now += 2.0
    assert deduper.is_duplicate("guid-1") is False  # expired -> fresh again


def test_capacity_eviction_forgets_the_oldest_id():
    deduper = DeliveryDeduper(max_entries=2)
    assert deduper.is_duplicate("a") is False
    assert deduper.is_duplicate("b") is False
    assert deduper.is_duplicate("c") is False  # evicts "a" (oldest)
    assert deduper.is_duplicate("a") is False  # forgotten, treated as new
    assert deduper.is_duplicate("c") is True  # newest still remembered


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

    app = FastAPI(title="The Sentinel Dedup Test App")
    orchestrator = _DummyOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.include_router(webhook_router)
    return TestClient(app), orchestrator


def test_repeated_delivery_id_is_queued_once(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)
    headers = {"X-GitHub-Delivery": "guid-abc"}

    first = client.post("/webhook", json={"repo": "demo", "pr_number": 1}, headers=headers)
    second = client.post("/webhook", json={"repo": "demo", "pr_number": 1}, headers=headers)

    assert first.status_code == 200
    assert first.json() == {"status": "queued"}
    assert second.status_code == 200  # GitHub must still record success
    assert second.json() == {"status": "duplicate"}
    assert len(orchestrator.received) == 1


def test_distinct_delivery_ids_both_queue(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    first = client.post(
        "/webhook", json={"repo": "demo", "pr_number": 1},
        headers={"X-GitHub-Delivery": "guid-1"},
    )
    second = client.post(
        "/webhook", json={"repo": "demo", "pr_number": 2},
        headers={"X-GitHub-Delivery": "guid-2"},
    )

    assert first.json() == {"status": "queued"}
    assert second.json() == {"status": "queued"}
    assert len(orchestrator.received) == 2


def test_requests_without_delivery_header_are_never_deduped(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)

    for _ in range(2):
        response = client.post("/webhook", json={"repo": "demo", "pr_number": 1})
        assert response.json() == {"status": "queued"}

    assert len(orchestrator.received) == 2


def test_sync_mode_duplicate_delivery_skips_reprocessing(monkeypatch):
    client, orchestrator = _build_client(monkeypatch)
    payload = {"repo": "demo", "pr_number": 1, "code": "print('hello')"}
    headers = {"X-GitHub-Delivery": "guid-sync"}

    first = client.post("/webhook", json=payload, headers=headers)
    second = client.post("/webhook", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert orchestrator.sync_reviews == 1
