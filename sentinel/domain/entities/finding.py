from dataclasses import dataclass

from sentinel.domain.value_objects.severity_level import SeverityLevel


@dataclass(frozen=True)
class Finding:
    rule: str
    match: str
    severity: SeverityLevel
    finding_type: str = "security"
    similarity_score: float | None = None
    category: str = "Unknown"
    owasp_category: str = "Unknown"
    description: str = ""
    file: str | None = None
    line: int | None = None
    recommendation: str | None = None
    fix_suggestion: str | None = None
    explanation: str | None = None

    @property
    def type(self) -> str:
        return self.finding_type
