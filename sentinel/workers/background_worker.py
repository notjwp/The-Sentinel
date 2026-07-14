import asyncio
import sys
import time

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.application.risk_engine import RiskEngine
from sentinel.config.settings import Settings, get_settings
from sentinel.domain.entities.pull_request import PullRequest
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.github.github_client import GitHubClient
from sentinel.infrastructure.llm.llm_service import LLMService
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


def _build_github_client(settings: Settings) -> GitHubClient | None:
    """Build a GitHub client for the worker, or None when GitHub is disabled.

    Kept at module scope (mirrors webhook_controller.get_github_client) so tests can
    monkeypatch ``bw_module._build_github_client`` to inject a fake.
    """
    if not settings.ENABLE_GITHUB:
        return None
    return GitHubClient(
        app_id=settings.GITHUB_APP_ID,
        installation_id=settings.GITHUB_INSTALLATION_ID,
        private_key=settings.GITHUB_PRIVATE_KEY,
        api_base_url=settings.GITHUB_API_BASE_URL,
    )


def _build_llm_service(settings: Settings) -> LLMService:
    """Build the LLM service for the worker (mirrors webhook_controller.get_llm_service).

    Kept at module scope so tests can monkeypatch ``bw_module._build_llm_service``.
    With no LLM_API_KEY, ``enable_llm`` is False and the service returns fallback
    strings without any network call — so wiring it in is safe with creds absent.
    Base URL / model / key come from env (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY),
    so the provider is swappable without a code change.
    """
    llm_enabled = settings.ENABLE_LLM and bool(settings.LLM_API_KEY)
    return LLMService(
        enable_llm=llm_enabled,
        max_calls=settings.LLM_MAX_CALLS,
        timeout=settings.LLM_TIMEOUT,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
    )


def _safe_assessment() -> dict:
    """Assessment-shaped defaults, used when risk analysis fails outright."""
    return {
        "severity": SeverityLevel.LOW,
        "complexity": 1,
        "maintainability": 100.0,
        "security_findings_count": 0,
        "security": {"findings": [], "severity": SeverityLevel.LOW},
        "semantic_findings_count": 0,
        "semantic": {"findings": [], "severity": SeverityLevel.LOW},
    }


class BackgroundWorker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    @staticmethod
    def _format_risk_line(pr_number: object, risk: object) -> str:
        risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk).upper()
        return f"PR #{pr_number} Risk: {risk_value}"

    @staticmethod
    def _assess(job: dict, risk_engine: RiskEngine) -> tuple[PullRequest, dict]:
        """Read/coerce/truncate the job's code and run the resilient assessment.

        Returns the ``PullRequest`` identity plus the full assessment dict (safe
        defaults on unexpected failure). Shared by ``process_job`` (one-liner) and
        ``start`` (report) so the code is assessed exactly once per job.
        """
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
        except Exception:
            logger.exception(
                "Risk assessment failed unexpectedly for repo=%s pr_number=%s; defaulting to LOW",
                pull_request.repo,
                pull_request.pr_number,
            )
            assessment = _safe_assessment()

        assessment_elapsed = time.monotonic() - assessment_start
        if len(code) <= SMALL_PAYLOAD_THRESHOLD and assessment_elapsed > TARGET_LATENCY_SECONDS:
            logger.warning(
                "Small payload PR analysis exceeded target: repo=%s pr=%s elapsed=%.4fs",
                pull_request.repo,
                pull_request.pr_number,
                assessment_elapsed,
            )

        return pull_request, assessment

    @staticmethod
    def process_job(job: dict, risk_engine: RiskEngine) -> str:
        pull_request, assessment = BackgroundWorker._assess(job, risk_engine)
        risk = assessment["severity"]
        logger.info(
            "Completed PR analysis for repo=%s pr_number=%s severity=%s",
            pull_request.repo,
            pull_request.pr_number,
            risk.value if isinstance(risk, SeverityLevel) else risk,
        )
        return BackgroundWorker._format_risk_line(pull_request.pr_number, risk)

    @staticmethod
    def _identity(job: dict) -> tuple[object, object, object]:
        """Extract (owner, bare_repo_name, pr_number) for GitHub API calls.

        ``repo`` may arrive as ``"owner/name"`` or a bare ``"name"``; owner is a
        separate job key. Returns the bare repo name so the API URL is well-formed.
        """
        owner = job.get("owner")
        repo_raw = job.get("repo")
        if isinstance(repo_raw, str) and "/" in repo_raw:
            repo_name: object = repo_raw.split("/", 1)[1]
        else:
            repo_name = repo_raw
        return owner, repo_name, job.get("pr_number")

    @staticmethod
    def _fetch_pr_code(job: dict, github_client: GitHubClient | None) -> None:
        """Populate ``job['code']`` from the PR's diff when possible. Failure-safe."""
        if github_client is None or job.get("code"):
            return
        owner, repo_name, pr_number = BackgroundWorker._identity(job)
        if not owner or not repo_name or pr_number is None:
            return
        try:
            fetched = github_client.get_pull_request_code(owner, repo_name, pr_number)
        except Exception:
            logger.exception("Failed to fetch PR code for repo=%s pr=%s", repo_name, pr_number)
            return
        if fetched and fetched.strip():
            job["code"] = fetched
            logger.info(
                "Fetched %s chars of PR code for repo=%s pr=%s",
                len(fetched),
                repo_name,
                pr_number,
            )

    @staticmethod
    def _post_review(
        job: dict,
        assessment: dict,
        orchestrator: AuditOrchestrator,
        github_client: GitHubClient | None,
    ) -> None:
        """Build a structured report from the assessment and post it. Failure-safe."""
        if github_client is None:
            return
        owner, repo_name, pr_number = BackgroundWorker._identity(job)
        if not owner or not repo_name or pr_number is None:
            return
        try:
            security = assessment.get("security", {})
            findings = security.get("findings", []) if isinstance(security, dict) else []
            enriched = orchestrator.enrich_findings_with_llm(job.get("code", ""), findings)
            report = orchestrator.build_report(
                enriched,
                assessment["severity"],
                complexity=assessment.get("complexity"),
                maintainability=assessment.get("maintainability"),
                semantic_findings_count=assessment.get("semantic_findings_count"),
            )
            posted = github_client.upsert_comment(owner, repo_name, pr_number, report)
            logger.info(
                "Worker posted review comment=%s repo=%s pr=%s", posted, repo_name, pr_number
            )
        except Exception:
            logger.exception("Failed to post PR review for repo=%s pr=%s", repo_name, pr_number)

    async def start(self) -> None:
        embedding_engine = EmbeddingEngine()
        semantic_service = SemanticService(embedding_engine)
        risk_engine = RiskEngine(semantic_service=semantic_service)

        settings = get_settings()
        github_client = _build_github_client(settings)
        llm_service = _build_llm_service(settings)
        orchestrator = AuditOrchestrator(self.queue, llm_service=llm_service)

        while True:
            try:
                job = await self.queue.dequeue()
                start_time = time.monotonic()

                self._fetch_pr_code(job, github_client)

                pull_request, assessment = self._assess(job, risk_engine)
                report_line = self._format_risk_line(
                    pull_request.pr_number, assessment["severity"]
                )

                logger.info("%s", report_line)
                sys.stdout.write(f"{report_line}\n")
                sys.stdout.flush()

                self._post_review(job, assessment, orchestrator, github_client)

                elapsed = time.monotonic() - start_time
                logger.info("Processed job in %.4fs", elapsed)
            except Exception:
                logger.exception("Worker failed to process job; continuing")
            await asyncio.sleep(2)
