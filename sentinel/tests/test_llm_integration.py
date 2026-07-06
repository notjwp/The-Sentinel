from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.base import LLMProvider
from sentinel.infrastructure.llm.llm_service import LLMService
from sentinel.workers.job_queue import JobQueue


class FakeLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    def generate_pr_audit(self, code: str, findings_summary: str) -> str:
        self.calls += 1
        return "Issue:\nExplanation: explanation\nFix: fixed_code"


class FailingLLMProvider(LLMProvider):
    def generate_pr_audit(self, code: str, findings_summary: str) -> str:
        raise RuntimeError("failed")


def test_high_severity_triggers_llm_and_attaches_fields(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")

    provider = FakeLLMProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=service)

    findings = [
        Finding(
            rule="sql_injection",
            match="SELECT",
            severity=SeverityLevel.HIGH,
            description="SQL injection detected",
        )
    ]

    enriched = orchestrator.enrich_findings_with_llm("query = user_input", findings)

    assert enriched[0].explanation == "explanation"
    assert enriched[0].fix_suggestion == "fixed_code"
    assert provider.calls == 1


def test_low_severity_does_not_trigger_llm(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")

    provider = FakeLLMProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=service)

    findings = [
        Finding(
            rule="eval_call",
            match="eval(",
            severity=SeverityLevel.MEDIUM,
            description="Use of eval",
        )
    ]

    enriched = orchestrator.enrich_findings_with_llm("x = eval('2+2')", findings)

    assert enriched[0].explanation is None
    assert enriched[0].fix_suggestion is None
    assert provider.calls == 0


def test_fallback_values_are_used_on_provider_failure():
    service = LLMService(provider=FailingLLMProvider(), enable_llm=True, max_calls=5)
    
    findings = [
        Finding(rule="r1", match="m", severity=SeverityLevel.HIGH, description="issue")
    ]
    
    result = service.generate_pr_audit("code", findings)
    
    assert result[id(findings[0])]["fix"] == "Use parameterized queries or validate input."
    assert result[id(findings[0])]["explanation"] == "Potential security issue detected. Review code manually."


def test_llm_disabled_mode_skips_processing(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "false")

    provider = FakeLLMProvider()
    service = LLMService(provider=provider, enable_llm=False, max_calls=5)
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=service)

    findings = [
        Finding(
            rule="sql_injection",
            match="SELECT",
            severity=SeverityLevel.CRITICAL,
            description="SQL injection detected",
        )
    ]

    enriched = orchestrator.enrich_findings_with_llm("query = user_input", findings)

    assert enriched[0].explanation is None
    assert enriched[0].fix_suggestion is None
    assert provider.calls == 0



