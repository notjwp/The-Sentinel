import os
from typing import Any

from sentinel.infrastructure.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        max_retries: int = 2,
        timeout: float = 8.0,
        client: Any = None,
    ) -> None:
        self.model = model
        self.max_retries = max(0, max_retries)

        if client is not None:
            self._client = client
            return

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        openai_module = None
        try:
            openai_module = __import__("openai")
        except ImportError:
            openai_module = None

        if openai_module is None:
            raise RuntimeError("openai package is not installed")

        openai_client_cls = getattr(openai_module, "OpenAI", None)
        if openai_client_cls is None:
            raise RuntimeError("OpenAI client class unavailable")

        self._client = openai_client_cls(api_key=api_key, timeout=timeout)

    @staticmethod
    def _sanitize(text: str) -> str:
        return text.replace("\u0000", "").strip()

    @staticmethod
    def _extract_text(response: Any) -> str:
        output = getattr(response, "output", None)
        if not output:
            return ""

        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for part in content:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(text)
        return "\n".join(chunks)

    def _run_prompt(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.responses.create(
                    model=self.model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_output_tokens=700,
                )
                text = getattr(response, "output_text", "") or self._extract_text(response)
                cleaned = self._sanitize(text)
                if not cleaned:
                    raise RuntimeError("LLM returned empty response")
                return cleaned
            except Exception as exc:  # pragma: no cover - network/API path is not unit tested
                last_error = exc
                if attempt >= self.max_retries:
                    break

        raise RuntimeError("OpenAI request failed") from last_error

    def generate_pr_audit(self, code: str, findings_summary: str) -> str:
        prompt = (
            "You are a senior secure code reviewer.\n\n"
            "Analyze the pull request and detected issues.\n\n"
            "Code:\n"
            f"{code}\n\n"
            "Detected Issues:\n"
            f"{findings_summary}\n\n"
            "For EACH issue provide:\n\n"
            "Issue:\n"
            "Explanation:\n"
            "Fix:\n\n"
            "Rules:\n\n"
            "* Explanation must be concise.\n"
            "* Fix must contain corrected code.\n"
            "* Return all issues.\n"
            "* Do not skip any issue."
        )
        return self._run_prompt("", prompt)
