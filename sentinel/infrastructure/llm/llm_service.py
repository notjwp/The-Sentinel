from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.base import LLMProvider
from sentinel.infrastructure.llm.nim_provider import NIMProvider


def _get_logger(name: str):
    try:
        logger_module = __import__("sentinel.monitoring.logger", fromlist=["get_logger"])
        get_logger = getattr(logger_module, "get_logger", None)
        if callable(get_logger):
            return get_logger(name)
    except Exception:
        pass

    class _FallbackLogger:
        def info(self, msg: str, *args: object, **_: object) -> None:
            print(msg % args if args else msg)

        def warning(self, msg: str, *args: object, **_: object) -> None:
            print(msg % args if args else msg)

        def exception(self, msg: str, *args: object, **_: object) -> None:
            print(msg % args if args else msg)

    return _FallbackLogger()


class LLMService:
    FALLBACK_FIX = "Use parameterized queries or validate input."
    FALLBACK_EXPLANATION = "Potential security issue detected. Review code manually."

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        enable_llm: bool = False,
        max_calls: int = 1,
        timeout: float = 5.0,
        api_key: str | None = None,
    ) -> None:
        self.logger = _get_logger(__name__)
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

    @staticmethod
    def _parse_unified_response(content: str) -> tuple[str, str]:
        cleaned = content.strip()
        if "Fix:" in cleaned:
            explanation_part, fix_part = cleaned.split("Fix:", 1)
        else:
            explanation_part = cleaned
            fix_part = "Fix unavailable"
        explanation = explanation_part.replace("Explanation:", "").strip()
        fix = fix_part.strip()
        return explanation, fix

    def analyze_issue_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> tuple[str, str]:
        if not self._can_invoke(severity):
            self.logger.info("LLM skipped; using fallback response.")
            return self.FALLBACK_EXPLANATION, self.FALLBACK_FIX

        self.logger.info("LLM request start.")
        try:
            self.call_count += 1
            content = self.provider.review_issue(code, issue)
        except Exception:
            self.logger.exception("LLM request failed; using fallback response.")
            return self.FALLBACK_EXPLANATION, self.FALLBACK_FIX

        if not content or not str(content).strip():
            self.logger.warning("LLM returned empty content; using fallback response.")
            return self.FALLBACK_EXPLANATION, self.FALLBACK_FIX

        explanation, fix = self._parse_unified_response(str(content))
        if not explanation:
            self.logger.warning("LLM response missing explanation; using fallback explanation.")
            explanation = self.FALLBACK_EXPLANATION
        if not fix:
            self.logger.warning("LLM response missing fix; using fallback fix.")
            fix = self.FALLBACK_FIX

        self.logger.info("LLM response received.")
        return explanation, fix

    def generate_fix_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str:
        if not self._can_invoke(severity):
            self.logger.info("LLM skipped; using fallback fix.")
            return self.FALLBACK_FIX

        try:
            self.call_count += 1
            result = self.provider.generate_fix(code, issue)
            if not result:
                self.logger.warning("LLM returned empty fix; using fallback fix.")
                return self.FALLBACK_FIX
            return result.strip()
        except Exception:
            self.logger.exception("LLM fix request failed; using fallback fix.")
            return self.FALLBACK_FIX

    def explain_issue_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str:
        if not self._can_invoke(severity):
            self.logger.info("LLM skipped; using fallback explanation.")
            return self.FALLBACK_EXPLANATION

        try:
            self.call_count += 1
            result = self.provider.explain_issue(code, issue)
            if not result:
                self.logger.warning("LLM returned empty explanation; using fallback explanation.")
                return self.FALLBACK_EXPLANATION
            return result.strip()
        except Exception:
            self.logger.exception("LLM explanation request failed; using fallback explanation.")
            return self.FALLBACK_EXPLANATION
