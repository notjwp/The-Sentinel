from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.entities.finding import Finding
from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.value_objects.severity_level import SeverityLevel


EXPECTED_KEYS = {
    "severity",
    "complexity",
    "maintainability",
    "security_findings_count",
    "security",
}


def _snapshot(code: str, pr_number: int = 2) -> dict:
    return RiskEngine().assess(code=code)


def test_risk_contract_schema_keys_and_types_are_stable():
    code = "def simple():\n    return 1"
    contract = _snapshot(code)

    assert set(contract.keys()) == EXPECTED_KEYS
    assert type(contract["severity"]) is SeverityLevel
    assert type(contract["complexity"]) is int
    assert type(contract["maintainability"]) is float
    assert type(contract["security_findings_count"]) is int
    assert type(contract["security"]) is dict
    assert set(contract["security"].keys()) == {"findings", "severity"}
    assert type(contract["security"]["severity"]) is SeverityLevel
    assert isinstance(contract["security"]["findings"], list)


def test_debt_metrics_have_no_unexpected_keys():
    metrics = DebtService().evaluate_debt("def simple():\n    return 1")
    assert set(metrics.keys()) == {"severity", "complexity", "maintainability"}


def test_contract_has_no_extra_keys():
    contract = _snapshot("def simple():\n    return 1")
    assert sorted(contract.keys()) == [
        "complexity",
        "maintainability",
        "security",
        "security_findings_count",
        "severity",
    ]


def test_contract_output_shape_is_deterministic():
    code = "def medium(v):\n    if v > 0:\n        return v\n    return 0"
    first = _snapshot(code)
    for _ in range(20):
        assert _snapshot(code) == first


def test_security_findings_count_matches_findings_length():
    contract = _snapshot('def f():\n    password = "123"\n    return 1')
    assert contract["security_findings_count"] == len(contract["security"]["findings"])


def test_security_findings_are_finding_instances():
    contract = _snapshot('def f():\n    api_key = "abc"\n    return 1')
    assert all(isinstance(finding, Finding) for finding in contract["security"]["findings"])
