import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router


class _CountingOrchestrator:
    def __init__(self) -> None:
        self.count = 0

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.count += 1
        await asyncio.sleep(0.001)


def _build_client() -> tuple[TestClient, _CountingOrchestrator]:
    app = FastAPI(title="Stress Test")
    orch = _CountingOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.include_router(webhook_router)
    app.include_router(health_router)
    return TestClient(app), orch


def test_500_concurrent_webhook_posts_no_deadlock():
    client, orch = _build_client()

    async def _post(index: int) -> int:
        response = await asyncio.to_thread(
            client.post,
            "/webhook",
            json={"repo": "stress", "pr_number": index},
        )
        return response.status_code

    async def _run() -> list[int]:
        return await asyncio.gather(*[_post(i) for i in range(500)])

    statuses = asyncio.run(_run())
    assert all(s == 200 for s in statuses)
    assert orch.count == 500


def test_1000_concurrent_webhook_posts_no_deadlock():
    client, orch = _build_client()

    async def _post(index: int) -> int:
        response = await asyncio.to_thread(
            client.post,
            "/webhook",
            json={"repo": "stress", "pr_number": index},
        )
        return response.status_code

    async def _run() -> list[int]:
        return await asyncio.gather(*[_post(i) for i in range(1000)])

    statuses = asyncio.run(_run())
    assert all(s == 200 for s in statuses)
    assert orch.count == 1000


def test_no_duplicate_processing_in_concurrent_posts():
    client, _ = _build_client()
    seen_indices: list[int] = []

    class _TrackingOrchestrator:
        def __init__(self) -> None:
            self.received: list[int] = []

        async def enqueue_pull_request(self, payload: dict) -> None:
            self.received.append(payload.get("pr_number"))
            await asyncio.sleep(0.001)

    app = FastAPI(title="Dedup Test")
    tracker = _TrackingOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: tracker
    app.include_router(webhook_router)
    dedup_client = TestClient(app)

    async def _post(index: int) -> int:
        response = await asyncio.to_thread(
            dedup_client.post,
            "/webhook",
            json={"repo": "dedup", "pr_number": index},
        )
        return response.status_code

    async def _run() -> list[int]:
        return await asyncio.gather(*[_post(i) for i in range(500)])

    statuses = asyncio.run(_run())
    assert all(s == 200 for s in statuses)
    assert len(tracker.received) == 500
    assert len(set(tracker.received)) == 500


def test_queue_drains_completely_after_concurrent_enqueue():
    from sentinel.workers.job_queue import JobQueue

    async def _run() -> None:
        queue = JobQueue()
        for i in range(500):
            await queue.enqueue({"repo": "drain", "pr_number": i})

        assert queue._queue.qsize() == 500

        seen: set[int] = set()
        for _ in range(500):
            job = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
            seen.add(job["pr_number"])

        assert len(seen) == 500
        assert queue._queue.qsize() == 0

    asyncio.run(_run())


def test_concurrent_posts_average_response_time_under_threshold():
    client, _ = _build_client()
    latencies: list[float] = []

    async def _timed_post(index: int) -> int:
        start = time.monotonic()
        response = await asyncio.to_thread(
            client.post,
            "/webhook",
            json={"repo": "latency", "pr_number": index},
        )
        elapsed = time.monotonic() - start
        latencies.append(elapsed)
        return response.status_code

    async def _run() -> list[int]:
        return await asyncio.gather(*[_timed_post(i) for i in range(200)])

    statuses = asyncio.run(_run())
    assert all(s == 200 for s in statuses)
    avg_latency = sum(latencies) / len(latencies)
    assert avg_latency < 2.0


def test_concurrent_enqueue_dequeue_interleaved():
    from sentinel.workers.job_queue import JobQueue

    async def _run() -> None:
        queue = JobQueue()
        consumed: list[int] = []

        async def _producer() -> None:
            for i in range(300):
                await queue.enqueue({"repo": "interleaved", "pr_number": i})
                await asyncio.sleep(0)

        async def _consumer() -> None:
            for _ in range(300):
                job = await asyncio.wait_for(queue.dequeue(), timeout=5.0)
                consumed.append(job["pr_number"])

        await asyncio.gather(_producer(), _consumer())
        assert len(consumed) == 300
        assert queue._queue.qsize() == 0

    asyncio.run(_run())
