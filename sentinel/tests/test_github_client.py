from sentinel.infrastructure.github.github_client import GitHubClient


def test_post_comment_returns_false_without_credentials():
    client = GitHubClient(app_id=None, installation_id=None, private_key=None)

    assert client.post_comment("octo", "repo", 1, "body") is False


def test_get_installation_token_uses_cached_token():
    client = GitHubClient(app_id="123", installation_id="999", private_key="key")
    client._installation_token = "cached"
    client._installation_token_expiry = client._now() + 600

    token = client._get_installation_token()

    assert token == "cached"


def test_post_comment_success_when_token_and_comment_calls_succeed(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="key")

    calls: list[tuple[str, str]] = []

    def fake_http_json(method: str, url: str, headers: dict[str, str], data=None):
        _ = headers
        calls.append((method, url))
        if url.endswith("/access_tokens"):
            return {
                "token": "installation-token",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        return {"id": 12345}

    monkeypatch.setattr(client, "_build_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(client, "_http_json", fake_http_json)

    posted = client.post_comment("octo", "repo", 7, "hello")

    assert posted is True
    assert len(calls) == 2
    assert calls[0][0] == "POST"
    assert calls[1][1].endswith("/repos/octo/repo/issues/7/comments")


def test_post_comment_returns_false_when_comment_post_fails(monkeypatch):
    client = GitHubClient(app_id="123", installation_id="999", private_key="key")

    def fake_http_json(method: str, url: str, headers: dict[str, str], data=None):
        _ = method
        _ = headers
        _ = data
        if url.endswith("/access_tokens"):
            return {
                "token": "installation-token",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        return None

    monkeypatch.setattr(client, "_build_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(client, "_http_json", fake_http_json)

    assert client.post_comment("octo", "repo", 7, "hello") is False
