import re
from dataclasses import dataclass

from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


@dataclass(frozen=True)
class _RuleSpec:
    rule: str
    pattern: str
    severity: SeverityLevel
    vulnerability_type: str
    description: str
    recommendation: str | None = None


class SecurityService:
    VULNERABILITY_CLASSIFICATION: dict[str, tuple[str, str]] = {
        "sql_injection": ("Injection", "A03: Injection"),
        "command_injection": ("Injection", "A03: Injection"),
        "hardcoded_secret": (
            "Sensitive Data Exposure",
            "A02: Cryptographic Failures",
        ),
        "weak_hash": ("Cryptography", "A02: Cryptographic Failures"),
        "insecure_deserialization": (
            "Integrity Failure",
            "A08: Software and Data Integrity Failures",
        ),
        "dangerous_code_execution": ("Insecure Design", "A04: Insecure Design"),
        "security_misconfiguration": (
            "Security Misconfiguration",
            "A05: Security Misconfiguration",
        ),
    }

    RULES: tuple[_RuleSpec, ...] = (
        _RuleSpec(
            rule="openai_key",
            pattern=r"sk-[A-Za-z0-9]{20,}",
            severity=SeverityLevel.HIGH,
            vulnerability_type="hardcoded_secret",
            description="Potential OpenAI API key hardcoded in source code.",
            recommendation="Move secrets to environment variables or a secrets manager.",
        ),
        _RuleSpec(
            rule="aws_access_key",
            pattern=r"AKIA[0-9A-Z]{16}",
            severity=SeverityLevel.HIGH,
            vulnerability_type="hardcoded_secret",
            description="Potential AWS access key hardcoded in source code.",
            recommendation="Rotate the key and load credentials from a secure runtime source.",
        ),
        _RuleSpec(
            rule="api_key_assignment",
            pattern=r"api_key\s*=\s*[\"'][^\"']+[\"']",
            severity=SeverityLevel.HIGH,
            vulnerability_type="hardcoded_secret",
            description="Possible API key assignment in application code.",
            recommendation="Use secure configuration providers instead of hardcoded values.",
        ),
        _RuleSpec(
            rule="password_assignment",
            pattern=r"password\s*=\s*[\"'][^\"']+[\"']",
            severity=SeverityLevel.HIGH,
            vulnerability_type="hardcoded_secret",
            description="Possible hardcoded password assignment detected.",
            recommendation="Store passwords outside source code and use secure secret injection.",
        ),
        _RuleSpec(
            rule="sql_injection",
            pattern=r"f[\"'][^\"'\n]*SELECT\s+[^\n]*\{[^}\n]+\}[^\n\"']*[\"']",
            severity=SeverityLevel.CRITICAL,
            vulnerability_type="sql_injection",
            description="Dynamic SQL query interpolation detected.",
            recommendation="Use parameterized queries with bound parameters.",
        ),
        _RuleSpec(
            rule="command_injection",
            pattern=r"(?:os\.system\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)|subprocess\.[A-Za-z_]+\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*shell\s*=\s*True\s*\))",
            severity=SeverityLevel.CRITICAL,
            vulnerability_type="command_injection",
            description="User-controlled command execution pattern detected.",
            recommendation="Avoid shell execution and validate/allowlist command inputs.",
        ),
        _RuleSpec(
            rule="eval_call",
            pattern=r"eval\(",
            severity=SeverityLevel.MEDIUM,
            vulnerability_type="dangerous_code_execution",
            description="Use of eval can execute untrusted input.",
            recommendation="Replace eval with safe parsers or strict expression evaluators.",
        ),
        _RuleSpec(
            rule="exec_call",
            pattern=r"exec\(",
            severity=SeverityLevel.MEDIUM,
            vulnerability_type="dangerous_code_execution",
            description="Use of exec may execute arbitrary code.",
            recommendation="Avoid dynamic execution of code strings.",
        ),
        _RuleSpec(
            rule="os_system_call",
            pattern=r"os\.system\(",
            severity=SeverityLevel.MEDIUM,
            vulnerability_type="security_misconfiguration",
            description="Command execution via os.system can be unsafe.",
            recommendation="Prefer subprocess with argument lists and shell=False.",
        ),
        _RuleSpec(
            rule="subprocess_shell_true",
            pattern=r"subprocess\.[A-Za-z_]+\([^)\n]*[\"'][^\"']+[\"'][^)\n]*shell\s*=\s*True\s*\)",
            severity=SeverityLevel.MEDIUM,
            vulnerability_type="security_misconfiguration",
            description="subprocess usage with shell=True increases injection risk.",
            recommendation="Set shell=False and pass command arguments as a list.",
        ),
        _RuleSpec(
            rule="naive_sql_concat",
            pattern=r"SELECT\s+[^\n]*\s+FROM\s+[^\n]*\+",
            severity=SeverityLevel.MEDIUM,
            vulnerability_type="sql_injection",
            description="SQL query built via string concatenation detected.",
            recommendation="Use parameterized statements instead of string concatenation.",
        ),
    )
    COMPILED_RULES: tuple[tuple[_RuleSpec, re.Pattern[str]], ...] = tuple(
        (spec, re.compile(spec.pattern)) for spec in RULES
    )
    RULE_TRIGGERS: dict[str, tuple[str, ...]] = {
        "openai_key": ("sk-",),
        "aws_access_key": ("AKIA",),
        "api_key_assignment": ("api_key",),
        "password_assignment": ("password",),
        "sql_injection": ("SELECT", "f\"", "f'"),
        "command_injection": ("os.system(", "subprocess."),
        "eval_call": ("eval(",),
        "exec_call": ("exec(",),
        "os_system_call": ("os.system(",),
        "subprocess_shell_true": ("subprocess.", "shell=True"),
        "naive_sql_concat": ("SELECT", "+"),
    }

    def _classify(self, vulnerability_type: str) -> tuple[str, str]:
        return self.VULNERABILITY_CLASSIFICATION.get(
            vulnerability_type,
            ("Unknown", "Unknown"),
        )

    @staticmethod
    def _line_number(code: str, start_index: int) -> int:
        return code.count("\n", 0, start_index) + 1

    @staticmethod
    def _overall_severity(findings: list[Finding]) -> SeverityLevel:
        if any(finding.severity == SeverityLevel.CRITICAL for finding in findings):
            return SeverityLevel.CRITICAL
        if any(finding.severity == SeverityLevel.HIGH for finding in findings):
            return SeverityLevel.HIGH
        if any(finding.severity == SeverityLevel.MEDIUM for finding in findings):
            return SeverityLevel.MEDIUM
        return SeverityLevel.LOW

    def analyze(self, code: str) -> dict:
        if not isinstance(code, str):
            raise TypeError("SecurityService.analyze expects code as a string")

        if not code:
            return {
                "findings": [],
                "severity": SeverityLevel.LOW,
            }

        findings: list[Finding] = []

        for spec, compiled_pattern in self.COMPILED_RULES:
            triggers = self.RULE_TRIGGERS.get(spec.rule, ())
            if triggers and not any(trigger in code for trigger in triggers):
                continue

            category, owasp_category = self._classify(spec.vulnerability_type)
            for match in compiled_pattern.finditer(code):
                findings.append(
                    Finding(
                        rule=spec.rule,
                        match=match.group(0),
                        severity=spec.severity,
                        category=category,
                        owasp_category=owasp_category,
                        description=spec.description,
                        line=self._line_number(code, match.start()),
                        recommendation=spec.recommendation,
                    )
                )

        return {
            "findings": findings,
            "severity": self._overall_severity(findings),
        }
