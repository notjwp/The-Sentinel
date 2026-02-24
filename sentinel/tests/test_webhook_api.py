import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router


class DummyOrchestrator:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.received.append(payload)


def _build_client() -> tuple[TestClient, DummyOrchestrator]:
    app = FastAPI(title="The Sentinel Test App")
    orchestrator = DummyOrchestrator()

    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.include_router(webhook_router)
    app.include_router(health_router)

    @app.get("/")
    def root():
        return {"message": "The Sentinel is running"}

    return TestClient(app), orchestrator


def test_health_endpoint_returns_ok():
    client, _ = _build_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_valid_payload_returns_200():
    client, orchestrator = _build_client()
    response = client.post("/webhook", json={"repo": "test-repo", "pr_number": 123})
    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    assert orchestrator.received == [{"repo": "test-repo", "pr_number": 123}]


def test_webhook_invalid_payload_returns_422_for_non_object_body():
    client, _ = _build_client()
    response = client.post("/webhook", json=["not", "an", "object"])
    assert response.status_code == 422


def test_webhook_missing_body_returns_422():
    client, _ = _build_client()
    response = client.post("/webhook")
    assert response.status_code == 422


def test_webhook_get_returns_405():
    client, _ = _build_client()
    response = client.get("/webhook")
    assert response.status_code == 405


def test_root_endpoint_returns_expected_message():
    client, _ = _build_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "The Sentinel is running"}


def test_webhook_100_sequential_posts_stable():
    client, orchestrator = _build_client()
    for index in range(100):
        response = client.post("/webhook", json={"repo": "seq", "pr_number": index})
        assert response.status_code == 200
    assert len(orchestrator.received) == 100


def test_webhook_parallel_posts_via_asyncio_gather():
    client, _ = _build_client()

    async def _post(index: int) -> int:
        response = await asyncio.to_thread(
            client.post,
            "/webhook",
            json={"repo": "parallel", "pr_number": index},
        )
        return response.status_code

    async def _run_parallel() -> list[int]:
        return await asyncio.gather(*[_post(index) for index in range(25)])

    statuses = asyncio.run(_run_parallel())
    assert all(status == 200 for status in statuses)


def test_webhook_invalid_json_payload_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content='{"repo": "x", "pr_number": 1',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_missing_content_type_header_is_handled():
    client, _ = _build_client()
    response = client.post("/webhook", content='{"repo":"x","pr_number":1}')
    assert response.status_code in {200, 422}


def test_webhook_large_payload_1mb_no_crash():
    client, _ = _build_client()
    large_repo = "r" * (1024 * 1024)
    response = client.post("/webhook", json={"repo": large_repo, "pr_number": 1})
    assert response.status_code == 200
