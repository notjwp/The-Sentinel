from sentinel.infrastructure.llm.nim_provider import NIMProvider


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, response: _Response | None = None, should_raise: bool = False) -> None:
        self.response = response or _Response("ok")
        self.should_raise = should_raise
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.should_raise:
            raise RuntimeError("request failed")
        return self.response


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _Client:
    def __init__(self, completions: _Completions) -> None:
        self.chat = _Chat(completions)


def test_generate_fix_returns_clean_text_from_nim_response():
    completions = _Completions(response=_Response("  safe_code()  "))
    provider = NIMProvider(api_key="k", client=_Client(completions), timeout=10.0)
    assert provider.generate_fix("bad_code", "issue") == "safe_code()"

    call = completions.calls[0]
    assert call["model"] == "meta/llama-3.3-70b-instruct"
    assert call["stream"] is False


def test_explain_issue_returns_none_on_api_error():
    completions = _Completions(should_raise=True)
    provider = NIMProvider(api_key="k", client=_Client(completions))
    assert provider.explain_issue("bad_code", "issue") is None


def test_returns_none_when_missing_api_key():
    provider = NIMProvider(api_key="", client=_Client(_Completions()))
    assert provider.generate_fix("code", "issue") is None


def test_returns_none_on_invalid_payload_shape():
    provider = NIMProvider(api_key="k", client=_Client(_Completions(response=_Response(None))))
    assert provider.explain_issue("code", "issue") is None
