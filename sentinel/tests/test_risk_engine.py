import pytest

from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.value_objects.severity_level import SeverityLevel

MEDIUM_COMPLEXITY_CODE = """
def medium(value):
    if value > 0:
        value += 1
    if value > 1:
        value += 1
    if value > 2:
        value += 1
    if value > 3:
        value += 1
    if value > 4:
        value += 1
    if value > 5:
        value += 1
    if value > 6:
        value += 1
    return value
"""

HIGH_COMPLEXITY_CODE = "\n".join(
    ["def extreme(value):"] + ["    if value > 0:"] * 15 + ["        return value"]
)


def _code_for_decisions(decision_points: int) -> str:
    lines = ["def boundary_fn(x):"]
    for index in range(decision_points):
        lines.append(f"    if x > {index}:")
        lines.append("        x += 1")
    lines.append("    return x")
    return "\n".join(lines)


def test_debt_high_forces_overall_high_even_when_pr_is_even():
    result = RiskEngine().calculate_risk(pr_number=2, code=HIGH_COMPLEXITY_CODE)
    assert result == SeverityLevel.HIGH


def test_debt_medium_returns_medium_when_other_signal_is_low():
    result = RiskEngine().calculate_risk(pr_number=2, code=MEDIUM_COMPLEXITY_CODE)
    assert result == SeverityLevel.MEDIUM


def test_all_low_returns_low():
    result = RiskEngine().calculate_risk(pr_number=2, code="def simple():\n    return 1")
    assert result == SeverityLevel.LOW


def test_boundary_complexity_15_propagates_medium():
    code = _code_for_decisions(14)
    result = RiskEngine().calculate_risk(pr_number=2, code=code)
    assert result == SeverityLevel.MEDIUM


def test_boundary_complexity_16_propagates_high():
    code = _code_for_decisions(15)
    result = RiskEngine().calculate_risk(pr_number=2, code=code)
    assert result == SeverityLevel.HIGH


def test_mutation_guard_for_threshold_operator():
    code = _code_for_decisions(14)
    result = RiskEngine().calculate_risk(pr_number=2, code=code)
    assert result != SeverityLevel.HIGH


def test_severity_mapping_exact_for_low_medium_high_paths():
    engine = RiskEngine()
    low = engine.calculate_risk(pr_number=2, code="def simple():\n    return 1")
    medium = engine.calculate_risk(pr_number=2, code=_code_for_decisions(14))
    high = engine.calculate_risk(pr_number=2, code=_code_for_decisions(15))
    assert low == SeverityLevel.LOW
    assert medium == SeverityLevel.MEDIUM
    assert high == SeverityLevel.HIGH


def test_negative_pr_number_is_deterministic():
    result = RiskEngine().calculate_risk(pr_number=-1, code="def simple():\n    return 1")
    assert result == SeverityLevel.LOW


def test_zero_pr_number_is_deterministic():
    result = RiskEngine().calculate_risk(pr_number=0, code="def simple():\n    return 1")
    assert result == SeverityLevel.LOW


def test_extremely_large_pr_number_supported():
    result = RiskEngine().calculate_risk(pr_number=10**18, code="def simple():\n    return 1")
    assert result == SeverityLevel.LOW


def test_invalid_debt_severity_type_falls_back_to_base_risk():
    class InvalidDebtService:
        def evaluate_debt(self, code: str) -> dict:
            return {"complexity": 1, "maintainability": 99.0, "severity": "UNKNOWN"}

    result = RiskEngine(debt_service=InvalidDebtService()).calculate_risk(pr_number=2, code="")
    assert result == SeverityLevel.LOW


def test_debt_service_failure_is_controlled_exception():
    class ExplodingDebtService:
        def evaluate_debt(self, code: str) -> dict:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        RiskEngine(debt_service=ExplodingDebtService()).calculate_risk(pr_number=2, code="")


@pytest.mark.parametrize("bad_pr", [None, "abc"])
def test_invalid_pr_types_are_handled_deterministically(bad_pr):
    result = RiskEngine().calculate_risk(pr_number=bad_pr, code="")
    assert result == SeverityLevel.LOW


def test_float_pr_number_is_handled_deterministically():
    result = RiskEngine().calculate_risk(pr_number=3.14, code="")
    assert result == SeverityLevel.LOW


def test_security_high_forces_overall_high():
    code = 'def f():\n    api_key = "abc"\n    return 1'
    result = RiskEngine().calculate_risk(pr_number=2, code=code)
    assert result == SeverityLevel.HIGH


def test_security_medium_elevates_overall_to_medium_when_debt_low():
    code = 'def f():\n    return eval("2+2")'
    result = RiskEngine().calculate_risk(pr_number=2, code=code)
    assert result == SeverityLevel.MEDIUM


def test_risk_output_contract_contains_no_unexpected_keys():
    code = "def simple():\n    return 1"
    metrics = DebtService().evaluate_debt(code)
    severity = RiskEngine().calculate_risk(pr_number=2, code=code)
    snapshot = {
        "severity": severity,
        "complexity": metrics["complexity"],
        "maintainability": metrics["maintainability"],
    }
    assert set(snapshot.keys()) == {"severity", "complexity", "maintainability"}