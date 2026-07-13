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


def test_is_valid_signature_non_ascii_header_returns_false():
    # A non-ASCII signature header must not raise (would 500); it must be rejected.
    assert is_valid_signature("s3cret", b'{"repo":"r"}', "sha256=\xff") is False
