from sentinel.application.audit_orchestrator import AuditOrchestrator


class _FakeLLMService:
    FALLBACK_EXPLANATION = "Potential security issue detected. Review code manually."

    def __init__(self, response: str) -> None:
        self.response = response

    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        return self.response


def test_translator_returns_translated_text_for_supported_language(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    orchestrator = AuditOrchestrator(queue=None, llm_service=_FakeLLMService("translated report"))
    translated = orchestrator.append_translations("Report", ["Hindi"])
    assert "## Hindi Version\n\ntranslated report" in translated


def test_translator_returns_empty_for_unsupported_language(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    orchestrator = AuditOrchestrator(queue=None, llm_service=_FakeLLMService("translated"))
    translated = orchestrator.append_translations("Report", ["Spanish"])
    assert "Spanish Version" not in translated


def test_translator_returns_empty_when_llm_returns_fallback(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    orchestrator = AuditOrchestrator(
        queue=None,
        llm_service=_FakeLLMService("Potential security issue detected. Review code manually.")
    )
    translated = orchestrator.append_translations("Report", ["Kannada"])
    assert "Kannada Version" not in translated


def test_translator_returns_empty_for_empty_or_invalid_text_and_missing_llm(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    orchestrator = AuditOrchestrator(queue=None, llm_service=None)
    assert orchestrator.append_translations("") == ""
    assert orchestrator.append_translations("Report", ["Hindi"]) == "Report"


def test_translator_returns_empty_when_llm_raises_exception(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSLATION", "true")
    class _RaisingLLMService:
        def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
            raise RuntimeError("llm down")

    orchestrator = AuditOrchestrator(queue=None, llm_service=_RaisingLLMService())
    translated = orchestrator.append_translations("Report", ["Tamil"])
    assert "Tamil Version" not in translated
