import json
from dataclasses import dataclass
from typing import Any


@dataclass
class GitHubClient:
    app_id: str | None
    installation_id: str | None
    private_key: str | None
    api_base_url: str = "https://api.github.com"

    # Hidden marker embedded in Sentinel's own PR comments so re-runs can find and
    # update the existing review in place instead of stacking duplicates. Bare (no
    # annotation) so @dataclass treats it as a class attribute, not a field.
    SENTINEL_COMMENT_MARKER = "<!-- sentinel-review -->"

    # Upper bound on pages fetched per list endpoint (10 x per_page=100 = 1000 items).
    MAX_LIST_PAGES = 10

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

    def _http_json_list(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> list[Any] | None:
        """Like _http_json, but for endpoints that return a JSON array.

        _http_json intentionally discards non-dict bodies (returns {}); the GitHub
        "list PR files" endpoint returns an array, so it needs its own primitive.
        Returns the parsed list on success, [] for an empty body, None on any error
        or when the body is not a list.
        """
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
                    return []
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
                return None
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

    def _get_paginated(self, endpoint: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        """GET all pages of a GitHub list endpoint (up to MAX_LIST_PAGES).

        Failure-safe: a non-list page (error) ends pagination, returning whatever
        was collected so far — partial results beat none for a best-effort client.
        """
        results: list[dict[str, Any]] = []
        for page in range(1, self.MAX_LIST_PAGES + 1):
            url = f"{endpoint}?per_page=100&page={page}"
            batch = self._http_json_list("GET", url, headers, data=None)
            if not isinstance(batch, list):
                break
            results.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:  # short page == last page
                break
        return results

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

    def list_issue_comments(self, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """GET the issue comments on a PR (all pages, capped). Returns [] on any failure."""
        if not owner or not repo:
            return []

        token = self._get_installation_token()
        if not token:
            return []

        endpoint = (
            f"{self.api_base_url.rstrip('/')}/repos/{owner}/{repo}"
            f"/issues/{pr_number}/comments"
        )
        return self._get_paginated(
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"token {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def update_comment(self, owner: str, repo: str, comment_id: int, body: str) -> bool:
        """PATCH an existing issue comment in place. Returns False on any failure."""
        if not owner or not repo or not body:
            return False

        token = self._get_installation_token()
        if not token:
            return False

        endpoint = (
            f"{self.api_base_url.rstrip('/')}/repos/{owner}/{repo}"
            f"/issues/comments/{comment_id}"
        )
        response = self._http_json(
            "PATCH",
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

    def upsert_comment(self, owner: str, repo: str, pr_number: int, body: str) -> bool:
        """Post Sentinel's review, or update its existing comment in place.

        Idempotent: the body carries a hidden ``SENTINEL_COMMENT_MARKER`` so later
        runs locate the prior review and PATCH it instead of stacking new comments.
        Falls back to creating a comment when none is found or the lookup fails.
        """
        if not owner or not repo or not body:
            return False

        if self.SENTINEL_COMMENT_MARKER in body:
            marked_body = body
        else:
            marked_body = f"{self.SENTINEL_COMMENT_MARKER}\n{body}"

        try:
            for comment in self.list_issue_comments(owner, repo, pr_number):
                existing_body = comment.get("body")
                comment_id = comment.get("id")
                if (
                    isinstance(existing_body, str)
                    and self.SENTINEL_COMMENT_MARKER in existing_body
                    and isinstance(comment_id, int)
                ):
                    return self.update_comment(owner, repo, comment_id, marked_body)
        except Exception:
            pass

        return self.post_comment(owner, repo, pr_number, marked_body)

    @staticmethod
    def _added_lines_from_patch(patch: str | None) -> str:
        """Reconstruct the code a PR introduces from a unified-diff patch hunk.

        Keeps added ('+') lines, dropping the '+++ ' file header and all
        context/removed/'@@' lines. Returns "" for anything non-string/empty.
        """
        if not isinstance(patch, str) or not patch:
            return ""
        added: list[str] = []
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
        return "\n".join(added)

    def get_pull_request_files(self, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """GET the changed files of a PR (all pages, capped). Returns [] on any failure."""
        if not owner or not repo:
            return []

        token = self._get_installation_token()
        if not token:
            return []

        endpoint = (
            f"{self.api_base_url.rstrip('/')}/repos/{owner}/{repo}"
            f"/pulls/{pr_number}/files"
        )
        return self._get_paginated(
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"token {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def get_pull_request_data(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Assemble everything a review needs from one PR-files fetch.

        Returns ``{"code": str, "files": list[str], "file_contents": dict[str, str]}``:
        added-line code joined across patches, the changed file names, and each file's
        patch text keyed by name (the doc reviewer's content source, mirroring the sync
        path's patch fallback). Empty values on failure.
        """
        items = self.get_pull_request_files(owner, repo, pr_number)
        segments: list[str] = []
        files: list[str] = []
        file_contents: dict[str, str] = {}
        for item in items:
            name = item.get("filename")
            patch = item.get("patch")
            if isinstance(name, str) and name:
                files.append(name)
                if isinstance(patch, str) and patch:
                    file_contents[name] = patch
            added = self._added_lines_from_patch(patch)
            if added:
                segments.append(added)
        return {"code": "\n".join(segments), "files": files, "file_contents": file_contents}

    def get_pull_request_code(self, owner: str, repo: str, pr_number: int) -> str:
        """Assemble the added-line code introduced by a PR. Returns "" on failure."""
        return self.get_pull_request_data(owner, repo, pr_number)["code"]
