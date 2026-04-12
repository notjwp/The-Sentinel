from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.webhook_controller import get_document_service
from sentinel.api.webhook_controller import get_github_client
from sentinel.api.webhook_controller import get_llm_service
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import get_report_service
from sentinel.api.webhook_controller import get_risk_engine
from sentinel.api.webhook_controller import get_security_service
from sentinel.api.webhook_controller import get_translator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.workers.job_queue import JobQueue


class _DummySecurityService:
    def analyze(self, code: str) -> dict:
        _ = code
        return {
            "findings": [
                Finding(
                    rule="sql_injection",
                    match="SELECT",
                    severity=SeverityLevel.HIGH,
                    category="Injection",
                    owasp_category="A03: Injection",
                    description="SQL injection detected",
                    recommendation="Use parameterized queries",
                )
            ],
            "severity": SeverityLevel.HIGH,
        }


class _DummyRiskEngine:
    def assess(self, code: str) -> dict:
        _ = code
        return {
            "severity": SeverityLevel.HIGH,
            "complexity": 12,
            "maintainability": 71.5,
            "semantic_findings_count": 2,
        }


class _DummyLLMService:
    call_count = 0

    def reset_budget(self) -> None:
        self.call_count = 0

    def generate_fix_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        self.call_count += 1
        return "safe_fix()"

    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        self.call_count += 1
        return "AI explanation"


class _DummyDocumentService:
    def analyze(self, files, file_contents=None, *, enable_llm_review=False, llm_reviewer=None):
        _ = files
        _ = file_contents
        _ = enable_llm_review
        _ = llm_reviewer
        return [
            Finding(
                rule="missing_usage",
                match="README.md",
                severity=SeverityLevel.MEDIUM,
                finding_type="documentation",
                category="Documentation",
                owasp_category="N/A",
                description="Documentation is missing usage guidance.",
                file="README.md",
                line=1,
                recommendation="Add usage examples.",
            )
        ]


class _DummyTranslator:
    def translate(self, text: str, language: str) -> str:
        _ = text
        return f"{language} translation"


class _DummyGitHubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> bool:
        self.calls.append((owner, repo, pr_number, body))
        return True


def _build_client(github_client: _DummyGitHubClient) -> TestClient:
    app = FastAPI(title="Phase4 Integration Test")
    app.dependency_overrides[get_orchestrator] = lambda: AuditOrchestrator(JobQueue())
    app.dependency_overrides[get_security_service] = lambda: _DummySecurityService()
    app.dependency_overrides[get_risk_engine] = lambda: _DummyRiskEngine()
    app.dependency_overrides[get_llm_service] = lambda: _DummyLLMService()
    app.dependency_overrides[get_report_service] = get_report_service
    app.dependency_overrides[get_document_service] = lambda: _DummyDocumentService()
    app.dependency_overrides[get_translator] = lambda: _DummyTranslator()
    app.dependency_overrides[get_github_client] = lambda: github_client
    app.include_router(webhook_router)
    return TestClient(app)


def test_webhook_phase4_includes_doc_findings_translations_and_posts_comment(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    monkeypatch.setenv("ENABLE_GITHUB", "true")

    github_client = _DummyGitHubClient()
    client = _build_client(github_client)

    response = client.post(
        "/webhook",
        json={
            "repo": "octo-repo",
            "pr_number": 42,
            "author": "octo-user",
            "files": ["README.md"],
            "code": "query = f\"SELECT * FROM users WHERE id={uid}\"",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processed"
    assert body["risk"] == "HIGH"
    assert "report" in body
    assert "## Hindi Version" in body["report"]
    assert "## Kannada Version" in body["report"]

    finding_types = {finding["type"] for finding in body["findings"]}
    assert "security" in finding_types
    assert "documentation" in finding_types

    assert len(github_client.calls) == 1
    posted_owner, posted_repo, posted_pr, posted_body = github_client.calls[0]
    assert posted_owner == "octo-user"
    assert posted_repo == "octo-repo"
    assert posted_pr == 42
    assert "Sentinel AI Code Review" in posted_body
