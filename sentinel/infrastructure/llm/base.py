class LLMProvider:
    def generate_fix(self, code: str, issue: str) -> str:
        raise NotImplementedError

    def explain_issue(self, code: str, issue: str) -> str:
        raise NotImplementedError
