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

    def generate_pr_audit(self, code: str, findings: list) -> dict[int, dict[str, str]]:
        _ = code
        self.call_count += 1
        result = {}
        for f in findings:
            severity_name = f.severity.value if isinstance(f.severity, SeverityLevel) else str(f.severity).upper()
            is_sec = f.type == "security"
            is_meaningful = severity_name != "MEDIUM" or bool(f.recommendation)
            if is_sec and severity_name != "LOW" and is_meaningful:
                result[id(f)] = {"explanation": self.explanation, "fix": self.fix}
        return result

    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        if "Translate" in issue:
            if "Hindi" in issue:
                return "Hindi text"
            if "Kannada" in issue:
                return "Kannada text"
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

    def analyze_code(self, code, *, source_label="inline"):
        _ = code
        _ = source_label
        if self.should_raise:
            raise RuntimeError("doc failure")
        return []





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
    orchestrator = AuditOrchestrator(
        JobQueue(),
        llm_service=llm,
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
    assert "Sentinel AI Code Review" in final_report
    assert "## Hindi Version" in final_report
    assert "## Kannada Version" in final_report
    assert doc.calls[0]["enable_llm_review"] is False


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
    llm = _LLMService(
        fix="Use parameterized queries or validate input.",
        explanation="Potential security issue detected. Review code manually.",
    )
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=llm)

    findings = [
        _security_finding(severity=SeverityLevel.HIGH),
        _security_finding(severity=SeverityLevel.LOW),
        _security_finding(severity=SeverityLevel.MEDIUM, recommendation=None),
    ]
    enriched = orchestrator.enrich_findings_with_llm("x", findings)

    assert enriched[0].fix_suggestion == "Use parameterized queries or validate input."
    assert enriched[0].explanation == "Potential security issue detected. Review code manually."
    assert enriched[1].fix_suggestion is None
    assert enriched[2].explanation is None
    assert llm.reset_calls == 1


def test_build_report_fallback_paths(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "false")
    findings = [_security_finding(severity=SeverityLevel.HIGH)]
    orchestrator = AuditOrchestrator(JobQueue())
    rendered = orchestrator.build_report(findings, SeverityLevel.HIGH)
    assert "Risk Score: HIGH" in rendered


def test_append_translations_disabled_and_failure_paths(monkeypatch):
    base_report = "# Sentinel AI Code Review"

    monkeypatch.setenv("ENABLE_TRANSLATION", "false")
    orchestrator = AuditOrchestrator(JobQueue())
    assert orchestrator.append_translations(base_report) == base_report

    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    no_translator = AuditOrchestrator(JobQueue(), llm_service=None)
    assert no_translator.append_translations(base_report) == base_report

    partial = AuditOrchestrator(JobQueue(), llm_service=_LLMService())
    rendered = partial.append_translations(base_report, ["Hindi"])
    assert "## Hindi Version" in rendered
    assert "Hindi text" in rendered
