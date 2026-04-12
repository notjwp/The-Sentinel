from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.base import LLMProvider
from sentinel.infrastructure.llm.nim_provider import NIMProvider


class LLMService:
    FALLBACK_FIX = "Fix suggestion unavailable"
    FALLBACK_EXPLANATION = "Explanation unavailable"

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        enable_llm: bool = False,
        max_calls: int = 5,
        timeout: float = 10.0,
        api_key: str | None = None,
    ) -> None:
        self.provider = provider
        if self.provider is None and enable_llm:
            self.provider = NIMProvider(api_key=api_key, timeout=timeout)
        self.enable_llm = enable_llm
        self.max_calls = max(0, max_calls)
        self.call_count = 0

    @property
    def calls_made(self) -> int:
        return self.call_count

    def reset_budget(self) -> None:
        self.call_count = 0

    @staticmethod
    def _severity_name(severity: SeverityLevel | str | None) -> str:
        if isinstance(severity, SeverityLevel):
            return severity.value
        if isinstance(severity, str):
            return severity.upper()
        return "LOW"

    def _can_invoke(self, severity: SeverityLevel | str | None) -> bool:
        if not self.enable_llm:
            return False
        if self.provider is None:
            return False
        if self.call_count >= self.max_calls:
            return False
        return self._severity_name(severity) in {"MEDIUM", "HIGH", "CRITICAL"}

    def generate_fix_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str:
        if not self._can_invoke(severity):
            return self.FALLBACK_FIX

        try:
            self.call_count += 1
            result = self.provider.generate_fix(code, issue)
            if not result:
                return self.FALLBACK_FIX
            return result.strip()
        except Exception:
            return self.FALLBACK_FIX

    def explain_issue_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str:
        if not self._can_invoke(severity):
            return self.FALLBACK_EXPLANATION

        try:
            self.call_count += 1
            result = self.provider.explain_issue(code, issue)
            if not result:
                return self.FALLBACK_EXPLANATION
            return result.strip()
        except Exception:
            return self.FALLBACK_EXPLANATION
