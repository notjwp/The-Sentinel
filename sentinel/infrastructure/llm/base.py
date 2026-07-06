class LLMProvider:
    def generate_pr_audit(self, code: str, findings_summary: str) -> str | None:
        raise NotImplementedError
