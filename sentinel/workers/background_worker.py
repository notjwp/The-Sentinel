import asyncio

from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase
from sentinel.workers.job_queue import JobQueue


class BackgroundWorker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def start(self) -> None:
        risk_engine = RiskEngine()
        report_service = ReportService()
        use_case = ProcessPullRequestUseCase(risk_engine, report_service)

        while True:
            job = await self.queue.dequeue()
            report = use_case.execute(job)
            print(report, flush=True)
            await asyncio.sleep(2)
