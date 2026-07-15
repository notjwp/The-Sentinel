"""M7: MetricsRegistry, the /metrics endpoint, and LLM outcome counters."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.metrics_controller import router as metrics_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.llm_service import LLMService
from sentinel.monitoring.metrics import MetricsRegistry, metrics
from sentinel.workers.job_queue import JobQueue

# ── registry unit tests ────────────────────────────────────────────────────────


def test_counters_accumulate_per_label_set():
    registry = MetricsRegistry()
    registry.counter_inc("hits", {"mode": "a"})
    registry.counter_inc("hits", {"mode": "a"}, value=2)
    registry.counter_inc("hits", {"mode": "b"})
    registry.counter_inc("plain")

    snap = registry.snapshot()
    assert snap["counters"]['hits{mode="a"}'] == 3
    assert snap["counters"]['hits{mode="b"}'] == 1
    assert snap["counters"]["plain"] == 1


def test_gauge_overwrites_and_summary_aggregates():
    registry = MetricsRegistry()
    registry.gauge_set("depth", 5)
    registry.gauge_set("depth", 2)
    for value in (0.5, 1.5, 1.0):
        registry.observe("latency", value)

    snap = registry.snapshot()
    assert snap["gauges"]["depth"] == 2
    assert snap["summaries"]["latency"] == {"count": 3, "sum": 3.0, "min": 0.5, "max": 1.5}


def test_prometheus_rendering_format():
    registry = MetricsRegistry()
    registry.counter_inc("sentinel_webhooks_total", {"mode": "queued"})
    registry.gauge_set("sentinel_queue_depth", 0)
    registry.observe("sentinel_job_duration_seconds", 1.25)

    text = registry.render_prometheus()
    assert "# TYPE sentinel_webhooks_total counter" in text
    assert 'sentinel_webhooks_total{mode="queued"} 1' in text
    assert "# TYPE sentinel_queue_depth gauge" in text
    assert "sentinel_queue_depth 0" in text
    assert "# TYPE sentinel_job_duration_seconds summary" in text
    assert "sentinel_job_duration_seconds_count 1" in text
    assert "sentinel_job_duration_seconds_sum 1.25" in text
    assert text.endswith("\n")


def test_reset_clears_everything():
    registry = MetricsRegistry()
    registry.counter_inc("x")
    registry.gauge_set("y", 1)
    registry.observe("z", 1)
    registry.reset()
    assert registry.snapshot() == {"counters": {}, "gauges": {}, "summaries": {}}
    assert registry.render_prometheus() == ""


class _PoisonLabel:
    def __str__(self):
        raise RuntimeError("boom")


def test_registry_never_raises():
    registry = MetricsRegistry()
    registry.counter_inc("x", {_PoisonLabel(): "v"})  # type: ignore[dict-item]
    registry.gauge_set("y", "not-a-number")  # type: ignore[arg-type]
    registry.observe("z", object())  # type: ignore[arg-type]
    # Nothing recorded, nothing raised.
    assert registry.snapshot() == {"counters": {}, "gauges": {}, "summaries": {}}


# ── /metrics endpoint ──────────────────────────────────────────────────────────


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.received.append(payload)


def _build_client() -> TestClient:
    app = FastAPI(title="Metrics Test App")
    app.dependency_overrides[get_orchestrator] = lambda: _DummyOrchestrator()
    app.include_router(webhook_router)
    app.include_router(metrics_router)
    app.state.job_queue = JobQueue()
    return TestClient(app)


def test_metrics_endpoint_exposes_queue_depth_and_counters():
    metrics.reset()
    client = _build_client()

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "sentinel_queue_depth 0" in response.text

    client.post("/webhook", json={"repo": "demo", "pr_number": 1})
    text = client.get("/metrics").text
    assert 'sentinel_webhooks_total{mode="queued"} 1' in text


def test_metrics_endpoint_survives_broken_queue():
    metrics.reset()
    client = _build_client()
    client.app.state.job_queue = object()  # no depth() at all
    response = client.get("/metrics")
    assert response.status_code == 200


# ── LLM outcome counters ───────────────────────────────────────────────────────


def _enrichable_finding() -> Finding:
    return Finding(
        rule="password_assignment",
        match="x",
        severity=SeverityLevel.HIGH,
        recommendation="rotate it",
    )


def test_llm_fallback_counted_when_disabled():
    metrics.reset()
    service = LLMService(enable_llm=False)
    service.generate_pr_audit("code", [_enrichable_finding()])
    assert metrics.snapshot()["counters"]['sentinel_llm_calls_total{outcome="fallback"}'] == 1


def test_llm_nothing_to_enrich_is_not_a_fallback():
    metrics.reset()
    service = LLMService(enable_llm=False)
    service.generate_pr_audit("code", [])
    assert metrics.snapshot()["counters"] == {}


class _OkProvider:
    def generate_pr_audit(self, code, findings_summary):
        return "Issue: x\nExplanation: fine\nFix: y"


def test_llm_success_counted():
    metrics.reset()
    service = LLMService(provider=_OkProvider(), enable_llm=True)
    service.generate_pr_audit("code", [_enrichable_finding()])
    assert metrics.snapshot()["counters"]['sentinel_llm_calls_total{outcome="success"}'] == 1


# ── queue depth ────────────────────────────────────────────────────────────────


def test_in_memory_queue_depth():
    async def _run() -> None:
        queue = JobQueue()
        await queue.enqueue({"repo": "a", "pr_number": 1})
        await queue.enqueue({"repo": "a", "pr_number": 2})
        assert await queue.depth() == 2

    asyncio.run(_run())
