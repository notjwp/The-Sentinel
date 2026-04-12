from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class ReportService:
    def generate_report(self, pr_number: int, risk: SeverityLevel) -> str:
        return f"PR #{pr_number} Risk: {risk.value}"

    @staticmethod
    def _severity_value(risk: SeverityLevel | str) -> str:
        if isinstance(risk, SeverityLevel):
            return risk.value
        return str(risk).upper()

    def format_report(
        self,
        findings: list[Finding],
        risk: SeverityLevel | str,
        *,
        complexity: int | None = None,
        maintainability: float | None = None,
        semantic_findings_count: int | None = None,
    ) -> str:
        risk_value = self._severity_value(risk)
        lines: list[str] = [
            "# Sentinel AI Code Review",
            "",
            f"## Risk Score: {risk_value}",
            "",
            "## Security Issues",
        ]

        security_findings = [finding for finding in findings if finding.type == "security"]
        if security_findings:
            for finding in security_findings:
                lines.append(
                    f"- {finding.description or finding.rule} (Severity: {finding.severity.value})"
                )
        else:
            lines.append("- No security issues detected.")

        lines.extend(["", "## Explanation"])
        explanations = [finding.explanation for finding in security_findings if finding.explanation]
        if explanations:
            for explanation in explanations:
                lines.append(f"- {explanation}")
        else:
            lines.append("- No AI explanation available.")

        lines.extend(["", "## Fix Suggestion"])
        fixes = [finding.fix_suggestion for finding in security_findings if finding.fix_suggestion]
        if fixes:
            for fix in fixes:
                lines.extend(["```python", fix, "```"])
        else:
            lines.append("- No fix suggestion available.")

        documentation_findings = [finding for finding in findings if finding.type == "documentation"]
        if documentation_findings:
            lines.extend(["", "## Documentation Issues"])
            for finding in documentation_findings:
                lines.append(
                    f"- {finding.description or finding.rule} (Severity: {finding.severity.value})"
                )

        lines.extend(["", "## Technical Debt"])
        if complexity is None and maintainability is None:
            lines.append("- Technical debt metrics unavailable.")
        else:
            if complexity is not None:
                lines.append(f"- Complexity: {complexity}")
            if maintainability is not None:
                lines.append(f"- Maintainability: {maintainability:.2f}")

        lines.extend(["", "## Semantic Similarity"])
        if semantic_findings_count is None:
            lines.append("- Semantic similarity metrics unavailable.")
        else:
            lines.append(f"- Similar findings detected: {semantic_findings_count}")

        return "\n".join(lines).strip()