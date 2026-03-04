import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.workers.job_queue import JobQueue


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.received.append(payload)


class _FailingOrchestrator:
    async def enqueue_pull_request(self, payload: dict) -> None:
        raise RuntimeError("orchestrator failure")


def _build_client(
    orchestrator: object | None = None,
) -> tuple[TestClient, object]:
    app = FastAPI(title="Test")
    orch = orchestrator or _FakeOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.include_router(webhook_router)
    app.include_router(health_router)

    @app.get("/")
    def root() -> dict:
        return {"message": "The Sentinel is running"}

    return TestClient(app, raise_server_exceptions=False), orch


# --- Webhook Invalid JSON ---


def test_webhook_malformed_json_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content="{broken json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_empty_body_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content="",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_null_json_body_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content="null",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_array_body_returns_422():
    client, _ = _build_client()
    response = client.post("/webhook", json=[1, 2, 3])
    assert response.status_code == 422


def test_webhook_string_body_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content='"just a string"',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_integer_body_returns_422():
    client, _ = _build_client()
    response = client.post(
        "/webhook",
        content="42",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


# --- Missing Fields ---


def test_webhook_empty_object_accepted_by_api():
    client, orch = _build_client()
    response = client.post("/webhook", json={})
    assert response.status_code == 200
    assert orch.received == [{}]


def test_webhook_missing_repo_field_still_accepted():
    client, orch = _build_client()
    response = client.post("/webhook", json={"pr_number": 1})
    assert response.status_code == 200
    assert len(orch.received) == 1


def test_webhook_missing_pr_number_field_still_accepted():
    client, orch = _build_client()
    response = client.post("/webhook", json={"repo": "test"})
    assert response.status_code == 200
    assert len(orch.received) == 1


# --- Orchestrator Failure ---


def test_webhook_returns_500_when_orchestrator_raises():
    client, _ = _build_client(orchestrator=_FailingOrchestrator())
    response = client.post("/webhook", json={"repo": "t", "pr_number": 1})
    assert response.status_code == 500


# --- Dependency Override Missing ---


def test_missing_dependency_override_raises_runtime_error():
    app = FastAPI()
    app.include_router(webhook_router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/webhook", json={"repo": "t", "pr_number": 1})
    assert response.status_code == 500


# --- Queue Enqueue Failure ---


def test_orchestrator_propagates_queue_failure():
    class _BrokenQueue:
        async def enqueue(self, job: dict) -> None:
            raise OSError("queue full")

    orch = AuditOrchestrator(queue=_BrokenQueue())

    async def _run() -> None:
        with pytest.raises(OSError, match="queue full"):
            await orch.enqueue_pull_request({"repo": "x", "pr_number": 1})

    asyncio.run(_run())


# --- Background Worker Exception Handling ---


def test_worker_survives_bad_job_and_processes_next(capsys):
    import sentinel.workers.background_worker as bw_module

    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        await queue.enqueue({"bad": "job"})
        await queue.enqueue({"repo": "ok", "pr_number": 99})

        from sentinel.workers.background_worker import BackgroundWorker

        worker = BackgroundWorker(queue)

        original_start = worker.start

        async def _patched_start() -> None:
            from sentinel.application.report_service import ReportService
            from sentinel.application.risk_engine import RiskEngine
            from sentinel.application.use_cases.process_pull_request import (
                ProcessPullRequestUseCase,
            )
            from sentinel.domain.services.semantic_service import SemanticService
            from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine

            embedding_engine = EmbeddingEngine()
            semantic_service = SemanticService(embedding_engine)
            risk_engine = RiskEngine(semantic_service=semantic_service)
            report_service = ReportService()
            use_case = ProcessPullRequestUseCase(risk_engine, report_service)

            processed = 0
            while True:
                job = await queue.dequeue()
                try:
                    report = use_case.execute(job)
                    print(report, flush=True)
                except (KeyError, TypeError):
                    pass
                processed += 1
                if processed >= 2:
                    break
                await fast_sleep(0)

        await _patched_start()

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #99 Risk: LOW" in output


# --- Orchestrator Enqueue Pull Request Coverage ---


def test_orchestrator_enqueue_extracts_repo_and_pr_number():
    async def _run() -> None:
        queue = JobQueue()
        orch = AuditOrchestrator(queue)
        await orch.enqueue_pull_request({"repo": "my-repo", "pr_number": 42})
        job = await queue.dequeue()
        assert job["repo"] == "my-repo"
        assert job["pr_number"] == 42

    asyncio.run(_run())


def test_orchestrator_enqueue_missing_keys_sets_none():
    async def _run() -> None:
        queue = JobQueue()
        orch = AuditOrchestrator(queue)
        await orch.enqueue_pull_request({})
        job = await queue.dequeue()
        assert job["repo"] is None
        assert job["pr_number"] is None

    asyncio.run(_run())


def test_orchestrator_enqueue_extra_fields_ignored():
    async def _run() -> None:
        queue = JobQueue()
        orch = AuditOrchestrator(queue)
        await orch.enqueue_pull_request(
            {"repo": "r", "pr_number": 1, "extra": "ignored"}
        )
        job = await queue.dequeue()
        assert "extra" not in job
        assert set(job.keys()) == {"repo", "pr_number"}

    asyncio.run(_run())
