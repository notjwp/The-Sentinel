from sentinel.infrastructure.translation.translator import Translator


class _FakeLLMService:
    FALLBACK_EXPLANATION = "Explanation unavailable"

    def __init__(self, response: str) -> None:
        self.response = response

    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        return self.response


def test_translator_returns_translated_text_for_supported_language():
    translator = Translator(llm_service=_FakeLLMService("translated report"))

    translated = translator.translate("Report", "Hindi")

    assert translated == "translated report"


def test_translator_returns_empty_for_unsupported_language():
    translator = Translator(llm_service=_FakeLLMService("translated"))

    translated = translator.translate("Report", "Spanish")

    assert translated == ""


def test_translator_returns_empty_when_llm_returns_fallback():
    translator = Translator(llm_service=_FakeLLMService("Explanation unavailable"))

    translated = translator.translate("Report", "Kannada")

    assert translated == ""
