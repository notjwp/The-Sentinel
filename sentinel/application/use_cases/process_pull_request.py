from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.entities.pull_request import PullRequest

EXISTING_CODE_LIST: list[str] = [
    "def add(a, b): return a + b",
    "def subtract(a, b): return a - b",
]


class ProcessPullRequestUseCase:
    def __init__(self, risk_engine: RiskEngine, report_service: ReportService) -> None:
        self.risk_engine = risk_engine
        self.report_service = report_service

    def execute(self, job: dict) -> str:
        pull_request = PullRequest(
            repo=job["repo"],
            pr_number=job["pr_number"],
        )
        code = job.get("code", "")
        risk = self.risk_engine.calculate_risk(
            pull_request.pr_number,
            code=code,
            existing_code_list=EXISTING_CODE_LIST,
        )
        report = self.report_service.generate_report(
            pull_request.pr_number,
            risk,
        )
        return report