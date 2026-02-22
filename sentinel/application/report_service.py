from sentinel.domain.value_objects.severity_level import SeverityLevel


class ReportService:
    def generate_report(self, pr_number: int, risk: SeverityLevel) -> str:
        return f"PR #{pr_number} Risk: {risk.value}"