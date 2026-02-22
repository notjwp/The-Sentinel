from sentinel.domain.value_objects.severity_level import SeverityLevel


class RiskEngine:
    def calculate_risk(self, pr_number: int) -> SeverityLevel:
        if pr_number % 2 == 0:
            return SeverityLevel.LOW
        return SeverityLevel.HIGH