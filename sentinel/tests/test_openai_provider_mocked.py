import builtins
import sys
import types

import pytest

from sentinel.infrastructure.llm.openai_provider import OpenAIProvider


class _Responses:
    def __init__(self, response=None, should_raise: bool = False) -> None:
        self.response = response
        self.should_raise = should_raise
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.should_raise:
            raise RuntimeError("api failed")
        return self.response


class _Client:
    def __init__(self, responses: _Responses) -> None:
        self.responses = responses


def test_init_accepts_injected_client():
    client = _Client(_Responses())
    provider = OpenAIProvider(client=client)
    assert provider._client is client


def test_init_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        OpenAIProvider(client=None)


def test_init_raises_when_openai_import_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            raise ImportError("missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="openai package is not installed"):
        OpenAIProvider(client=None)


def test_init_raises_when_openai_class_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace())
    with pytest.raises(RuntimeError, match="OpenAI client class unavailable"):
        OpenAIProvider(client=None)


def test_init_success_constructs_openai_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured = {}

    class _OpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_OpenAI))
    provider = OpenAIProvider(timeout=3.0, client=None)

    assert provider._client is not None
    assert captured["api_key"] == "k"
    assert captured["timeout"] == 3.0


def test_extract_text_handles_empty_and_nested_text():
    assert OpenAIProvider._extract_text(types.SimpleNamespace(output=None)) == ""

    nested = types.SimpleNamespace(
        output=[
            types.SimpleNamespace(
                content=[
                    types.SimpleNamespace(text="a"),
                    types.SimpleNamespace(text="b"),
                    types.SimpleNamespace(text=None),
                ]
            )
        ]
    )
    assert OpenAIProvider._extract_text(nested) == "a\nb"


def test_run_prompt_uses_output_text_when_available():
    response = types.SimpleNamespace(output_text="  fixed  ")
    provider = OpenAIProvider(client=_Client(_Responses(response=response)), max_retries=0)

    result = provider._run_prompt("sys", "user")

    assert result == "fixed"


def test_run_prompt_falls_back_to_extract_text_when_output_text_missing():
    response = types.SimpleNamespace(
        output_text="",
        output=[types.SimpleNamespace(content=[types.SimpleNamespace(text="  explain  ")])],
    )
    provider = OpenAIProvider(client=_Client(_Responses(response=response)), max_retries=0)

    result = provider._run_prompt("sys", "user")

    assert result == "explain"


def test_run_prompt_raises_when_response_is_empty_after_retries():
    response = types.SimpleNamespace(output_text="   ", output=None)
    provider = OpenAIProvider(client=_Client(_Responses(response=response)), max_retries=0)

    with pytest.raises(RuntimeError, match="OpenAI request failed"):
        provider._run_prompt("sys", "user")


def test_run_prompt_retries_on_exception_and_then_raises():
    responses = _Responses(should_raise=True)
    provider = OpenAIProvider(client=_Client(responses), max_retries=1)

    with pytest.raises(RuntimeError, match="OpenAI request failed"):
        provider._run_prompt("sys", "user")

    assert responses.calls == 2


def test_generate_fix_and_explain_issue_delegate_to_prompt_runner(monkeypatch):
    provider = OpenAIProvider(client=_Client(_Responses()), max_retries=0)

    calls = []

    def _fake_run(system_prompt: str, user_prompt: str) -> str:
        calls.append((system_prompt, user_prompt))
        return "ok"

    monkeypatch.setattr(provider, "_run_prompt", _fake_run)

    assert provider.generate_fix("code", "issue") == "ok"
    assert provider.explain_issue("code", "issue") == "ok"
    assert len(calls) == 2
