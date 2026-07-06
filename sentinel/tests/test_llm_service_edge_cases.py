from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.llm_service import LLMService


class _SuccessProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate_pr_audit(self, code: str, findings_summary: str):
        self.calls += 1
        return "Issue:\nExplanation: explanation\nFix: fixed_code"


class _EmptyProvider:
    def generate_pr_audit(self, code: str, summary: str):
        return ""


class _FailingProvider:
    def generate_pr_audit(self, code: str, summary: str):
        raise RuntimeError("failure")


def _finding(severity=SeverityLevel.HIGH, rule="r1", finding_type="security") -> Finding:
    return Finding(rule=rule, match="m", severity=severity, finding_type=finding_type, description="d")


def test_llm_disabled_returns_fallback_and_no_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=False, max_calls=5)

    f = _finding()
    result = service.generate_pr_audit("code", [f])

    assert result[id(f)]["fix"] == "Use parameterized queries or validate input."
    assert result[id(f)]["explanation"] == "Potential security issue detected. Review code manually."
    assert service.call_count == 0
    assert provider.calls == 0


def test_invalid_severity_string_does_not_invoke_provider():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)

    f = _finding(severity="LOW")
    result = service.generate_pr_audit("code", [f])

    assert id(f) not in result
    assert service.call_count == 0


def test_success_results_are_stripped_and_non_empty():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)

    f = _finding(severity=SeverityLevel.CRITICAL)
    result = service.generate_pr_audit("", [f])

    assert result[id(f)]["fix"] == "fixed_code"
    assert result[id(f)]["explanation"] == "explanation"


def test_provider_failure_and_empty_results_use_fallbacks():
    f = _finding()
    
    failing = LLMService(provider=_FailingProvider(), enable_llm=True, max_calls=5)
    result_fail = failing.generate_pr_audit("code", [f])
    assert result_fail[id(f)]["fix"] == "Use parameterized queries or validate input."
    assert result_fail[id(f)]["explanation"] == "Potential security issue detected. Review code manually."

    empty = LLMService(provider=_EmptyProvider(), enable_llm=True, max_calls=5)
    result_empty = empty.generate_pr_audit("code", [f])
    assert result_empty[id(f)]["fix"] == "Use parameterized queries or validate input."
    assert result_empty[id(f)]["explanation"] == "Potential security issue detected. Review code manually."


def test_call_limit_enforced_across_multiple_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=2)

    f1 = _finding(rule="1")
    f2 = _finding(rule="2")
    f3 = _finding(rule="3")

    first = service.generate_pr_audit("code", [f1])
    second = service.generate_pr_audit("code", [f2])
    third = service.generate_pr_audit("code", [f3])

    assert first[id(f1)]["fix"] == "fixed_code"
    assert second[id(f2)]["explanation"] == "explanation"
    assert third[id(f3)]["fix"] == "Use parameterized queries or validate input."
    assert service.call_count == 2
    assert service.calls_made == 2


def test_reset_budget_allows_subsequent_calls():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=1)

    f = _finding()
    assert service.generate_pr_audit("code", [f])[id(f)]["fix"] == "fixed_code"
    assert service.generate_pr_audit("code", [f])[id(f)]["fix"] == "Use parameterized queries or validate input."

    service.reset_budget()

    assert service.call_count == 0
    assert service.generate_pr_audit("code", [f])[id(f)]["fix"] == "fixed_code"


def test_analyze_issue_safe_parses_explanation_and_fix():
    provider = _SuccessProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)

    f = _finding()
    result = service.generate_pr_audit("code", [f])

    assert result[id(f)]["explanation"] == "explanation"
    assert result[id(f)]["fix"] == "fixed_code"
    assert service.call_count == 1
    assert provider.calls == 1
