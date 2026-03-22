import asyncio

import pytest

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.application.use_cases import process_pull_request as use_case_module
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue
import sentinel.workers.background_worker as bw_module


class _ExplodingDebtService:
    def evaluate_debt(self, code: str) -> dict:
        raise RuntimeError("debt boom")


class _ExplodingSecurityService:
    def analyze(self, code: str) -> dict:
        raise RuntimeError("security boom")


class _ExplodingSemanticService:
    def detect_duplicates(self, new_code: str, existing_code_list: list[str]) -> list:
        raise RuntimeError("semantic boom")


class _HighSemanticService:
    def detect_duplicates(self, new_code: str, existing_code_list: list[str]) -> list[Finding]:
        return [
            Finding(
                rule="semantic_duplicate",
                match="def a(): return 1",
                severity=SeverityLevel.HIGH,
                finding_type="semantic",
                similarity_score=0.99,
            )
        ]


class _RaisingRiskEngine:
    def assess_resilient(self, **_: object) -> dict:
        raise RuntimeError("risk boom")


def test_orchestrator_rejects_non_dict_payload():
    async def _run() -> None:
        queue = JobQueue()
        orchestrator = AuditOrchestrator(queue)
        with pytest.raises(TypeError):
            await orchestrator.enqueue_pull_request("bad")  # type: ignore[arg-type]

    asyncio.run(_run())


def test_orchestrator_includes_supported_optional_fields():
    async def _run() -> None:
        queue = JobQueue()
        orchestrator = AuditOrchestrator(queue)
        payload = {
            "repo": "my-repo",
            "pr_number": 7,
            "author": "alice",
            "files": ["a.py", "b.py"],
            "code": "def x():\n    return 1",
            "extra": "ignored",
        }
        await orchestrator.enqueue_pull_request(payload)
        job = await queue.dequeue()
        assert job["author"] == "alice"
        assert job["files"] == ["a.py", "b.py"]
        assert job["code"] == "def x():\n    return 1"
        assert "extra" not in job

    asyncio.run(_run())


def test_job_queue_rejects_non_dict_jobs():
    async def _run() -> None:
        queue = JobQueue()
        with pytest.raises(TypeError):
            await queue.enqueue("bad")  # type: ignore[arg-type]

    asyncio.run(_run())


def test_assess_resilient_survives_engine_failures():
    engine = RiskEngine(
        debt_service=_ExplodingDebtService(),
        security_service=_ExplodingSecurityService(),
        semantic_service=_ExplodingSemanticService(),
    )

    result = engine.assess_resilient(
        code="def changed():\n    return 2",
        existing_code_list=["def base():\n    return 1"],
        warn_threshold_seconds=0,
    )

    assert result["severity"] == SeverityLevel.LOW
    assert result["security_findings_count"] == 0
    assert result["semantic_findings_count"] == 0
    assert result["complexity"] == 1


def test_assess_resilient_sets_high_for_high_semantic_finding():
    engine = RiskEngine(semantic_service=_HighSemanticService())

    result = engine.assess_resilient(
        code="def changed():\n    return 2",
        existing_code_list=["def base():\n    return 1"],
        warn_threshold_seconds=0,
    )

    assert result["severity"] == SeverityLevel.HIGH
    assert result["semantic"]["severity"] == SeverityLevel.HIGH
    assert result["semantic_findings_count"] == 1


def test_use_case_handles_none_and_non_string_code(monkeypatch):
    use_case = ProcessPullRequestUseCase(RiskEngine(), ReportService())

    result_none = use_case.execute({"repo": "x", "pr_number": 1, "code": None})
    result_int = use_case.execute({"repo": "x", "pr_number": 2, "code": 123})

    assert result_none == "PR #1 Risk: LOW"
    assert result_int == "PR #2 Risk: LOW"


def test_use_case_truncates_large_code_and_still_processes(monkeypatch):
    monkeypatch.setattr(use_case_module, "MAX_CODE_LENGTH", 5)
    use_case = ProcessPullRequestUseCase(RiskEngine(), ReportService())

    result = use_case.execute({"repo": "x", "pr_number": 3, "code": "abcdef"})

    assert result == "PR #3 Risk: LOW"


def test_use_case_falls_back_to_low_on_risk_engine_failure():
    use_case = ProcessPullRequestUseCase(_RaisingRiskEngine(), ReportService())

    result = use_case.execute({"repo": "x", "pr_number": 4, "code": "def a():\n    return 1"})

    assert result == "PR #4 Risk: LOW"


def test_use_case_latency_warning_path_is_safe(monkeypatch):
    monkeypatch.setattr(use_case_module, "TARGET_LATENCY_SECONDS", -1.0)
    use_case = ProcessPullRequestUseCase(RiskEngine(), ReportService())

    result = use_case.execute({"repo": "x", "pr_number": 6, "code": ""})

    assert result == "PR #6 Risk: LOW"


def test_worker_continues_after_processing_error(capsys):
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)

        await queue.enqueue({})
        await queue.enqueue({"repo": "recovery", "pr_number": 5})

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(2000):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #5 Risk:" in output
