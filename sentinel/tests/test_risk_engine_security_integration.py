from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.value_objects.severity_level import SeverityLevel


def test_single_high_security_finding_sets_overall_high():
    code = 'api_key = "abc"'
    result = RiskEngine().assess(code=code)

    assert result["security"]["severity"] == SeverityLevel.HIGH
    assert result["severity"] == SeverityLevel.HIGH


def test_multiple_low_signals_stay_low_when_security_has_no_findings():
    code = "\n".join(["def a(): return 1", "def b(): return 2", "def c(): return 3"])
    result = RiskEngine().assess(code=code)

    assert result["security"]["findings"] == []
    assert result["security"]["severity"] == SeverityLevel.LOW
    assert result["severity"] == SeverityLevel.LOW


def test_mixed_security_severities_use_highest_dominance():
    code = "\n".join(
        [
            'x = eval("2+2")',
            'api_key = "abc"',
            'query = f"SELECT id FROM users WHERE id={user_id}"',
        ]
    )
    result = RiskEngine().assess(code=code)

    assert result["security"]["severity"] == SeverityLevel.CRITICAL
    assert result["severity"] == SeverityLevel.CRITICAL


def test_no_security_findings_results_in_low_overall_for_simple_code():
    code = "def safe(x):\n    return x + 1"
    result = RiskEngine().assess(code=code)

    assert result["security_findings_count"] == 0
    assert result["security"]["severity"] == SeverityLevel.LOW
    assert result["severity"] == SeverityLevel.LOW
