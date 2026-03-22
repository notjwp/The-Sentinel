import time

from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.domain.entities.pull_request import PullRequest
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.monitoring.logger import get_logger

EXISTING_CODE_LIST: list[str] = [
    "def add(a, b): return a + b",
    "def subtract(a, b): return a - b",
]

logger = get_logger(__name__)
MAX_CODE_LENGTH = 2 * 1024 * 1024
SMALL_PAYLOAD_THRESHOLD = 20_000
TARGET_LATENCY_SECONDS = 0.1


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

        start_time = time.monotonic()
        try:
            assessment = self.risk_engine.assess_resilient(
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

        elapsed = time.monotonic() - start_time
        if len(code) <= SMALL_PAYLOAD_THRESHOLD and elapsed > TARGET_LATENCY_SECONDS:
            logger.warning(
                "Small payload PR analysis exceeded target: repo=%s pr=%s elapsed=%.4fs",
                pull_request.repo,
                pull_request.pr_number,
                elapsed,
            )

        report = self.report_service.generate_report(
            pull_request.pr_number,
            risk,
        )
        logger.info(
            "Completed PR analysis for repo=%s pr_number=%s severity=%s",
            pull_request.repo,
            pull_request.pr_number,
            risk.value,
        )
        return report