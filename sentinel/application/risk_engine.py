from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.value_objects.severity_level import SeverityLevel


class RiskEngine:
    def __init__(
        self,
        debt_service: DebtService | None = None,
        security_service: SecurityService | None = None,
    ) -> None:
        self.debt_service = debt_service or DebtService()
        self.security_service = security_service or SecurityService()

    def assess(self, code: str = "") -> dict:
        debt_result = self.debt_service.evaluate_debt(code)
        security_result = self.security_service.analyze(code)

        debt_severity = debt_result["severity"]
        security_severity = security_result["severity"]

        if security_severity == SeverityLevel.HIGH:
            overall_severity = SeverityLevel.HIGH
        elif debt_severity == SeverityLevel.HIGH:
            overall_severity = SeverityLevel.HIGH
        elif security_severity == SeverityLevel.MEDIUM or debt_severity == SeverityLevel.MEDIUM:
            overall_severity = SeverityLevel.MEDIUM
        else:
            overall_severity = SeverityLevel.LOW

        return {
            "severity": overall_severity,
            "complexity": debt_result["complexity"],
            "maintainability": debt_result["maintainability"],
            "security_findings_count": len(security_result["findings"]),
            "security": security_result,
        }

    def calculate_risk(self, pr_number: int, code: str = "") -> SeverityLevel:
        _ = pr_number
        return self.assess(code)["severity"]