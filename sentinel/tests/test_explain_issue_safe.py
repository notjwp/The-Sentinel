"""Housekeeping: LLMService.explain_issue_safe — translation/doc LLM tasks go live.

Previously only test fakes implemented explain_issue_safe, making
ENABLE_TRANSLATION dead code with the real service. These tests pin the real
implementation: budget-shared, failure-safe, and wired through append_translations.
"""

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.infrastructure.llm.llm_service import LLMService


class _TextProvider:
    def __init__(self, response: str = "Translated body") -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_disabled_returns_fallback_without_calling_provider():
    provider = _TextProvider()
    service = LLMService(provider=provider, enable_llm=False)
    assert service.explain_issue_safe("content", "Translate") == LLMService.FALLBACK_EXPLANATION
    assert provider.prompts == []


def test_budget_is_shared_and_exhaustion_falls_back():
    provider = _TextProvider()
    service = LLMService(provider=provider, enable_llm=True, max_calls=1)
    assert service.explain_issue_safe("a", "T") == "Translated body"  # spends the budget
    assert service.explain_issue_safe("b", "T") == LLMService.FALLBACK_EXPLANATION
    assert len(provider.prompts) == 1
    assert service.calls_made == 1


def test_success_passes_instruction_and_content():
    provider = _TextProvider("Translated report text")
    service = LLMService(provider=provider, enable_llm=True, max_calls=5)
    out = service.explain_issue_safe("REPORT BODY", "Translate into Hindi")
    assert out == "Translated report text"
    assert "Translate into Hindi" in provider.prompts[0]
    assert "REPORT BODY" in provider.prompts[0]


def test_provider_error_and_empty_content_fall_back():
    class _Boom:
        def generate_text(self, prompt: str) -> str:
            raise RuntimeError("provider down")

    service = LLMService(provider=_Boom(), enable_llm=True, max_calls=5)
    assert service.explain_issue_safe("x", "T") == LLMService.FALLBACK_EXPLANATION

    empty = LLMService(provider=_TextProvider("   "), enable_llm=True, max_calls=5)
    assert empty.explain_issue_safe("x", "T") == LLMService.FALLBACK_EXPLANATION


def test_provider_without_generate_text_falls_back():
    class _LegacyProvider:
        def generate_pr_audit(self, code: str, findings_summary: str) -> None:
            return None

    service = LLMService(provider=_LegacyProvider(), enable_llm=True, max_calls=5)
    assert service.explain_issue_safe("x", "T") == LLMService.FALLBACK_EXPLANATION


def test_append_translations_end_to_end_with_real_service(monkeypatch):
    """The previously-dead path: real LLMService now produces translated sections."""
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")

    class _LangProvider:
        def generate_text(self, prompt: str) -> str:
            if "Hindi" in prompt:
                return "Hindi text of the report"
            if "Kannada" in prompt:
                return "Kannada text of the report"
            return "generic"

    service = LLMService(provider=_LangProvider(), enable_llm=True, max_calls=3)
    orchestrator = AuditOrchestrator(queue=None, llm_service=service)
    out = orchestrator.append_translations("# Report")

    assert "## Hindi Version" in out and "Hindi text of the report" in out
    assert "## Kannada Version" in out and "Kannada text of the report" in out


def test_append_translations_skips_when_budget_spent(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    service = LLMService(provider=_TextProvider(), enable_llm=True, max_calls=0)
    orchestrator = AuditOrchestrator(queue=None, llm_service=service)
    assert orchestrator.append_translations("# Report") == "# Report"