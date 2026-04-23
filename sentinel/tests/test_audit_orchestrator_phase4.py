from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.workers.job_queue import JobQueue


class _LLMService:
    def __init__(self, *, fix: str = "fixed", explanation: str = "explained") -> None:
        self.fix = fix
        self.explanation = explanation
        self.call_count = 0
        self.reset_calls = 0

    def reset_budget(self) -> None:
        self.call_count = 0
        self.reset_calls += 1

    def generate_fix_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        self.call_count += 1
        return self.fix

    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        self.call_count += 1
        return self.explanation


class _DocumentService:
    def __init__(self, findings: list[Finding] | None = None, should_raise: bool = False) -> None:
        self.findings = findings or []
        self.should_raise = should_raise
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
        if self.should_raise:
            raise RuntimeError("doc failure")
        return list(self.findings)


class _ReportService:
    def __init__(self, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls: list[dict] = []

    def format_report(
        self,
        findings: list[Finding],
        risk,
        *,
        complexity=None,
        maintainability=None,
        semantic_findings_count=None,
    ) -> str:
        self.calls.append(
            {
                "findings": findings,
                "risk": risk,
                "complexity": complexity,
                "maintainability": maintainability,
                "semantic_findings_count": semantic_findings_count,
            }
        )
        if self.should_raise:
            raise RuntimeError("report failed")
        risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk)
        return f"report:{risk_value}:{len(findings)}"


class _Translator:
    def __init__(self, responses: dict[str, str] | None = None, raise_languages: set[str] | None = None) -> None:
        self.responses = responses or {}
        self.raise_languages = raise_languages or set()
        self.calls: list[str] = []

    def translate(self, text: str, language: str) -> str:
        _ = text
        self.calls.append(language)
        if language in self.raise_languages:
            raise RuntimeError("translation failure")
        return self.responses.get(language, "")


def _security_finding(*, severity: SeverityLevel, recommendation: str | None = "do this") -> Finding:
    return Finding(
        rule="rule",
        match="match",
        severity=severity,
        description="issue",
        recommendation=recommendation,
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


def test_run_full_review_all_engines_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")

    llm = _LLMService()
    doc = _DocumentService(findings=[_doc_finding()])
    report = _ReportService()
    translator = _Translator(responses={"Hindi": "Hindi text", "Kannada": "Kannada text"})
    orchestrator = AuditOrchestrator(
        JobQueue(),
        llm_service=llm,
        report_service=report,
        translator=translator,
        document_service=doc,
    )

    findings, final_report = orchestrator.run_full_review(
        code="x",
        findings=[_security_finding(severity=SeverityLevel.HIGH)],
        risk=SeverityLevel.HIGH,
        files=["README.md"],
        file_contents={"README.md": "content"},
        complexity=7,
        maintainability=88.0,
        semantic_findings_count=1,
    )

    assert len(findings) == 2
    assert findings[0].fix_suggestion == "fixed"
    assert findings[0].explanation == "explained"
    assert "report:HIGH:2" in final_report
    assert "## Hindi Version" in final_report
    assert "## Kannada Version" in final_report
    assert doc.calls[0]["enable_llm_review"] is True


def test_collect_document_findings_guards(monkeypatch):
    doc = _DocumentService(findings=[_doc_finding()])
    orchestrator = AuditOrchestrator(JobQueue(), document_service=doc)

    monkeypatch.setenv("ENABLE_DOC_REVIEW", "false")
    assert orchestrator.collect_document_findings(["README.md"]) == []

    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    no_service = AuditOrchestrator(JobQueue(), document_service=None)
    assert no_service.collect_document_findings(["README.md"]) == []
    assert orchestrator.collect_document_findings(None) == []


def test_collect_document_findings_handles_service_failure(monkeypatch):
    monkeypatch.setenv("ENABLE_DOC_REVIEW", "true")
    doc = _DocumentService(should_raise=True)
    orchestrator = AuditOrchestrator(JobQueue(), document_service=doc)

    assert orchestrator.collect_document_findings(["README.md"]) == []


def test_enrich_findings_no_findings_and_llm_disabled(monkeypatch):
    llm = _LLMService()
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=llm)

    monkeypatch.setenv("ENABLE_LLM", "true")
    assert orchestrator.enrich_findings_with_llm("x", []) == []

    monkeypatch.setenv("ENABLE_LLM", "false")
    findings = [_security_finding(severity=SeverityLevel.HIGH)]
    assert orchestrator.enrich_findings_with_llm("x", findings) == findings


def test_enrich_findings_multiple_findings_with_fallback(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    llm = _LLMService(fix="Fix suggestion unavailable", explanation="Explanation unavailable")
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=llm)

    findings = [
        _security_finding(severity=SeverityLevel.HIGH),
        _security_finding(severity=SeverityLevel.LOW),
        _security_finding(severity=SeverityLevel.MEDIUM, recommendation=None),
    ]
    enriched = orchestrator.enrich_findings_with_llm("x", findings)

    assert enriched[0].fix_suggestion == "Fix suggestion unavailable"
    assert enriched[0].explanation == "Explanation unavailable"
    assert enriched[1].fix_suggestion is None
    assert enriched[2].explanation is None
    assert llm.reset_calls == 1


def test_build_report_fallback_paths(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "false")
    findings = [_security_finding(severity=SeverityLevel.HIGH)]

    no_report_service = AuditOrchestrator(JobQueue(), report_service=None)
    rendered = no_report_service.build_report(findings, SeverityLevel.HIGH)
    assert "Risk Score: HIGH" in rendered

    failing_report = _ReportService(should_raise=True)
    orchestrator = AuditOrchestrator(JobQueue(), report_service=failing_report)
    rendered_on_error = orchestrator.build_report(findings, "medium")
    assert "Risk Score: MEDIUM" in rendered_on_error


def test_append_translations_disabled_and_failure_paths(monkeypatch):
    base_report = "# Sentinel AI Code Review"

    monkeypatch.setenv("ENABLE_TRANSLATION", "false")
    orchestrator = AuditOrchestrator(JobQueue(), translator=_Translator())
    assert orchestrator.append_translations(base_report) == base_report

    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    no_translator = AuditOrchestrator(JobQueue(), translator=None)
    assert no_translator.append_translations(base_report) == base_report

    failing = AuditOrchestrator(
        JobQueue(),
        translator=_Translator(raise_languages={"Hindi", "Kannada"}),
    )
    assert failing.append_translations(base_report) == base_report

    empty = AuditOrchestrator(JobQueue(), translator=_Translator(responses={"Hindi": "", "Kannada": ""}))
    assert empty.append_translations(base_report) == base_report

    partial = AuditOrchestrator(JobQueue(), translator=_Translator(responses={"Hindi": "translated"}))
    rendered = partial.append_translations(base_report)
    assert "## Hindi Version" in rendered
    assert "translated" in rendered
