from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel


class RiskEngine:
    def __init__(
        self,
        debt_service: DebtService | None = None,
        security_service: SecurityService | None = None,
        semantic_service: SemanticService | None = None,
    ) -> None:
        self.debt_service = debt_service or DebtService()
        self.security_service = security_service or SecurityService()
        self.semantic_service = semantic_service

    def assess(self, code: str = "", existing_code_list: list[str] | None = None) -> dict:
        debt_result = self.debt_service.evaluate_debt(code)
        security_result = self.security_service.analyze(code)

        debt_severity = debt_result["severity"]
        security_severity = security_result["severity"]

        if self.semantic_service and code.strip():
            semantic_findings = self.semantic_service.detect_duplicates(
                code, existing_code_list or []
            )
            semantic_severity = (
                SeverityLevel.HIGH
                if any(f.severity == SeverityLevel.HIGH for f in semantic_findings)
                else SeverityLevel.LOW
            )
        else:
            semantic_findings = []
            semantic_severity = SeverityLevel.LOW

        if semantic_severity == SeverityLevel.HIGH:
            overall_severity = SeverityLevel.HIGH
        elif security_severity == SeverityLevel.HIGH:
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
            "semantic_findings_count": len(semantic_findings),
            "semantic": {
                "findings": semantic_findings,
                "severity": semantic_severity,
            },
        }

    def calculate_risk(
        self,
        pr_number: int,
        code: str = "",
        existing_code_list: list[str] | None = None,
    ) -> SeverityLevel:
        _ = pr_number
        return self.assess(code, existing_code_list)["severity"]