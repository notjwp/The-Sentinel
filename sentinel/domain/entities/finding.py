from dataclasses import dataclass

from sentinel.domain.value_objects.severity_level import SeverityLevel


@dataclass(frozen=True)
class Finding:
    rule: str
    match: str
    severity: SeverityLevel
    finding_type: str = "security"
    similarity_score: float | None = None
