import builtins
import types

import pytest

from sentinel.infrastructure.github.github_client import GitHubClient


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = exc_type
        _ = exc
        _ = tb
        return False


class _FakeHTTPError(Exception):
    def __init__(self, message: str = "http error") -> None:
        super().__init__(message)
        self.code = 500

    def read(self) -> bytes:
        return b"{}"


class _FakeURLError(Exception):
    pass


def _patch_imports(
    monkeypatch,
    *,
    jwt_encode=None,
    jwt_raises: bool = False,
    response_payload: bytes = b"{}",
    urlopen_error: Exception | None = None,
) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "jwt":
            if jwt_raises:
                raise ImportError("jwt unavailable")
            encode = jwt_encode or (lambda payload, private_key, algorithm: "token")
            return types.SimpleNamespace(encode=encode)

        if name == "urllib.request":
            def request_factory(**kwargs):
                return kwargs

            def fake_urlopen(request, timeout=10):
                _ = request
                _ = timeout
                if urlopen_error is not None:
                    raise urlopen_error
                return _FakeResponse(response_payload)

            return types.SimpleNamespace(Request=request_factory, urlopen=fake_urlopen)

        if name == "urllib.error":
            return types.SimpleNamespace(HTTPError=_FakeHTTPError, URLError=_FakeURLError)

        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_build_app_jwt_success_with_bytes(monkeypatch):
    captured = {}

    def fake_encode(payload, private_key, algorithm):
        captured["payload"] = payload
        captured["private_key"] = private_key
        captured["algorithm"] = algorithm
        return b"jwt-token"

    _patch_imports(monkeypatch, jwt_encode=fake_encode)
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    token = client._build_app_jwt()

    assert token == "jwt-token"
    assert captured["private_key"] == "private"
    assert captured["algorithm"] == "RS256"
    assert captured["payload"]["iss"] == "123"


def test_build_app_jwt_success_with_string_token(monkeypatch):
    _patch_imports(
        monkeypatch,
        jwt_encode=lambda payload, private_key, algorithm: "string-token",
    )
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    assert client._build_app_jwt() == "string-token"


def test_build_app_jwt_returns_none_for_missing_credentials_or_jwt_failure(monkeypatch):
    no_creds = GitHubClient(app_id=None, installation_id="1", private_key=None)
    assert no_creds._build_app_jwt() is None

    _patch_imports(monkeypatch, jwt_raises=True)
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")
    assert client._build_app_jwt() is None


def test_parse_expiry_handles_missing_and_invalid_values():
    now = GitHubClient._now()
    missing = GitHubClient._parse_expiry(None)
    invalid = GitHubClient._parse_expiry("not-a-date")

    assert missing >= now + 299
    assert invalid >= now + 299


def test_http_json_returns_empty_dict_on_empty_body(monkeypatch):
    _patch_imports(monkeypatch, response_payload=b"")
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    result = client._http_json("POST", "https://api.github.com/x", headers={}, data={})

    assert result == {}


def test_http_json_returns_empty_dict_for_non_dict_json(monkeypatch):
    _patch_imports(monkeypatch, response_payload=b"[1,2,3]")
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    result = client._http_json("POST", "https://api.github.com/x", headers={}, data={})

    assert result == {}


def test_http_json_returns_dict_payload(monkeypatch):
    _patch_imports(monkeypatch, response_payload=b'{"id": 42}')
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    result = client._http_json("POST", "https://api.github.com/x", headers={}, data={})

    assert result == {"id": 42}


def test_http_json_handles_network_and_http_errors(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    _patch_imports(monkeypatch, urlopen_error=_FakeURLError("network"))
    assert client._http_json("POST", "https://api.github.com/x", headers={}, data={}) is None

    _patch_imports(monkeypatch, urlopen_error=_FakeHTTPError("api failure"))
    assert client._http_json("POST", "https://api.github.com/x", headers={}, data={}) is None


def test_http_json_handles_http_error_read_failure(monkeypatch):
    class _UnreadableHTTPError(_FakeHTTPError):
        def read(self) -> bytes:
            raise RuntimeError("cannot read body")

    _patch_imports(monkeypatch, urlopen_error=_UnreadableHTTPError("api failure"))
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    assert client._http_json("POST", "https://api.github.com/x", headers={}, data={}) is None


def test_http_json_handles_generic_exception(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    _patch_imports(monkeypatch, urlopen_error=RuntimeError("boom"))
    assert client._http_json("POST", "https://api.github.com/x", headers={}, data={}) is None


def test_access_token_retrieval_success_and_cache(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    calls = {"count": 0}

    def fake_http_json(method: str, url: str, headers: dict[str, str], data=None):
        _ = method
        _ = headers
        _ = data
        calls["count"] += 1
        assert url.endswith("/app/installations/999/access_tokens")
        return {
            "token": "installation-token",
            "expires_at": "2099-01-01T00:00:00Z",
        }

    monkeypatch.setattr(client, "_build_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(client, "_http_json", fake_http_json)

    first = client._get_installation_token()
    second = client._get_installation_token()

    assert first == "installation-token"
    assert second == "installation-token"
    assert calls["count"] == 1


@pytest.mark.parametrize(
    "response",
    [None, {}, {"token": 123}, {"token": "   "}],
)
def test_access_token_failure_for_invalid_response(monkeypatch, response):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_build_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(client, "_http_json", lambda method, url, headers, data=None: response)

    assert client._get_installation_token() is None


def test_invalid_installation_id_returns_none():
    client = GitHubClient(app_id="123", installation_id="", private_key="private")

    assert client._get_installation_token() is None


def test_get_installation_token_returns_none_when_jwt_generation_fails(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_build_app_jwt", lambda: None)

    assert client._get_installation_token() is None


def test_post_comment_success_and_failures(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")

    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")
    monkeypatch.setattr(client, "_http_json", lambda method, url, headers, data=None: {"id": 1})
    assert client.post_comment("octo", "repo", 7, "hello") is True

    monkeypatch.setattr(client, "_http_json", lambda method, url, headers, data=None: None)
    assert client.post_comment("octo", "repo", 7, "hello") is False

    monkeypatch.setattr(client, "_get_installation_token", lambda: None)
    assert client.post_comment("octo", "repo", 7, "hello") is False


def test_post_comment_rejects_empty_inputs():
    client = GitHubClient(app_id=None, installation_id=None, private_key=None)

    assert client.post_comment("", "repo", 1, "body") is False
    assert client.post_comment("octo", "", 1, "body") is False
    assert client.post_comment("octo", "repo", 1, "") is False
