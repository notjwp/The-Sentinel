import builtins
import sys
import types

from sentinel.infrastructure.llm.nim_provider import NIMProvider


class _Completions:
    def __init__(self, response=None, should_raise: bool = False) -> None:
        self.response = response
        self.should_raise = should_raise
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.should_raise:
            raise RuntimeError("nim error")
        return self.response


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _Client:
    def __init__(self, completions: _Completions) -> None:
        self.chat = _Chat(completions)


class _ResponseObj:
    def __init__(self, choices):
        self.choices = choices


def test_build_client_returns_none_when_import_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            raise ImportError("missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    provider = NIMProvider(api_key="key", client=None)
    assert provider._client is None


def test_build_client_returns_none_when_openai_class_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace())
    provider = NIMProvider(api_key="key", client=None)
    assert provider._client is None


def test_build_client_returns_none_when_constructor_fails(monkeypatch):
    class _OpenAI:
        def __init__(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_OpenAI))
    provider = NIMProvider(api_key="key", client=None)
    assert provider._client is None


def test_build_client_success_passes_base_url_and_timeout(monkeypatch):
    captured = {}

    class _OpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_OpenAI))
    provider = NIMProvider(api_key="key", timeout=7.0, client=None)

    assert provider._client is not None
    assert captured["api_key"] == "key"
    assert captured["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert captured["timeout"] == 7.0


def test_extract_content_handles_missing_choices_and_dict_message():
    assert NIMProvider._extract_content(types.SimpleNamespace(choices=[])) is None

    response = _ResponseObj(choices=[{"message": {"content": "  hello  "}}])
    assert NIMProvider._extract_content(response) == "hello"


def test_chat_returns_none_for_missing_key_or_client():
    provider_no_key = NIMProvider(api_key="", client=_Client(_Completions()))
    assert provider_no_key.generate_fix("code", "issue") is None

    provider_no_client = NIMProvider(api_key="key", client=None)
    provider_no_client._client = None
    assert provider_no_client.explain_issue("code", "issue") is None


def test_chat_returns_none_on_completion_exception_and_invalid_structure():
    provider_error = NIMProvider(
        api_key="key",
        client=_Client(_Completions(response=None, should_raise=True)),
    )
    assert provider_error.generate_fix("code", "issue") is None

    invalid_response = _ResponseObj(choices=[types.SimpleNamespace(message=None)])
    provider_invalid = NIMProvider(
        api_key="key",
        client=_Client(_Completions(response=invalid_response)),
    )
    assert provider_invalid.explain_issue("code", "issue") is None
