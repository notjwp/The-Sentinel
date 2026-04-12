from typing import Final

from sentinel.infrastructure.llm.llm_service import LLMService


class Translator:
    SUPPORTED_LANGUAGES: Final[dict[str, str]] = {
        "hindi": "Hindi",
        "kannada": "Kannada",
        "tamil": "Tamil",
        "telugu": "Telugu",
    }

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service

    def translate(self, text: str, language: str) -> str:
        if not isinstance(text, str) or text.strip() == "":
            return ""

        normalized_language = language.strip().lower()
        target_language = self.SUPPORTED_LANGUAGES.get(normalized_language)
        if target_language is None:
            return ""

        if self.llm_service is None:
            return ""

        instruction = (
            "Translate the provided markdown report into "
            f"{target_language}. Preserve headings, bullets, and code blocks."
        )

        try:
            translated = self.llm_service.explain_issue_safe(
                text,
                instruction,
                severity="HIGH",
            )
        except Exception:
            return ""

        fallback = getattr(self.llm_service, "FALLBACK_EXPLANATION", "Explanation unavailable")
        if not translated or translated == fallback:
            return ""

        return translated.strip()
