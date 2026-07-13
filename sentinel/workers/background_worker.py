import asyncio
import sys
import time

from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.entities.pull_request import PullRequest
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine
from sentinel.monitoring.logger import get_logger
from sentinel.workers.job_queue import JobQueue

logger = get_logger(__name__)

EXISTING_CODE_LIST: list[str] = [
    "def add(a, b): return a + b",
    "def subtract(a, b): return a - b",
]

MAX_CODE_LENGTH = 2 * 1024 * 1024
SMALL_PAYLOAD_THRESHOLD = 20_000
TARGET_LATENCY_SECONDS = 0.1

logger = get_logger(__name__)


class BackgroundWorker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    @staticmethod
    def process_job(job: dict, risk_engine: RiskEngine) -> str:
        pull_request = PullRequest(
            repo=job.get("repo", "unknown"),
            pr_number=job.get("pr_number", 0),
        )

        code = job.get("code", "")
        if code is None:
            code = ""
        elif not isinstance(code, str):
            code = str(code)

        if len(code) > MAX_CODE_LENGTH:
            logger.warning(
                "Code payload too large for repo=%s pr_number=%s; truncating from %s chars",
                pull_request.repo,
                pull_request.pr_number,
                len(code),
            )
            code = code[:MAX_CODE_LENGTH]

        assessment_start = time.monotonic()
        try:
            assessment = risk_engine.assess_resilient(
                code=code,
                existing_code_list=EXISTING_CODE_LIST,
                warn_threshold_seconds=TARGET_LATENCY_SECONDS,
            )
            risk = assessment["severity"]
        except Exception:
            logger.exception(
                "Risk assessment failed unexpectedly for repo=%s pr_number=%s; defaulting to LOW",
                pull_request.repo,
                pull_request.pr_number,
            )
            risk = SeverityLevel.LOW

        assessment_elapsed = time.monotonic() - assessment_start
        if len(code) <= SMALL_PAYLOAD_THRESHOLD and assessment_elapsed > TARGET_LATENCY_SECONDS:
            logger.warning(
                "Small payload PR analysis exceeded target: repo=%s pr=%s elapsed=%.4fs",
                pull_request.repo,
                pull_request.pr_number,
                assessment_elapsed,
            )

        risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk).upper()
        report = f"PR #{pull_request.pr_number} Risk: {risk_value}"

        logger.info(
            "Completed PR analysis for repo=%s pr_number=%s severity=%s",
            pull_request.repo,
            pull_request.pr_number,
            risk_value,
        )
        return report

    async def start(self) -> None:
        embedding_engine = EmbeddingEngine()
        semantic_service = SemanticService(embedding_engine)
        risk_engine = RiskEngine(semantic_service=semantic_service)

        while True:
            try:
                job = await self.queue.dequeue()
                start_time = time.monotonic()
                
                report = self.process_job(job, risk_engine)
                
                logger.info("%s", report)
                sys.stdout.write(f"{report}\n")
                sys.stdout.flush()
                elapsed = time.monotonic() - start_time
                logger.info("Processed job in %.4fs", elapsed)
            except Exception:
                logger.exception("Worker failed to process job; continuing")
            await asyncio.sleep(2)
