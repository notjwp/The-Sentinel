from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.value_objects.severity_level import SeverityLevel

def test_even_pr_low():
    engine = RiskEngine()
    assert engine.calculate_risk(2) == SeverityLevel.LOW

def test_odd_pr_high():
    engine = RiskEngine()
    assert engine.calculate_risk(3) == SeverityLevel.HIGH