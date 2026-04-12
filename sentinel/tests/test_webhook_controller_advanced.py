from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.webhook_controller import get_llm_service
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import get_risk_engine
from sentinel.api.webhook_controller import get_security_service
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []
        self.llm_service = None
        self.raise_on_enqueue = False
        self.raise_on_enrich = False

    async def enqueue_pull_request(self, payload: dict) -> None:
        if self.raise_on_enqueue:
            raise RuntimeError("queue failure")
        self.enqueued.append(payload)

    def enrich_findings_with_llm(self, code: str, findings: list[Finding]) -> list[Finding]:
        if self.raise_on_enrich:
            raise RuntimeError("enrich failure")
        return findings


class _DummySecurityService:
    def __init__(self, findings: list[Finding] | None = None, should_raise: bool = False) -> None:
        self.findings = findings or []
        self.should_raise = should_raise

    def analyze(self, code: str) -> dict:
        if self.should_raise:
            raise RuntimeError("security failure")
        return {
            "findings": self.findings,
            "severity": SeverityLevel.HIGH if self.findings else SeverityLevel.LOW,
        }


class _DummyRiskEngine:
    def __init__(self, severity: SeverityLevel = SeverityLevel.HIGH, should_raise: bool = False) -> None:
        self.severity = severity
        self.should_raise = should_raise

    def assess(self, code: str) -> dict:
        if self.should_raise:
            raise RuntimeError("risk failure")
        return {"severity": self.severity}


class _DummyLLMService:
    pass


def _build_client(
    orchestrator: _DummyOrchestrator,
    security_service: _DummySecurityService,
    risk_engine: _DummyRiskEngine,
    llm_service: _DummyLLMService,
) -> TestClient:
    app = FastAPI(title="Webhook Advanced Test")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_security_service] = lambda: security_service
    app.dependency_overrides[get_risk_engine] = lambda: risk_engine
    app.dependency_overrides[get_llm_service] = lambda: llm_service
    app.include_router(webhook_router)
    return TestClient(app)


def test_webhook_synchronous_mode_serializes_findings():
    finding = Finding(
        rule="sql_injection",
        match="SELECT",
        severity=SeverityLevel.HIGH,
        category="Injection",
        owasp_category="A03: Injection",
        description="SQL injection detected",
        file=None,
        line=None,
        recommendation="Use parameterized queries",
        explanation="Untrusted input can alter query semantics.",
        fix_suggestion="cursor.execute(query, (user_input,))",
    )
    orchestrator = _DummyOrchestrator()
    client = _build_client(
        orchestrator=orchestrator,
        security_service=_DummySecurityService(findings=[finding]),
        risk_engine=_DummyRiskEngine(severity=SeverityLevel.HIGH),
        llm_service=_DummyLLMService(),
    )

    response = client.post(
        "/webhook",
        json={"repo": "demo", "pr_number": 1, "author": "u", "code": "x = 1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processed"
    assert body["risk"] == "HIGH"
    assert len(body["findings"]) == 1
    rendered = body["findings"][0]
    assert rendered["type"] == "security"
    assert rendered["category"] == "Injection"
    assert rendered["owasp_category"] == "A03: Injection"
    assert rendered["severity"] == "HIGH"
    assert rendered["description"] == "SQL injection detected"
    assert rendered["file"] == "unknown"
    assert rendered["line"] == 1
    assert rendered["recommendation"] == "Use parameterized queries"
    assert rendered["explanation"]
    assert rendered["fix_suggestion"]
    assert orchestrator.llm_service is not None


def test_webhook_async_mode_queues_when_code_absent():
    orchestrator = _DummyOrchestrator()
    client = _build_client(
        orchestrator=orchestrator,
        security_service=_DummySecurityService(),
        risk_engine=_DummyRiskEngine(),
        llm_service=_DummyLLMService(),
    )

    response = client.post("/webhook", json={"repo": "demo", "pr_number": 2, "author": "alice"})

    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    assert orchestrator.enqueued == [{"repo": "demo", "pr_number": 2, "author": "alice"}]


def test_webhook_invalid_body_returns_422():
    orchestrator = _DummyOrchestrator()
    client = _build_client(
        orchestrator=orchestrator,
        security_service=_DummySecurityService(),
        risk_engine=_DummyRiskEngine(),
        llm_service=_DummyLLMService(),
    )

    response = client.post("/webhook", json=["bad", "payload"])

    assert response.status_code == 422


def test_webhook_synchronous_processing_exception_returns_error_status():
    orchestrator = _DummyOrchestrator()
    client = _build_client(
        orchestrator=orchestrator,
        security_service=_DummySecurityService(should_raise=True),
        risk_engine=_DummyRiskEngine(),
        llm_service=_DummyLLMService(),
    )

    response = client.post("/webhook", json={"repo": "demo", "pr_number": 3, "code": "x"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "security failure" in body["message"]


def test_webhook_queue_exception_returns_500_http_error():
    orchestrator = _DummyOrchestrator()
    orchestrator.raise_on_enqueue = True
    client = _build_client(
        orchestrator=orchestrator,
        security_service=_DummySecurityService(),
        risk_engine=_DummyRiskEngine(),
        llm_service=_DummyLLMService(),
    )

    response = client.post("/webhook", json={"repo": "demo", "pr_number": 4})

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to queue webhook payload"}
