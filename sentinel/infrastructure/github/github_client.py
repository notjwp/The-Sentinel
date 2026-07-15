import base64
import json
from dataclasses import dataclass
from typing import Any

from sentinel.domain.services.document_service import DocumentService


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

    # Bounds for repo-content fetches (semantic corpus + full doc content).
    CORPUS_MAX_FILES = 20  # most .py blobs fetched per corpus build
    CORPUS_MAX_FILE_BYTES = 50_000  # skip larger blobs (tree items carry size)
    MAX_CONTENT_BYTES = 200_000  # hard cap on any decoded file content

    def __post_init__(self) -> None:
        self._installation_token: str | None = None
        self._installation_token_expiry: float = 0.0
        # Corpus per (owner, repo, ref); a ref pins content, so entries never go
        # stale — capacity-bounded only (plain dict keeps insertion order).
        self._corpus_cache: dict[tuple[str, str, str], list[str]] = {}

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

    def _token_headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_pull_request_refs(self, owner: str, repo: str, pr_number: int) -> dict[str, str]:
        """GET the PR's head/base commit SHAs. Returns {} on any failure."""
        if not owner or not repo:
            return {}

        token = self._get_installation_token()
        if not token:
            return {}

        endpoint = f"{self.api_base_url.rstrip('/')}/repos/{owner}/{repo}/pulls/{pr_number}"
        response = self._http_json("GET", endpoint, headers=self._token_headers(token))
        if not isinstance(response, dict):
            return {}

        refs: dict[str, str] = {}
        for key in ("head", "base"):
            sha = response.get(key, {}).get("sha") if isinstance(response.get(key), dict) else None
            if isinstance(sha, str) and sha:
                refs[f"{key}_sha"] = sha
        return refs

    def _decode_content_response(self, response: dict[str, Any] | None) -> str | None:
        """Decode a contents/blob API response body to text.

        None = failure/unsupported encoding; "" = a genuinely empty file. The
        distinction matters: callers fall back to patch text only on failure.
        """
        if not isinstance(response, dict):
            return None
        if response.get("encoding") != "base64":
            return None
        content = response.get("content")
        if not isinstance(content, str):
            return None
        if content.strip() == "":
            return ""
        try:
            # The API wraps base64 with newlines; b64decode tolerates them only
            # after they're stripped.
            raw = base64.b64decode(content.replace("\n", ""), validate=False)
        except Exception:
            return None
        return raw[: self.MAX_CONTENT_BYTES].decode("utf-8", errors="replace")

    def get_content_by_url(self, contents_url: str) -> str | None:
        """GET a PR file's full content via its contents_url (pinned to head ref).

        Returns the decoded text, "" for an empty file, or None on any failure.
        """
        if not isinstance(contents_url, str) or not contents_url.startswith("http"):
            return None

        token = self._get_installation_token()
        if not token:
            return None

        response = self._http_json("GET", contents_url, headers=self._token_headers(token))
        return self._decode_content_response(response)

    def get_repo_code_corpus(self, owner: str, repo: str, ref: str) -> list[str]:
        """Fetch the repo's Python sources at a ref (the semantic-duplicate corpus).

        Bounded (CORPUS_MAX_FILES / CORPUS_MAX_FILE_BYTES) and failure-safe:
        partial results beat none; [] when nothing could be fetched. Cached per
        (owner, repo, ref) — a ref pins content, so cache entries never go stale.
        """
        if not owner or not repo or not ref:
            return []

        cache_key = (owner, repo, ref)
        cached = self._corpus_cache.get(cache_key)
        if cached is not None:
            return cached

        token = self._get_installation_token()
        if not token:
            return []

        base = self.api_base_url.rstrip("/")
        tree_response = self._http_json(
            "GET",
            f"{base}/repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
            headers=self._token_headers(token),
        )
        tree = tree_response.get("tree") if isinstance(tree_response, dict) else None
        if not isinstance(tree, list):
            return []

        blob_shas: list[str] = []
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            path = item.get("path")
            sha = item.get("sha")
            size = item.get("size")
            if not isinstance(path, str) or not path.endswith(".py") or path.startswith("."):
                continue
            if not isinstance(sha, str) or not sha:
                continue
            if isinstance(size, int) and size > self.CORPUS_MAX_FILE_BYTES:
                continue
            blob_shas.append(sha)
            if len(blob_shas) >= self.CORPUS_MAX_FILES:
                break

        corpus: list[str] = []
        for sha in blob_shas:
            blob = self._http_json(
                "GET",
                f"{base}/repos/{owner}/{repo}/git/blobs/{sha}",
                headers=self._token_headers(token),
            )
            text = self._decode_content_response(blob)
            if text:
                corpus.append(text)

        self._corpus_cache[cache_key] = corpus
        while len(self._corpus_cache) > 4:
            self._corpus_cache.pop(next(iter(self._corpus_cache)))
        return corpus

    def get_pull_request_data(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Assemble everything a review needs from one PR-files fetch.

        Returns ``{"code": str, "files": list[str], "file_contents": dict[str, str]}``:
        added-line code joined across patches, the changed file names, and per-file
        content for the doc reviewer. Documentation files (.md/.txt) carry their FULL
        text fetched from the PR head (via the item's contents_url), so doc rules judge
        the whole document rather than a patch fragment; on fetch failure — and for all
        other files — the patch text remains the content source (the M3 behavior, which
        also mirrors the sync path's patch fallback). Empty values on failure.
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
                full_content: str | None = None
                if name.strip().lower().endswith(DocumentService.DOC_EXTENSIONS):
                    contents_url = item.get("contents_url")
                    if isinstance(contents_url, str):
                        full_content = self.get_content_by_url(contents_url)
                if full_content is not None:
                    file_contents[name] = full_content
                elif isinstance(patch, str) and patch:
                    file_contents[name] = patch
            added = self._added_lines_from_patch(patch)
            if added:
                segments.append(added)
        return {"code": "\n".join(segments), "files": files, "file_contents": file_contents}

    def get_pull_request_code(self, owner: str, repo: str, pr_number: int) -> str:
        """Assemble the added-line code introduced by a PR. Returns "" on failure."""
        return self.get_pull_request_data(owner, repo, pr_number)["code"]
