import re

from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class SecurityService:
    def analyze(self, code: str) -> dict:
        findings: list[Finding] = []

        high_patterns: list[tuple[str, str]] = [
            ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
            ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
            ("api_key_assignment", r"api_key\s*=\s*[\"'][^\"']+[\"']"),
            ("password_assignment", r"password\s*=\s*[\"'][^\"']+[\"']"),
        ]

        medium_patterns: list[tuple[str, str]] = [
            ("eval_call", r"eval\("),
            ("exec_call", r"exec\("),
            ("os_system_call", r"os\.system\("),
            ("subprocess_shell_true", r"subprocess\.[A-Za-z_]+\([^\)]*shell\s*=\s*True"),
            ("naive_sql_concat", r"SELECT\s+.*\s+FROM\s+.*\+"),
        ]

        for rule, pattern in high_patterns:
            for match in re.finditer(pattern, code):
                findings.append(
                    Finding(
                        rule=rule,
                        match=match.group(0),
                        severity=SeverityLevel.HIGH,
                    )
                )

        for rule, pattern in medium_patterns:
            for match in re.finditer(pattern, code):
                findings.append(
                    Finding(
                        rule=rule,
                        match=match.group(0),
                        severity=SeverityLevel.MEDIUM,
                    )
                )

        if any(finding.severity == SeverityLevel.HIGH for finding in findings):
            severity = SeverityLevel.HIGH
        elif any(finding.severity == SeverityLevel.MEDIUM for finding in findings):
            severity = SeverityLevel.MEDIUM
        else:
            severity = SeverityLevel.LOW

        return {
            "findings": findings,
            "severity": severity,
        }
