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


# --- PR fetch (M1) ---------------------------------------------------------


def test_added_lines_from_patch_extracts_added_code_only():
    patch = (
        "@@ -1,3 +1,4 @@\n"
        "+++ b/app.py\n"          # file header must be ignored
        " unchanged = 1\n"        # context line dropped
        "-removed = 2\n"          # removed line dropped
        "+password = \"hunter2\"\n"
        "+api_key = \"sk-abc\"\n"
    )

    result = GitHubClient._added_lines_from_patch(patch)

    assert result == 'password = "hunter2"\napi_key = "sk-abc"'


@pytest.mark.parametrize("patch", [None, "", 123, [], "@@ -1 +1 @@\n context"])
def test_added_lines_from_patch_safe_for_non_added_content(patch):
    assert GitHubClient._added_lines_from_patch(patch) == ""


def test_http_json_list_returns_list_empty_and_none(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="1", private_key="private")

    _patch_imports(monkeypatch, response_payload=b'[{"filename": "a.py"}]')
    assert client._http_json_list("GET", "https://api.github.com/x", headers={}) == [
        {"filename": "a.py"}
    ]

    _patch_imports(monkeypatch, response_payload=b"")
    assert client._http_json_list("GET", "https://api.github.com/x", headers={}) == []

    # A dict body (not a list) must not be surfaced as a list.
    _patch_imports(monkeypatch, response_payload=b'{"message": "not found"}')
    assert client._http_json_list("GET", "https://api.github.com/x", headers={}) is None

    _patch_imports(monkeypatch, urlopen_error=_FakeURLError("network"))
    assert client._http_json_list("GET", "https://api.github.com/x", headers={}) is None


def test_get_pull_request_files_success_and_failures(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")

    def fake_list(method, url, headers, data=None):
        assert method == "GET"
        assert url.endswith("/repos/octo/repo/pulls/7/files?per_page=100")
        assert headers["Authorization"] == "token installation-token"
        return [{"filename": "a.py", "patch": "+x = 1"}, "junk", {"filename": "b.py"}]

    monkeypatch.setattr(client, "_http_json_list", fake_list)
    files = client.get_pull_request_files("octo", "repo", 7)
    assert files == [{"filename": "a.py", "patch": "+x = 1"}, {"filename": "b.py"}]

    # None response -> []
    monkeypatch.setattr(client, "_http_json_list", lambda *a, **k: None)
    assert client.get_pull_request_files("octo", "repo", 7) == []


def test_get_pull_request_files_returns_empty_without_owner_or_token(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")
    assert client.get_pull_request_files("", "repo", 7) == []
    assert client.get_pull_request_files("octo", "", 7) == []

    monkeypatch.setattr(client, "_get_installation_token", lambda: None)
    assert client.get_pull_request_files("octo", "repo", 7) == []


def test_get_pull_request_code_assembles_added_lines(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")

    monkeypatch.setattr(
        client,
        "get_pull_request_files",
        lambda owner, repo, pr_number: [
            {"filename": "a.py", "patch": "@@ -0,0 +1 @@\n+import os\n+os.system(cmd)"},
            {"filename": "bin.png"},  # no patch -> skipped
            {"filename": "b.py", "patch": "+value = 2"},
        ],
    )

    code = client.get_pull_request_code("octo", "repo", 7)
    assert code == "import os\nos.system(cmd)\nvalue = 2"


def test_get_pull_request_code_empty_when_no_files(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "get_pull_request_files", lambda *a, **k: [])
    assert client.get_pull_request_code("octo", "repo", 7) == ""


def test_list_issue_comments_success_and_failures(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")

    def fake_list(method, url, headers, data=None):
        assert method == "GET"
        assert url.endswith("/repos/octo/repo/issues/7/comments?per_page=100")
        assert headers["Authorization"] == "token installation-token"
        return [{"id": 1, "body": "hi"}, "junk", {"id": 2, "body": "yo"}]

    monkeypatch.setattr(client, "_http_json_list", fake_list)
    assert client.list_issue_comments("octo", "repo", 7) == [
        {"id": 1, "body": "hi"},
        {"id": 2, "body": "yo"},
    ]

    # None (error) response -> []
    monkeypatch.setattr(client, "_http_json_list", lambda *a, **k: None)
    assert client.list_issue_comments("octo", "repo", 7) == []


def test_list_issue_comments_empty_without_owner_or_token(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")
    assert client.list_issue_comments("", "repo", 7) == []
    assert client.list_issue_comments("octo", "", 7) == []

    monkeypatch.setattr(client, "_get_installation_token", lambda: None)
    assert client.list_issue_comments("octo", "repo", 7) == []


def test_update_comment_success_and_failures(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    monkeypatch.setattr(client, "_get_installation_token", lambda: "installation-token")

    def fake_patch(method, url, headers, data=None):
        assert method == "PATCH"
        assert url.endswith("/repos/octo/repo/issues/comments/55")
        assert data == {"body": "updated"}
        return {"id": 55}

    monkeypatch.setattr(client, "_http_json", fake_patch)
    assert client.update_comment("octo", "repo", 55, "updated") is True

    monkeypatch.setattr(client, "_http_json", lambda *a, **k: None)
    assert client.update_comment("octo", "repo", 55, "updated") is False

    monkeypatch.setattr(client, "_get_installation_token", lambda: None)
    assert client.update_comment("octo", "repo", 55, "updated") is False


def test_update_comment_rejects_empty_inputs():
    client = GitHubClient(app_id=None, installation_id=None, private_key=None)
    assert client.update_comment("", "repo", 1, "body") is False
    assert client.update_comment("octo", "", 1, "body") is False
    assert client.update_comment("octo", "repo", 1, "") is False


def test_upsert_comment_updates_existing_marked_comment(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    marker = GitHubClient.SENTINEL_COMMENT_MARKER
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        client,
        "list_issue_comments",
        lambda *a, **k: [
            {"id": 10, "body": "unrelated human comment"},
            {"id": 11, "body": f"{marker}\n# old Sentinel review"},
        ],
    )

    def fake_update(owner, repo, comment_id, body):
        calls["update"] = (owner, repo, comment_id, body)
        return True

    def fake_post(*a, **k):
        calls["post"] = True
        return True

    monkeypatch.setattr(client, "update_comment", fake_update)
    monkeypatch.setattr(client, "post_comment", fake_post)

    assert client.upsert_comment("octo", "repo", 7, "# fresh review") is True
    # PATCHed the marked comment (id 11); never created a new one.
    assert calls["update"][:3] == ("octo", "repo", 11)
    assert calls["update"][3] == f"{marker}\n# fresh review"  # marker prepended once
    assert "post" not in calls


def test_upsert_comment_creates_when_no_marked_comment(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    marker = GitHubClient.SENTINEL_COMMENT_MARKER
    posted: dict[str, object] = {}

    monkeypatch.setattr(
        client, "list_issue_comments", lambda *a, **k: [{"id": 10, "body": "just a human"}]
    )
    monkeypatch.setattr(client, "update_comment", lambda *a, **k: pytest.fail("should not update"))

    def fake_post(owner, repo, pr_number, body):
        posted["args"] = (owner, repo, pr_number, body)
        return True

    monkeypatch.setattr(client, "post_comment", fake_post)

    assert client.upsert_comment("octo", "repo", 7, "# review") is True
    assert posted["args"] == ("octo", "repo", 7, f"{marker}\n# review")


def test_upsert_comment_falls_back_to_post_when_list_fails(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")

    def boom(*a, **k):
        raise RuntimeError("list blew up")

    monkeypatch.setattr(client, "list_issue_comments", boom)
    monkeypatch.setattr(client, "post_comment", lambda *a, **k: True)

    assert client.upsert_comment("octo", "repo", 7, "# review") is True


def test_upsert_comment_does_not_double_prepend_marker(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="private")
    marker = GitHubClient.SENTINEL_COMMENT_MARKER
    seen: dict[str, str] = {}

    def fake_post(owner, repo, pr, body):
        seen["body"] = body
        return True

    monkeypatch.setattr(client, "list_issue_comments", lambda *a, **k: [])
    monkeypatch.setattr(client, "post_comment", fake_post)

    already_marked = f"{marker}\n# review"
    assert client.upsert_comment("octo", "repo", 7, already_marked) is True
    assert seen["body"] == already_marked  # unchanged; marker not duplicated


def test_upsert_comment_rejects_empty_inputs():
    client = GitHubClient(app_id=None, installation_id=None, private_key=None)
    assert client.upsert_comment("", "repo", 1, "body") is False
    assert client.upsert_comment("octo", "", 1, "body") is False
    assert client.upsert_comment("octo", "repo", 1, "") is False
