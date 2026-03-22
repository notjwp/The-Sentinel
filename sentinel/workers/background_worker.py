import asyncio
import sys
import time

from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine
from sentinel.monitoring.logger import get_logger
from sentinel.workers.job_queue import JobQueue

logger = get_logger(__name__)


class BackgroundWorker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def start(self) -> None:
        embedding_engine = EmbeddingEngine()
        semantic_service = SemanticService(embedding_engine)
        risk_engine = RiskEngine(semantic_service=semantic_service)
        report_service = ReportService()
        use_case = ProcessPullRequestUseCase(risk_engine, report_service)

        while True:
            try:
                job = await self.queue.dequeue()
                start_time = time.monotonic()
                report = use_case.execute(job)
                logger.info("%s", report)
                sys.stdout.write(f"{report}\n")
                sys.stdout.flush()
                elapsed = time.monotonic() - start_time
                logger.info("Processed job in %.4fs", elapsed)
            except Exception:
                logger.exception("Worker failed to process job; continuing")
            await asyncio.sleep(2)
