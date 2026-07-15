"""M1: the async worker fetches real PR code from GitHub and posts a review.

Drives a real ``BackgroundWorker.start()`` iteration with a fake GitHub client
injected via ``bw_module._build_github_client`` (the module-level seam), proving
the full loop: fetch diff -> assess -> build_report -> post_comment.
"""

import asyncio

import sentinel.workers.background_worker as bw_module
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue


class _FakeGitHub:
    def __init__(
        self,
        code: str,
        files: list[str] | None = None,
        file_contents: dict[str, str] | None = None,
        corpus: list[str] | None = None,
        line_map: list | None = None,
    ) -> None:
        self._code = code
        self._files = files or []
        self._file_contents = file_contents or {}
        self._corpus = corpus or []
        self._line_map = line_map or []
        self.fetched_with: tuple | None = None
        self.corpus_ref: str | None = None
        self.posted: list[tuple] = []
        self.check_runs: list[dict] = []

    def get_pull_request_data(self, owner: str, repo: str, pr_number) -> dict:
        self.fetched_with = (owner, repo, pr_number)
        return {
            "code": self._code,
            "files": self._files,
            "file_contents": self._file_contents,
            "line_map": self._line_map,
        }

    def create_check_run(
        self, owner: str, repo: str, head_sha: str, *, conclusion, title, summary,
        text=None, annotations=None,
    ) -> bool:
        self.check_runs.append(
            {
                "owner": owner,
                "repo": repo,
                "head_sha": head_sha,
                "conclusion": conclusion,
                "title": title,
                "summary": summary,
                "text": text,
                "annotations": annotations or [],
            }
        )
        return True

    def get_pull_request_refs(self, owner: str, repo: str, pr_number) -> dict:
        return {"head_sha": "head-sha", "base_sha": "base-sha"}

    def get_repo_code_corpus(self, owner: str, repo: str, ref: str) -> list[str]:
        self.corpus_ref = ref
        return self._corpus

    def post_comment(self, owner: str, repo: str, pr_number, body: str) -> bool:
        self.posted.append((owner, repo, pr_number, body))
        return True

    def upsert_comment(self, owner: str, repo: str, pr_number, body: str) -> bool:
        # The worker posts via the idempotent upsert; record identically to post.
        self.posted.append((owner, repo, pr_number, body))
        return True


class _FakeLLM:
    """A stand-in LLM service that tags every finding with a distinctive explanation.

    Proves the worker actually routes findings through enrich_findings_with_llm before
    building the report (the enrichment matches by id(finding)).
    """

    ENRICHED_MARK = "SENTINEL_ENRICHED_EXPLANATION"

    def reset_budget(self) -> None:
        pass

    def generate_pr_audit(self, code, findings):
        return {
            id(finding): {"explanation": self.ENRICHED_MARK, "fix": "apply the fix"}
            for finding in findings
        }


def _drive_one_job(worker: BackgroundWorker, queue: JobQueue, fake: _FakeGitHub) -> None:
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(500):
            if fake.posted:
                break
            await real_sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep

    asyncio.run(_run())


def test_worker_fetches_pr_code_and_posts_structured_review(monkeypatch, capsys):
    # Vulnerable code the worker will only ever see by fetching it from GitHub.
    fake = _FakeGitHub('password = "hunter2"\napi_key = "sk-abcdefghijklmnopqrst"')
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)
    monkeypatch.setattr(bw_module, "_build_llm_service", lambda settings: _FakeLLM())

    async def _seed(queue: JobQueue) -> None:
        await queue.enqueue({"owner": "octo", "repo": "hello", "pr_number": 7})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    # Fetched using the job's identity (bare repo name).
    assert fake.fetched_with == ("octo", "hello", 7)

    # Posted exactly one structured review to the right PR.
    assert len(fake.posted) == 1
    owner, repo, pr_number, body = fake.posted[0]
    assert (owner, repo, pr_number) == ("octo", "hello", 7)
    assert "# Sentinel AI Code Review" in body
    assert "## Risk Score: HIGH" in body  # hardcoded secrets are HIGH severity
    assert "## Security Issues" in body
    # Proof the fetched findings were routed through LLM enrichment before the report
    # was built (the '## Explanation' section now carries the enriched text).
    assert _FakeLLM.ENRICHED_MARK in body

    # The one-liner still prints, now reflecting the fetched code (not empty -> LOW).
    assert "PR #7 Risk: HIGH" in capsys.readouterr().out


def test_worker_splits_owner_repo_from_full_name(monkeypatch, capsys):
    """When repo arrives as 'owner/name', the worker calls GitHub with the bare name."""
    fake = _FakeGitHub("x = 1")
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)

    async def _seed(queue: JobQueue) -> None:
        # owner separate, repo carries the full "owner/name" form.
        await queue.enqueue({"owner": "octo", "repo": "octo/hello", "pr_number": 3})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    assert fake.fetched_with == ("octo", "hello", 3)
    assert fake.posted and fake.posted[0][:3] == ("octo", "hello", 3)
    assert "PR #3 Risk:" in capsys.readouterr().out


def test_worker_skips_github_when_no_owner(monkeypatch, capsys):
    """Owner-less (flat/manual) jobs neither fetch nor post — no regression."""
    fake = _FakeGitHub('password = "leak"')
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)

    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        await queue.enqueue({"repo": "hello", "pr_number": 9})  # no owner
        worker = BackgroundWorker(queue)

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep
        task = asyncio.create_task(worker.start())
        for _ in range(500):
            if worker.processed_count >= 1:
                break
            await real_sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        bw_module.asyncio.sleep = original_sleep

    asyncio.run(_run())

    assert fake.fetched_with is None
    assert fake.posted == []
    # Still analyzed (empty code -> LOW) and printed the one-liner.
    assert "PR #9 Risk: LOW" in capsys.readouterr().out


def test_worker_review_includes_documentation_findings(monkeypatch, capsys):
    """M3 parity: async reviews now carry doc findings, like the sync path always has."""
    fake = _FakeGitHub(
        "x = 1",
        files=["README.md"],
        file_contents={"README.md": "notes only"},  # lacks install + usage guidance
    )
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)
    monkeypatch.setattr(bw_module, "_build_llm_service", lambda settings: _FakeLLM())

    async def _seed(queue: JobQueue) -> None:
        await queue.enqueue({"owner": "octo", "repo": "hello", "pr_number": 11})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    assert len(fake.posted) == 1
    body = fake.posted[0][3]
    assert "## Documentation Issues" in body
    assert "missing installation instructions" in body
    assert "missing usage guidance" in body
    assert "PR #11 Risk:" in capsys.readouterr().out


def test_worker_semantic_corpus_flags_duplicate_code(monkeypatch, capsys):
    """M5: the worker builds a real corpus from the base ref and flags duplication.

    The PR code is benign (no secrets), so the HIGH risk can only come from the
    semantic engine matching it against a corpus chunk.
    """
    pr_code = (
        "def compute_total(values):\n"
        "    total = 0\n"
        "    for value in values:\n"
        "        total = total + value\n"
        "    return total"
    )
    corpus_file = (
        "import math\n"
        "\n" + pr_code + "\n\n"
        "def unrelated_parser(text, sep, limit, flags):\n"
        "    return text.split(sep, limit)\n"
    )
    fake = _FakeGitHub(pr_code, corpus=[corpus_file])
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)
    monkeypatch.setattr(bw_module, "_build_llm_service", lambda settings: _FakeLLM())

    async def _seed(queue: JobQueue) -> None:
        await queue.enqueue({"owner": "octo", "repo": "hello", "pr_number": 12})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    assert fake.corpus_ref == "base-sha"  # corpus is built from the PR's BASE ref
    assert len(fake.posted) == 1
    body = fake.posted[0][3]
    assert "Similar findings detected: 1" in body
    assert "- No security issues detected." in body
    assert "## Risk Score: HIGH" in body  # driven purely by the semantic duplicate
    assert "PR #12 Risk: HIGH" in capsys.readouterr().out


def test_worker_posts_check_run_with_line_mapped_annotations(monkeypatch, capsys):
    """M6: alongside the comment, a check run lands with head-line annotations.

    The line map says the two added lines live at app.py lines 14-15 (a mid-file
    patch), so annotations must point THERE, not at code-blob lines 1-2.
    """
    code = 'password = "hunter2"\napi_key = "sk-abcdefghijklmnopqrst"'
    fake = _FakeGitHub(code, line_map=[("app.py", 14), ("app.py", 15)])
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)
    monkeypatch.setattr(bw_module, "_build_llm_service", lambda settings: _FakeLLM())

    async def _seed(queue: JobQueue) -> None:
        await queue.enqueue({"owner": "octo", "repo": "hello", "pr_number": 13})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    assert len(fake.posted) == 1  # the comment still lands
    assert len(fake.check_runs) == 1
    run = fake.check_runs[0]
    assert (run["owner"], run["repo"], run["head_sha"]) == ("octo", "hello", "head-sha")
    assert run["conclusion"] == "failure"  # HIGH risk -> failure
    assert "Risk: HIGH" in run["title"]
    assert "# Sentinel AI Code Review" in run["text"]  # full report attached

    security_annotations = [
        a for a in run["annotations"] if a["annotation_level"] == "failure"
    ]
    lines_hit = {(a["path"], a["start_line"]) for a in security_annotations}
    assert lines_hit == {("app.py", 14), ("app.py", 15)}
    assert all(a["start_line"] == a["end_line"] for a in security_annotations)
    assert "PR #13 Risk: HIGH" in capsys.readouterr().out


def test_worker_skips_check_run_when_flag_off(monkeypatch, capsys):
    """ENABLE_CHECKS=false: the comment posts, no check run is created."""
    monkeypatch.setenv("ENABLE_CHECKS", "false")
    fake = _FakeGitHub('password = "leak"')
    monkeypatch.setattr(bw_module, "_build_github_client", lambda settings: fake)
    monkeypatch.setattr(bw_module, "_build_llm_service", lambda settings: _FakeLLM())

    async def _seed(queue: JobQueue) -> None:
        await queue.enqueue({"owner": "octo", "repo": "hello", "pr_number": 14})

    queue = JobQueue()
    asyncio.run(_seed(queue))
    worker = BackgroundWorker(queue)

    _drive_one_job(worker, queue, fake)

    assert len(fake.posted) == 1
    assert fake.check_runs == []
    assert "PR #14 Risk:" in capsys.readouterr().out
