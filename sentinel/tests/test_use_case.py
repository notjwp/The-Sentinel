from sentinel.application.risk_engine import RiskEngine
from sentinel.application.report_service import ReportService
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase

def test_use_case_flow():
    engine = RiskEngine()
    reporter = ReportService()
    use_case = ProcessPullRequestUseCase(engine, reporter)

    job = {"repo": "test", "pr_number": 5}
    result = use_case.execute(job)

    assert "HIGH" in result