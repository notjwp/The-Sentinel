from sentinel.infrastructure.translation.translator import Translator


class _FakeLLMService:
    FALLBACK_EXPLANATION = "Potential security issue detected. Review code manually."

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
    translator = Translator(
        llm_service=_FakeLLMService("Potential security issue detected. Review code manually.")
    )

    translated = translator.translate("Report", "Kannada")

    assert translated == ""


def test_translator_returns_empty_for_empty_or_invalid_text_and_missing_llm():
    translator = Translator(llm_service=None)

    assert translator.translate("", "Hindi") == ""
    assert translator.translate("   ", "Hindi") == ""
    assert translator.translate(123, "Hindi") == ""  # type: ignore[arg-type]
    assert translator.translate("Report", "Hindi") == ""


def test_translator_returns_empty_when_llm_raises_exception():
    class _RaisingLLMService:
        def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
            _ = code
            _ = issue
            _ = severity
            raise RuntimeError("llm down")

    translator = Translator(llm_service=_RaisingLLMService())

    translated = translator.translate("Report", "Tamil")

    assert translated == ""
