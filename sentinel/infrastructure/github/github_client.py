import json
from dataclasses import dataclass
from typing import Any


@dataclass
class GitHubClient:
    app_id: str | None
    installation_id: str | None
    private_key: str | None
    api_base_url: str = "https://api.github.com"

    def __post_init__(self) -> None:
        self._installation_token: str | None = None
        self._installation_token_expiry: float = 0.0

    @staticmethod
    def _now() -> float:
        return float(__import__("time").time())

    @staticmethod
    def _parse_expiry(expires_at: str | None) -> float:
        if not expires_at:
            return GitHubClient._now() + 300.0
        try:
            datetime_module = __import__("datetime")
            parsed = datetime_module.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            return float(parsed.timestamp())
        except Exception:
            return GitHubClient._now() + 300.0

    def _build_app_jwt(self) -> str | None:
        if not self.app_id or not self.private_key:
            return None

        now = int(self._now())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.app_id,
        }

        try:
            jwt_module = __import__("jwt")
            token = jwt_module.encode(payload, self.private_key, algorithm="RS256")
        except Exception:
            return None

        if isinstance(token, bytes):
            return token.decode("utf-8")
        return str(token)

    def _http_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload_bytes = None
        if data is not None:
            payload_bytes = json.dumps(data).encode("utf-8")

        try:
            urllib_request = __import__("urllib.request", fromlist=["Request", "urlopen"])
            urllib_error = __import__("urllib.error", fromlist=["HTTPError", "URLError"])
            request = urllib_request.Request(
                url=url,
                data=payload_bytes,
                headers=headers,
                method=method,
            )
            with urllib_request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                if raw.strip() == "":
                    return {}
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {}
        except Exception as exc:
            if hasattr(exc, "code") and hasattr(exc, "read"):
                try:
                    _ = exc.read()
                except Exception:
                    pass
            if isinstance(exc, getattr(urllib_error, "URLError", tuple())):
                return None
            if isinstance(exc, getattr(urllib_error, "HTTPError", tuple())):
                return None
            return None

    def _get_installation_token(self) -> str | None:
        if self._installation_token and self._now() < (self._installation_token_expiry - 30):
            return self._installation_token

        if not self.installation_id:
            return None

        app_jwt = self._build_app_jwt()
        if not app_jwt:
            return None

        endpoint = (
            f"{self.api_base_url.rstrip('/')}/app/installations/"
            f"{self.installation_id}/access_tokens"
        )
        response = self._http_json(
            "POST",
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            data={},
        )

        if not response:
            return None

        token = response.get("token")
        if not isinstance(token, str) or token.strip() == "":
            return None

        self._installation_token = token
        self._installation_token_expiry = self._parse_expiry(response.get("expires_at"))
        return self._installation_token

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> bool:
        if not owner or not repo or not body:
            return False

        token = self._get_installation_token()
        if not token:
            return False

        endpoint = (
            f"{self.api_base_url.rstrip('/')}/repos/{owner}/{repo}"
            f"/issues/{pr_number}/comments"
        )
        response = self._http_json(
            "POST",
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"token {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            data={"body": body},
        )

        return isinstance(response, dict) and isinstance(response.get("id"), int)
