import os
from typing import Any

from sentinel.infrastructure.llm.base import LLMProvider


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


class NIMProvider(LLMProvider):
    BASE_URL = "https://integrate.api.nvidia.com/v1"
    MODEL = "meta/llama-3.3-70b-instruct"
    RETRY_DELAYS = (0.3, 0.8)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 5.0,
        base_url: str = BASE_URL,
        model: str = MODEL,
        client: Any = None,
    ) -> None:
        self.logger = _get_logger(__name__)
        self.api_key = api_key if api_key is not None else os.getenv("NVIDIA_API_KEY", "").strip()
        self.timeout = max(0.0, timeout)
        self.base_url = base_url
        self.model = model
        self._client = client if client is not None else self._build_client()

    @staticmethod
    def _sleep(seconds: float) -> None:
        try:
            time_module = __import__("time")
            sleep = getattr(time_module, "sleep", None)
            if callable(sleep):
                sleep(seconds)
        except Exception:
            return

    def _build_client(self) -> Any | None:
        if not self.api_key:
            return None
        try:
            openai_module = __import__("openai")
        except ImportError:
            return None

        openai_cls = getattr(openai_module, "OpenAI", None)
        if openai_cls is None:
            return None

        try:
            return openai_cls(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        except Exception:
            return None

    @staticmethod
    def _sanitize(text: str | None) -> str | None:
        if text is None:
            return None
        cleaned = text.replace("\u0000", "").strip()
        return cleaned or None

    @staticmethod
    def _extract_content(response: Any) -> str | None:
        choices = getattr(response, "choices", None)
        if not choices:
            return None

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is None and isinstance(first_choice, dict):
            message = first_choice.get("message")

        content = None
        if message is not None:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")

        return NIMProvider._sanitize(content)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True
        error_code = getattr(exc, "code", None)
        if error_code == 429:
            return True
        message = str(exc).lower()
        return "429" in message or "rate limit" in message

    def _chat(self, system_prompt: str, user_prompt: str) -> str | None:
        if not self.api_key or self._client is None:
            return None

        for attempt, delay in enumerate((0.0,) + self.RETRY_DELAYS):
            if attempt:
                self._sleep(delay)
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    max_tokens=700,
                    stream=False,
                )
            except Exception as exc:
                if self._is_rate_limit_error(exc) and attempt < len(self.RETRY_DELAYS):
                    self.logger.warning("NIM rate limit encountered; retrying.")
                    continue
                self.logger.warning("NIM request failed.")
                return None

            content = self._extract_content(response)
            if content:
                return content
            self.logger.warning("NIM response missing content.")
            return None

        return None

    def review_issue(self, code: str, issue: str) -> str | None:
        prompt = (
            "You are a secure code reviewer.\n\n"
            "Analyze the code and issue.\n\n"
            "Code:\n"
            f"{code}\n\n"
            "Issue:\n"
            f"{issue}\n\n"
            "Respond EXACTLY in this format:\n\n"
            "Explanation: <short clear explanation>\n\n"
            "Fix: <only corrected code>\n\n"
            "Keep it concise."
        )
        return self._chat("", prompt)

    def generate_fix(self, code: str, issue: str) -> str | None:
        system_prompt = (
            "You are a secure coding assistant. Return only corrected code. "
            "No markdown or explanation."
        )
        user_prompt = (
            "Issue:\n"
            f"{issue}\n\n"
            "Code:\n"
            f"{code}\n\n"
            "Return only secure corrected code."
        )
        return self._chat(system_prompt, user_prompt)

    def explain_issue(self, code: str, issue: str) -> str | None:
        system_prompt = "You are a concise security explainer."
        user_prompt = (
            "Issue:\n"
            f"{issue}\n\n"
            "Code:\n"
            f"{code}\n\n"
            "Explain briefly: what it is, why dangerous, how to fix."
        )
        return self._chat(system_prompt, user_prompt)
