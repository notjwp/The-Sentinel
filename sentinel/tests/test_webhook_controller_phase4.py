from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api import webhook_controller as wc
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class _DummyOrchestrator:
    def __init__(self, *, use_full_review: bool = False, raise_full_review: bool = False) -> None:
        self.enqueued: list[dict] = []
        self.llm_service = None
        self.raise_full_review = raise_full_review
        self.use_full_review = use_full_review

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.enqueued.append(payload)

    def enrich_findings_with_llm(self, code: str, findings: list[Finding]) -> list[Finding]:
        _ = code
        return findings

    def _run_full_review(
        self,
        *,
        code: str,
        findings: list[Finding],
        risk,
        files=None,
        file_contents=None,
        complexity=None,
        maintainability=None,
        semantic_findings_count=None,
    ):
        _ = code
        _ = risk
        _ = files
        _ = file_contents
        _ = complexity
        _ = maintainability
        _ = semantic_findings_count
        if self.raise_full_review:
            raise RuntimeError("orchestrator blew up")
        if not self.use_full_review:
            raise AttributeError("should not be called")
        return findings, "report-from-orchestrator"

    def __getattr__(self, name: str):
        if name == "run_full_review" and self.use_full_review:
            return self._run_full_review
        raise AttributeError(name)


class _SecurityService:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self.findings = findings or []

    def analyze(self, code: str) -> dict:
        _ = code
        severity = SeverityLevel.HIGH if self.findings else SeverityLevel.LOW
        return {"findings": list(self.findings), "severity": severity}


class _RiskEngine:
    def __init__(self, severity: SeverityLevel = SeverityLevel.HIGH, findings: list[Finding] | None = None) -> None:
        self.severity = severity
        self.findings = findings or []

    def assess(self, code: str) -> dict:
        _ = code
        return {
            "severity": self.severity,
            "complexity": 9,
            "maintainability": 91.2,
            "security_findings_count": len(self.findings),
            "security": {
                "findings": list(self.findings),
                "severity": self.severity,
            },
            "semantic_findings_count": 0,
            "semantic": {"findings": [], "severity": SeverityLevel.LOW},
        }


class _LLMService:
    pass


class _DocumentService:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self.findings = findings or []
        self.calls: list[dict] = []

    def analyze(self, files, file_contents=None, *, enable_llm_review=False, llm_reviewer=None):
        self.calls.append(
            {
                "files": files,
                "file_contents": file_contents,
                "enable_llm_review": enable_llm_review,
                "llm_reviewer": llm_reviewer,
            }
        )
        return list(self.findings)

    def analyze_code(self, code, *, source_label="inline"):
        _ = code
        _ = source_label
        return []


class _ReportService:
    def format_report(self, findings, risk, *, complexity=None, maintainability=None, semantic_findings_count=None):
        _ = complexity
        _ = maintainability
        _ = semantic_findings_count
        risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk)
        return f"report:{risk_value}:{len(findings)}"


class _Translator:
    def __init__(self, *, should_raise: bool = False, value: str = "translated") -> None:
        self.should_raise = should_raise
        self.value = value
        self.calls: list[str] = []

    def translate(self, text: str, language: str) -> str:
        _ = text
        self.calls.append(language)
        if self.should_raise:
            raise RuntimeError("translation failure")
        return self.value


class _GitHubClient:
    def __init__(self, *, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls: list[tuple[str, str, int, str]] = []

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> bool:
        if self.should_raise:
            raise RuntimeError("github failure")
        self.calls.append((owner, repo, pr_number, body))
        return True


class _RaisingJsonRequest:
    async def json(self):
        raise RuntimeError("bad json")


class _ListJsonRequest:
    async def json(self):
        return ["not", "a", "dict"]


class _DictJsonRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self):
        return self.payload


def _security_finding(rule: str = "sql_injection") -> Finding:
    return Finding(
        rule=rule,
        match="SELECT",
        severity=SeverityLevel.HIGH,
        category="Injection",
        owasp_category="A03: Injection",
        description="issue",
        recommendation="use parameters",
    )


def _doc_finding() -> Finding:
    return Finding(
        rule="missing_usage",
        match="README.md",
        severity=SeverityLevel.MEDIUM,
        finding_type="documentation",
        category="Documentation",
        owasp_category="N/A",
        description="missing usage",
        file="README.md",
        line=1,
        recommendation="add usage",
    )


def _build_client(
    orchestrator: _DummyOrchestrator,
    security_service: _SecurityService,
    risk_engine: _RiskEngine,
    llm_service: _LLMService,
    document_service: _DocumentService,
    translator: _Translator,
    github_client,
) -> TestClient:
    app = FastAPI(title="Webhook Phase4 Coverage")
    app.dependency_overrides[wc.get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[wc.get_security_service] = lambda: security_service
    app.dependency_overrides[wc.get_risk_engine] = lambda: risk_engine
    app.dependency_overrides[wc.get_llm_service] = lambda: llm_service
    app.dependency_overrides[wc.get_report_service] = lambda: _ReportService()
    app.dependency_overrides[wc.get_document_service] = lambda: document_service
    app.dependency_overrides[wc.get_translator] = lambda: translator
    app.dependency_overrides[wc.get_github_client] = lambda: github_client
    app.include_router(wc.router)
    return TestClient(app)


def test_helper_extractors_cover_branch_variants():
    assert wc._as_dict([]) == {}

    assert wc._extract_repo_name({"repository": {"full_name": "octo/repo"}}, None) == "repo"
    assert wc._extract_repo_name({"repository": {"name": "repo2"}}, None) == "repo2"

    assert wc._extract_owner({"repository": {"owner": {"login": "octo"}}}, None) == "octo"
    assert wc._extract_owner({"repository": {"full_name": "octo/repo"}}, None) == "octo"
    assert wc._extract_owner({}, "foo/bar") == "foo"

    assert wc._extract_pr_number({"pull_request": {"number": 8}}, None) == 8
    assert wc._extract_pr_number({"number": 9}, None) == 9

    assert wc._extract_author({"pull_request": {"user": {"login": "alice"}}}, None) == "alice"
    assert wc._extract_author({"sender": {"login": "bob"}}, None) == "bob"

    extracted_files = wc._extract_files(
        {"files": ["README.md", {"filename": "a.md"}, {"path": "b.txt"}]},
        None,
    )
    assert extracted_files == ["README.md", "a.md", "b.txt"]

    contents = wc._extract_file_contents(
        {
            "files": [
                {"filename": "README.md", "content": "hello"},
                {"path": "docs.txt", "patch": "+x"},
            ]
        }
    )
    assert contents["README.md"] == "hello"
    assert contents["docs.txt"] == "+x"


def test_append_report_translations_paths():
    translator = _Translator()
    assert wc._append_report_translations("report", translator, enable_translation=False) == "report"

    failing = _Translator(should_raise=True)
    assert wc._append_report_translations("report", failing, enable_translation=True) == "report"

    success = _Translator(value="ok")
    translated = wc._append_report_translations("report", success, enable_translation=True)
    assert "## Hindi Version" in translated
    assert "## Kannada Version" in translated


def test_get_github_client_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "false")
    assert wc.get_github_client() is None


def test_webhook_malformed_payload_returns_422():
    app = FastAPI()
    app.dependency_overrides[wc.get_orchestrator] = lambda: _DummyOrchestrator()
    app.include_router(wc.router)
    client = TestClient(app)

    response = client.post("/webhook", json=["bad"])
    assert response.status_code == 422


def test_webhook_async_mode_with_github_payload_populates_queue_fields(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "false")
    orchestrator = _DummyOrchestrator()

    from asyncio import run

    result = run(
        wc._webhook_impl(
            _DictJsonRequest(
                {
                    "action": "opened",
                    "repository": {"full_name": "octo/repo"},
                    "pull_request": {"number": 4, "user": {"login": "alice"}},
                    "files": [{"filename": "README.md"}],
                }
            ),
            wc.WebhookPayload(),
            orchestrator,
            _SecurityService(),
            _RiskEngine(),
            _LLMService(),
            _ReportService(),
            _DocumentService(),
            _Translator(),
            None,
        )
    )

    assert result == {"status": "queued"}
    assert orchestrator.enqueued == [{"repo": "repo", "pr_number": 4, "author": "alice", "files": ["README.md"]}]


def test_webhook_sync_mode_with_code_and_github_enabled_posts_comment(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "true")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    monkeypatch.setenv("ENABLE_LLM", "true")

    orchestrator = _DummyOrchestrator(use_full_review=True)
    security = _SecurityService(findings=[_security_finding()])
    doc = _DocumentService(findings=[_doc_finding()])
    translator = _Translator(value="translated")
    github = _GitHubClient()
    client = _build_client(orchestrator, security, _RiskEngine(findings=[_security_finding()]), _LLMService(), doc, translator, github)

    response = client.post(
        "/webhook",
        json={
            "repo": "my-repo",
            "pr_number": 11,
            "author": "owner-fallback",
            "code": "print('x')",
            "files": ["README.md"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processed"
    assert payload["risk"] == "HIGH"
    assert len(github.calls) == 1
    assert github.calls[0][0] == "owner-fallback"


def test_webhook_sync_mode_fallback_path_toggles_doc_and_translation(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "false")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "false")
    monkeypatch.setenv("ENABLE_TRANSLATION", "false")
    monkeypatch.setenv("ENABLE_LLM", "false")

    orchestrator = _DummyOrchestrator(use_full_review=False)
    security = _SecurityService(findings=[_security_finding()])
    doc = _DocumentService(findings=[_doc_finding()])
    translator = _Translator(value="translated")
    client = _build_client(orchestrator, security, _RiskEngine(), _LLMService(), doc, translator, None)

    response = client.post("/webhook", json={"repo": "demo", "pr_number": 7, "code": "x = 1"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processed"
    assert "report" in body
    assert translator.calls == []
    assert doc.calls == []


def test_webhook_sync_mode_handles_orchestrator_and_github_exceptions(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "true")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")

    # Exception inside orchestrator full review is converted to status=error
    failing_orchestrator = _DummyOrchestrator(use_full_review=True, raise_full_review=True)
    client_error = _build_client(
        failing_orchestrator,
        _SecurityService(findings=[_security_finding()]),
        _RiskEngine(),
        _LLMService(),
        _DocumentService(),
        _Translator(),
        _GitHubClient(),
    )
    error_response = client_error.post("/webhook", json={"repo": "demo", "pr_number": 8, "code": "x"})
    assert error_response.status_code == 200
    assert error_response.json()["status"] == "error"

    # Exception in GitHub client does not crash processing
    ok_orchestrator = _DummyOrchestrator(use_full_review=True)
    client_github_error = _build_client(
        ok_orchestrator,
        _SecurityService(findings=[_security_finding()]),
        _RiskEngine(),
        _LLMService(),
        _DocumentService(),
        _Translator(),
        _GitHubClient(should_raise=True),
    )
    github_error_response = client_github_error.post(
        "/webhook",
        json={"repo": "demo", "pr_number": 9, "author": "bob", "code": "x"},
    )
    assert github_error_response.status_code == 200
    assert github_error_response.json()["status"] == "processed"


def test_webhook_sync_mode_empty_multiple_and_large_findings_payloads(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "false")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "false")
    monkeypatch.setenv("ENABLE_TRANSLATION", "false")

    orchestrator = _DummyOrchestrator(use_full_review=False)
    client_empty = _build_client(
        orchestrator,
        _SecurityService(findings=[]),
        _RiskEngine(severity=SeverityLevel.LOW),
        _LLMService(),
        _DocumentService(),
        _Translator(),
        None,
    )
    empty_response = client_empty.post("/webhook", json={"repo": "demo", "pr_number": 5, "code": "x"})
    assert empty_response.status_code == 200
    assert empty_response.json()["findings"] == []

    many_findings = [_security_finding("r1"), _security_finding("r2")]
    client_many = _build_client(
        _DummyOrchestrator(use_full_review=False),
        _SecurityService(findings=many_findings),
        _RiskEngine(findings=many_findings),
        _LLMService(),
        _DocumentService(),
        _Translator(),
        None,
    )
    many_response = client_many.post("/webhook", json={"repo": "demo", "pr_number": 6, "code": "x"})
    assert many_response.status_code == 200
    assert len(many_response.json()["findings"]) == 2

    large_code = "x" * (1024 * 1024)
    large_response = client_many.post("/webhook", json={"repo": "demo", "pr_number": 10, "code": large_code})
    assert large_response.status_code == 200


def test_webhook_direct_call_handles_request_json_failures_and_non_dict(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "false")

    orchestrator = _DummyOrchestrator(use_full_review=False)
    payload = wc.WebhookPayload(repo="demo", pr_number=3, author="alice", code="x")

    from asyncio import run

    response_from_exception = run(
        wc._webhook_impl(
            _RaisingJsonRequest(),
            payload,
            orchestrator,
            _SecurityService(findings=[_security_finding()]),
            _RiskEngine(),
            _LLMService(),
            _ReportService(),
            _DocumentService(),
            _Translator(),
            None,
        )
    )
    assert response_from_exception["status"] == "processed"

    payload_no_code = wc.WebhookPayload()
    response_from_list = run(
        wc._webhook_impl(
            _ListJsonRequest(),
            payload_no_code,
            orchestrator,
            _SecurityService(),
            _RiskEngine(),
            _LLMService(),
            _ReportService(),
            _DocumentService(),
            _Translator(),
            None,
        )
    )
    assert response_from_list == {"status": "queued"}


def test_webhook_direct_call_owner_fallback_to_author(monkeypatch):
    monkeypatch.setenv("ENABLE_GITHUB", "true")

    github = _GitHubClient()
    from asyncio import run

    response = run(
        wc._webhook_impl(
            _DictJsonRequest({"code": "x"}),
            wc.WebhookPayload(repo="plain-repo", pr_number=33, author="fallback-owner", code="x"),
            _DummyOrchestrator(use_full_review=True),
            _SecurityService(findings=[_security_finding()]),
            _RiskEngine(),
            _LLMService(),
            _ReportService(),
            _DocumentService(),
            _Translator(),
            github,
        )
    )

    assert response["status"] == "processed"
    assert github.calls[0][0] == "fallback-owner"
