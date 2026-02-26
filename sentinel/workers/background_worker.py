import asyncio

from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine
from sentinel.workers.job_queue import JobQueue


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
            job = await self.queue.dequeue()
            report = use_case.execute(job)
            print(report, flush=True)
            await asyncio.sleep(2)
