"""Webhook controller internals: payload extraction and job building.

Trimmed in M9 when the synchronous ``code``-in-body mode was removed. The tests
that drove the engines through the HTTP layer are gone; what a real delivery
still exercises — pulling repo/owner/PR/author out of a nested GitHub body and
turning it into a queue job — is covered here.
"""

from asyncio import run

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sentinel.api.webhook_controller as wc


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    async def enqueue_pull_request(self, payload: dict) -> None:
        self.enqueued.append(payload)


class _RaisingJsonRequest:
    """Stands in for a request whose body is not parseable JSON."""

    headers: dict[str, str] = {}

    async def json(self):
        raise ValueError("not json")


class _ListJsonRequest:
    headers: dict[str, str] = {}

    async def json(self):
        return ["not", "a", "dict"]


# --- extractors -------------------------------------------------------------


def test_helper_extractors_cover_branch_variants():
    assert wc._as_dict([]) == {}

    assert wc._extract_repo_name({"repository": {"full_name": "octo/repo"}}, None) == "repo"
    assert wc._extract_repo_name({"repository": {"name": "repo2"}}, None) == "repo2"
    assert wc._extract_repo_name({}, None) is None

    assert wc._extract_owner({"repository": {"owner": {"login": "octo"}}}, None) == "octo"
    assert wc._extract_owner({"repository": {"full_name": "octo/repo"}}, None) == "octo"
    assert wc._extract_owner({}, "foo/bar") == "foo"
    assert wc._extract_owner({}, "bare-name") is None

    assert wc._extract_pr_number({"pull_request": {"number": 8}}, None) == 8
    assert wc._extract_pr_number({"number": 9}, None) == 9
    assert wc._extract_pr_number({}, None) is None

    assert wc._extract_author({"pull_request": {"user": {"login": "alice"}}}, None) == "alice"
    assert wc._extract_author({"sender": {"login": "bob"}}, None) == "bob"
    assert wc._extract_author({}, None) is None

    assert wc._extract_files(
        {"files": ["README.md", {"filename": "a.md"}, {"path": "b.txt"}]}, None
    ) == ["README.md", "a.md", "b.txt"]
    assert wc._extract_files({"files": "not-a-list"}, None) == []


# --- raw body parsing -------------------------------------------------------


def test_raw_json_returns_empty_dict_for_unparseable_or_non_object_bodies():
    assert run(wc._raw_json(_RaisingJsonRequest())) == {}
    assert run(wc._raw_json(_ListJsonRequest())) == {}


# --- job building -----------------------------------------------------------


def test_build_job_reads_a_real_github_payload():
    job = wc._build_job(
        {
            "action": "opened",
            "repository": {"full_name": "octo/repo", "owner": {"login": "octo"}},
            "pull_request": {"number": 4, "user": {"login": "alice"}},
            "files": [{"filename": "README.md"}],
        },
        wc.WebhookPayload(),
    )
    assert job == {
        "repo": "repo",
        "owner": "octo",
        "pr_number": 4,
        "author": "alice",
        "files": ["README.md"],
    }


def test_build_job_prefers_directly_supplied_fields():
    job = wc._build_job(
        {"repository": {"full_name": "octo/repo"}, "pull_request": {"number": 4}},
        wc.WebhookPayload(repo="other/name", pr_number=99, author="manual"),
    )
    assert job["repo"] == "other/name"
    assert job["pr_number"] == 99
    assert job["author"] == "manual"
    # Owner is the exception: _extract_owner reads the event body first and only
    # falls back to splitting the supplied repo. Long-standing asymmetry with
    # _extract_repo_name, harmless in practice — a real delivery carries no
    # top-level `repo`, and a manual trigger carries no `repository` body, so
    # the two sources never both appear outside a test like this one.
    assert job["owner"] == "octo"


def test_build_job_splits_owner_from_a_supplied_repo_when_the_body_is_bare():
    job = wc._build_job({}, wc.WebhookPayload(repo="other/name", pr_number=1))
    assert job["owner"] == "other"


def test_build_job_omits_absent_fields_rather_than_emitting_none():
    assert wc._build_job({}, wc.WebhookPayload()) == {}


# --- route ------------------------------------------------------------------


def _client(orchestrator: _DummyOrchestrator) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[wc.get_orchestrator] = lambda: orchestrator
    app.include_router(wc.router)
    return TestClient(app)


def test_webhook_malformed_payload_returns_422():
    assert _client(_DummyOrchestrator()).post("/webhook", json=["bad"]).status_code == 422


def test_webhook_queues_fields_pulled_from_a_github_payload():
    orchestrator = _DummyOrchestrator()

    response = _client(orchestrator).post(
        "/webhook",
        json={
            "action": "opened",
            "repository": {"full_name": "octo/repo", "owner": {"login": "octo"}},
            "pull_request": {"number": 4, "user": {"login": "alice"}},
        },
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.json() == {"status": "queued"}
    assert orchestrator.enqueued == [
        {"repo": "repo", "owner": "octo", "pr_number": 4, "author": "alice"}
    ]
