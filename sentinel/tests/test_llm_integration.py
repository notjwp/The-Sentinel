from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.base import LLMProvider
from sentinel.infrastructure.llm.llm_service import LLMService
from sentinel.workers.job_queue import JobQueue


class FakeLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.fix_calls = 0
        self.explain_calls = 0

    def generate_fix(self, code: str, issue: str) -> str:
        self.fix_calls += 1
        return "fixed_code"

    def explain_issue(self, code: str, issue: str) -> str:
        self.explain_calls += 1
        return "explanation"


class FailingLLMProvider(LLMProvider):
    def generate_fix(self, code: str, issue: str) -> str:
        raise RuntimeError("fix failed")

    def explain_issue(self, code: str, issue: str) -> str:
        raise RuntimeError("explanation failed")


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
    assert provider.fix_calls == 1
    assert provider.explain_calls == 1


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
    assert provider.fix_calls == 0
    assert provider.explain_calls == 0


def test_fallback_values_are_used_on_provider_failure():
    service = LLMService(provider=FailingLLMProvider(), enable_llm=True, max_calls=5)

    fix = service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH)
    explanation = service.explain_issue_safe("code", "issue", severity=SeverityLevel.HIGH)

    assert fix == "Fix suggestion unavailable"
    assert explanation == "Explanation unavailable"


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
    assert provider.fix_calls == 0
    assert provider.explain_calls == 0


def test_max_calls_limit_is_enforced_for_multiple_findings(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")

    provider = FakeLLMProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=2)
    orchestrator = AuditOrchestrator(JobQueue(), llm_service=service)

    findings = [
        Finding(rule="r1", match="m1", severity=SeverityLevel.HIGH, description="issue 1"),
        Finding(rule="r2", match="m2", severity=SeverityLevel.HIGH, description="issue 2"),
    ]

    enriched = orchestrator.enrich_findings_with_llm("code", findings)

    assert enriched[0].fix_suggestion == "fixed_code"
    assert enriched[0].explanation == "explanation"
    assert enriched[1].fix_suggestion == "Fix suggestion unavailable"
    assert enriched[1].explanation == "Explanation unavailable"
    assert provider.fix_calls == 1
    assert provider.explain_calls == 1
