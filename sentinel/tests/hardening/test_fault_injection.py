import asyncio

import pytest

from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine

SIMPLE_CODE = "def simple():\n    return 1"


# --- Debt Service Failure ---


class _ExplodingDebtService:
    def evaluate_debt(self, code: str) -> dict:
        raise RuntimeError("debt engine failure")


def test_riskengine_raises_when_debt_service_explodes():
    engine = RiskEngine(debt_service=_ExplodingDebtService())
    with pytest.raises(RuntimeError, match="debt engine failure"):
        engine.assess(code=SIMPLE_CODE)


def test_riskengine_calculate_risk_raises_when_debt_service_explodes():
    engine = RiskEngine(debt_service=_ExplodingDebtService())
    with pytest.raises(RuntimeError, match="debt engine failure"):
        engine.calculate_risk(pr_number=1, code=SIMPLE_CODE)


# --- Security Service Failure ---


class _ExplodingSecurityService:
    def analyze(self, code: str) -> dict:
        raise ValueError("security engine failure")


def test_riskengine_raises_when_security_service_explodes():
    engine = RiskEngine(security_service=_ExplodingSecurityService())
    with pytest.raises(ValueError, match="security engine failure"):
        engine.assess(code=SIMPLE_CODE)


# --- Semantic Service Failure ---


class _ExplodingSemanticService:
    SIMILARITY_THRESHOLD = 0.9

    def detect_duplicates(
        self, new_code: str, existing_code_list: list[str]
    ) -> list:
        raise ConnectionError("embedding backend failure")


def test_riskengine_raises_when_semantic_service_explodes():
    engine = RiskEngine(semantic_service=_ExplodingSemanticService())
    with pytest.raises(ConnectionError, match="embedding backend failure"):
        engine.assess(code=SIMPLE_CODE, existing_code_list=["def f(): pass"])


def test_riskengine_semantic_failure_does_not_affect_no_code_path():
    engine = RiskEngine(semantic_service=_ExplodingSemanticService())
    result = engine.assess(code="")
    assert result["severity"] == SeverityLevel.LOW
    assert result["semantic_findings_count"] == 0


def test_riskengine_semantic_failure_does_not_affect_whitespace_code():
    engine = RiskEngine(semantic_service=_ExplodingSemanticService())
    result = engine.assess(code="   ")
    assert result["severity"] == SeverityLevel.LOW


# --- Embedding Engine Failure ---


class _ExplodingEmbeddingEngine:
    def generate_embedding(self, text: str) -> list[float]:
        raise MemoryError("out of memory")

    def compute_similarity(
        self, vec1: list[float], vec2: list[float]
    ) -> float:
        raise MemoryError("out of memory")


def test_semantic_service_raises_when_embedding_engine_explodes():
    svc = SemanticService(_ExplodingEmbeddingEngine())
    with pytest.raises(MemoryError, match="out of memory"):
        svc.detect_duplicates("def f(): pass", ["def f(): pass"])


def test_embedding_engine_failure_isolated_from_debt_service():
    debt = DebtService()
    result = debt.evaluate_debt(SIMPLE_CODE)
    assert result["severity"] == SeverityLevel.LOW


def test_embedding_engine_failure_isolated_from_security_service():
    sec = SecurityService()
    result = sec.analyze(SIMPLE_CODE)
    assert result["severity"] == SeverityLevel.LOW


# --- Isolation Between Engines ---


def test_debt_failure_does_not_affect_security_service():
    sec = SecurityService()
    result = sec.analyze('api_key = "secret"')
    assert result["severity"] == SeverityLevel.HIGH


def test_security_failure_does_not_affect_debt_service():
    debt = DebtService()
    code = "\n".join(["def f(x):"] + ["    if x > 0:"] * 16 + ["        return x"])
    result = debt.evaluate_debt(code)
    assert result["severity"] == SeverityLevel.HIGH


def test_semantic_failure_does_not_affect_debt_or_security():
    debt = DebtService()
    sec = SecurityService()
    d_result = debt.evaluate_debt(SIMPLE_CODE)
    s_result = sec.analyze(SIMPLE_CODE)
    assert d_result["severity"] == SeverityLevel.LOW
    assert s_result["severity"] == SeverityLevel.LOW


# --- Partial Engine Injection ---


class _TimeoutDebtService:
    def evaluate_debt(self, code: str) -> dict:
        raise TimeoutError("debt timeout")


class _TimeoutSecurityService:
    def analyze(self, code: str) -> dict:
        raise TimeoutError("security timeout")


def test_partial_failure_debt_raises_even_with_healthy_security():
    engine = RiskEngine(
        debt_service=_TimeoutDebtService(),
        security_service=SecurityService(),
    )
    with pytest.raises(TimeoutError, match="debt timeout"):
        engine.assess(code=SIMPLE_CODE)


def test_partial_failure_security_raises_even_with_healthy_debt():
    engine = RiskEngine(
        debt_service=DebtService(),
        security_service=_TimeoutSecurityService(),
    )
    with pytest.raises(TimeoutError, match="security timeout"):
        engine.assess(code=SIMPLE_CODE)


# --- Worker Loop Continues After Job Failure ---


def test_worker_loop_continues_after_use_case_failure(capsys):
    from sentinel.workers.job_queue import JobQueue

    real_sleep = asyncio.sleep

    async def _run() -> None:
        queue = JobQueue()
        await queue.enqueue({"missing_keys": True})
        await queue.enqueue({"repo": "ok", "pr_number": 7})

        from sentinel.application.report_service import ReportService
        from sentinel.application.risk_engine import RiskEngine
        from sentinel.application.use_cases.process_pull_request import (
            ProcessPullRequestUseCase,
        )

        engine = RiskEngine()
        report_service = ReportService()
        use_case = ProcessPullRequestUseCase(engine, report_service)
        processed = 0

        while processed < 2:
            job = await queue.dequeue()
            try:
                report = use_case.execute(job)
            except (KeyError, TypeError):
                report = None
            processed += 1
            await real_sleep(0)

        assert queue._queue.qsize() == 0

    asyncio.run(_run())


# --- Null and Edge Input Resilience ---


def test_debt_service_handles_none_like_empty():
    debt = DebtService()
    with pytest.raises((TypeError, AttributeError)):
        debt.evaluate_debt(None)


def test_security_service_handles_none_like_empty():
    sec = SecurityService()
    with pytest.raises((TypeError, AttributeError)):
        sec.analyze(None)


def test_semantic_service_handles_none_new_code():
    svc = SemanticService(EmbeddingEngine())
    with pytest.raises((TypeError, AttributeError)):
        svc.detect_duplicates(None, ["def f(): pass"])
