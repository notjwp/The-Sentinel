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

    def _can_invoke(self) -> bool:
        if not self.enable_llm:
            return False
        if self.provider is None:
            return False
        if self.call_count >= self.max_calls:
            return False
        return True

    def _parse_pr_audit_response(self, content: str, expected_ids: list[int]) -> dict[int, dict[str, str]]:
        import re
        result = {}
        blocks = re.split(r'(?i)\bIssue:\s*', content)
        
        rule_idx = 0
        for block in blocks:
            if not block.strip():
                continue
                
            explanation_match = re.search(r'(?i)\bExplanation:\s*(.*?)(?=\bFix:\s*|$)', block, re.DOTALL)
            fix_match = re.search(r'(?i)\bFix:\s*(.*?)$', block, re.DOTALL)
            
            explanation = explanation_match.group(1).strip() if explanation_match else self.FALLBACK_EXPLANATION
            fix = fix_match.group(1).strip() if fix_match else self.FALLBACK_FIX
            
            if rule_idx < len(expected_ids):
                fid = expected_ids[rule_idx]
                result[fid] = {"explanation": explanation, "fix": fix}
                rule_idx += 1

        for fid in expected_ids:
            if fid not in result:
                result[fid] = {"explanation": self.FALLBACK_EXPLANATION, "fix": self.FALLBACK_FIX}
                
        return result

    def generate_pr_audit(self, code: str, findings: list) -> dict[int, dict[str, str]]:
        enrichable = []
        for finding in findings:
            severity_name = self._severity_name(finding.severity)
            is_security = finding.type == "security"
            is_meaningful_medium = severity_name != "MEDIUM" or bool(finding.recommendation)
            if is_security and severity_name != "LOW" and is_meaningful_medium:
                enrichable.append(finding)

        if not enrichable or not self._can_invoke():
            self.logger.info("LLM skipped; using fallback response.")
            return {id(f): {"explanation": self.FALLBACK_EXPLANATION, "fix": self.FALLBACK_FIX} for f in enrichable}

        summary_lines = []
        for i, f in enumerate(enrichable, 1):
            severity = self._severity_name(f.severity)
            summary_lines.append(f"{i}. {f.rule}\n   Severity: {severity}")
            
        findings_summary = "\n\n".join(summary_lines)

        self.logger.info("LLM PR audit started")
        try:
            self.call_count += 1
            content = self.provider.generate_pr_audit(code, findings_summary)
        except Exception:
            self.logger.exception("LLM request failed; using fallback response.")
            self.logger.info("Fallback triggered")
            return {id(f): {"explanation": self.FALLBACK_EXPLANATION, "fix": self.FALLBACK_FIX} for f in enrichable}

        if not content or not str(content).strip():
            self.logger.warning("LLM returned empty content; using fallback response.")
            self.logger.info("Fallback triggered")
            return {id(f): {"explanation": self.FALLBACK_EXPLANATION, "fix": self.FALLBACK_FIX} for f in enrichable}

        self.logger.info("LLM response received")
        parsed = self._parse_pr_audit_response(str(content), [id(f) for f in enrichable])
        
        self.logger.info(f"Parsed {len(parsed)} issue explanations")
        self.logger.info(f"Total findings enriched: {len(parsed)}")
        
        return parsed
