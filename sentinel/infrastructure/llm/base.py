class LLMProvider:
    def generate_pr_audit(self, code: str, findings_summary: str) -> str | None:
        raise NotImplementedError

    def generate_text(self, prompt: str) -> str | None:
        """One-shot free-form completion (translations, doc review). None on failure."""
        raise NotImplementedError
