import time

from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


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

    @staticmethod
    def _normalize_severity(value: object) -> SeverityLevel:
        if isinstance(value, SeverityLevel):
            return value
        return SeverityLevel.LOW

    def _security_severity_from_result(self, security_result: dict) -> SeverityLevel:
        findings = security_result.get("findings", [])
        finding_severities = [
            self._normalize_severity(getattr(finding, "severity", None)) for finding in findings
        ]

        if any(severity == SeverityLevel.CRITICAL for severity in finding_severities):
            return SeverityLevel.CRITICAL
        if any(severity == SeverityLevel.HIGH for severity in finding_severities):
            return SeverityLevel.HIGH
        if any(severity == SeverityLevel.MEDIUM for severity in finding_severities):
            return SeverityLevel.MEDIUM
        return self._normalize_severity(security_result.get("severity", SeverityLevel.LOW))

    @staticmethod
    def _overall_severity(
        debt_severity: SeverityLevel,
        security_severity: SeverityLevel,
        semantic_severity: SeverityLevel,
    ) -> SeverityLevel:
        if security_severity == SeverityLevel.CRITICAL:
            return SeverityLevel.CRITICAL
        if semantic_severity == SeverityLevel.HIGH:
            return SeverityLevel.HIGH
        if security_severity == SeverityLevel.HIGH:
            return SeverityLevel.HIGH
        if debt_severity == SeverityLevel.HIGH:
            return SeverityLevel.HIGH
        if security_severity == SeverityLevel.MEDIUM or debt_severity == SeverityLevel.MEDIUM:
            return SeverityLevel.MEDIUM
        return SeverityLevel.LOW

    def assess_resilient(
        self,
        code: str = "",
        existing_code_list: list[str] | None = None,
        *,
        warn_threshold_seconds: float = 0.1,
    ) -> dict:
        start_time = time.monotonic()

        try:
            debt_result = self.debt_service.evaluate_debt(code)
        except Exception:
            logger.exception("Debt engine failed; continuing with safe defaults")
            debt_result = {
                "complexity": 1,
                "maintainability": 100.0,
                "severity": SeverityLevel.LOW,
            }

        try:
            security_result = self.security_service.analyze(code)
        except Exception:
            logger.exception("Security engine failed; continuing with safe defaults")
            security_result = {
                "findings": [],
                "severity": SeverityLevel.LOW,
            }

        semantic_findings = []
        semantic_severity = SeverityLevel.LOW
        if self.semantic_service and code.strip():
            try:
                semantic_findings = self.semantic_service.detect_duplicates(
                    code,
                    existing_code_list or [],
                )
                semantic_severity = (
                    SeverityLevel.HIGH
                    if any(f.severity == SeverityLevel.HIGH for f in semantic_findings)
                    else SeverityLevel.LOW
                )
            except Exception:
                logger.exception("Semantic engine failed; continuing with safe defaults")

        debt_severity = self._normalize_severity(debt_result["severity"])
        security_severity = self._security_severity_from_result(security_result)
        overall_severity = self._overall_severity(
            debt_severity=debt_severity,
            security_severity=security_severity,
            semantic_severity=semantic_severity,
        )

        elapsed = time.monotonic() - start_time
        if elapsed > warn_threshold_seconds:
            logger.warning(
                "Risk assessment exceeded latency target: %.4fs (threshold %.4fs)",
                elapsed,
                warn_threshold_seconds,
            )

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

    def assess(self, code: str = "", existing_code_list: list[str] | None = None) -> dict:
        debt_result = self.debt_service.evaluate_debt(code)
        security_result = self.security_service.analyze(code)

        debt_severity = self._normalize_severity(debt_result["severity"])
        security_severity = self._security_severity_from_result(security_result)

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

        overall_severity = self._overall_severity(
            debt_severity=debt_severity,
            security_severity=security_severity,
            semantic_severity=semantic_severity,
        )

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