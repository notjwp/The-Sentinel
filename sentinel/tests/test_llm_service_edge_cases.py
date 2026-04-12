from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.llm_service import LLMService


class _SuccessProvider:
    def __init__(self) -> None:
        self.fix_calls = 0
        self.explain_calls = 0

    def generate_fix(self, code: str, issue: str):
        self.fix_calls += 1
        return "  fixed_code  "

    def explain_issue(self, code: str, issue: str):
        self.explain_calls += 1
        return "  explanation  "


class _EmptyProvider:
    def generate_fix(self, code: str, issue: str):
        return ""

    def explain_issue(self, code: str, issue: str):
        return None


class _FailingProvider:
    def generate_fix(self, code: str, issue: str):
        raise RuntimeError("fix failure")

    def explain_issue(self, code: str, issue: str):
        raise RuntimeError("explain failure")


def test_llm_disabled_returns_fallback_and_no_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=False, max_calls=5)

    fix = service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH)
    explanation = service.explain_issue_safe("code", "issue", severity=SeverityLevel.HIGH)

    assert fix == "Fix suggestion unavailable"
    assert explanation == "Explanation unavailable"
    assert service.call_count == 0
    assert provider.fix_calls == 0
    assert provider.explain_calls == 0


def test_invalid_severity_string_does_not_invoke_provider():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)

    result = service.generate_fix_safe("code", "issue", severity="invalid")

    assert result == "Fix suggestion unavailable"
    assert service.call_count == 0


def test_success_results_are_stripped_and_non_empty():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)

    fix = service.generate_fix_safe("", "issue", severity=SeverityLevel.CRITICAL)
    explanation = service.explain_issue_safe("x" * 10000, "issue", severity=SeverityLevel.HIGH)

    assert fix == "fixed_code"
    assert explanation == "explanation"
    assert fix != ""
    assert explanation != ""


def test_provider_failure_and_empty_results_use_fallbacks():
    failing = LLMService(provider=_FailingProvider(), enable_llm=True, max_calls=5)
    assert failing.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH) == "Fix suggestion unavailable"
    assert failing.explain_issue_safe("code", "issue", severity=SeverityLevel.HIGH) == "Explanation unavailable"

    empty = LLMService(provider=_EmptyProvider(), enable_llm=True, max_calls=5)
    assert empty.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH) == "Fix suggestion unavailable"
    assert empty.explain_issue_safe("code", "issue", severity=SeverityLevel.HIGH) == "Explanation unavailable"


def test_call_limit_enforced_across_multiple_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=2)

    first = service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH)
    second = service.explain_issue_safe("code", "issue", severity=SeverityLevel.HIGH)
    third = service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH)

    assert first == "fixed_code"
    assert second == "explanation"
    assert third == "Fix suggestion unavailable"
    assert service.call_count == 2
    assert service.calls_made == 2


def test_reset_budget_allows_subsequent_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=1)

    assert service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH) == "fixed_code"
    assert service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH) == "Fix suggestion unavailable"

    service.reset_budget()

    assert service.call_count == 0
    assert service.generate_fix_safe("code", "issue", severity=SeverityLevel.HIGH) == "fixed_code"
