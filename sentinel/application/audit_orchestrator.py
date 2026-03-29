from dataclasses import replace
from typing import Protocol

from sentinel.config.settings import get_settings
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.workers.job_queue import JobQueue
from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


class LLMServicePort(Protocol):
    def reset_budget(self) -> None: ...

    def generate_fix_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str: ...

    def explain_issue_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str: ...


class AuditOrchestrator:
    def __init__(self, queue: JobQueue, llm_service: LLMServicePort | None = None) -> None:
        self.queue = queue
        self.llm_service = llm_service

    async def enqueue_pull_request(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")

        repo = payload.get("repo")
        pr_number = payload.get("pr_number")

        job = {
            "repo": repo,
            "pr_number": pr_number,
        }

        if "author" in payload:
            job["author"] = payload["author"]
        if "files" in payload:
            job["files"] = payload["files"]
        if "code" in payload:
            job["code"] = payload["code"]

        await self.queue.enqueue(job)
        logger.info("Queued PR audit job for repo=%s pr_number=%s", repo, pr_number)

    def enrich_findings_with_llm(self, code: str, findings: list[Finding]) -> list[Finding]:
        settings = get_settings()
        if not findings:
            return []
        if not settings.ENABLE_LLM or self.llm_service is None:
            return findings

        self.llm_service.reset_budget()
        logger.info("LLM enrichment enabled for %s findings", len(findings))

        enriched: list[Finding] = []
        for finding in findings:
            if finding.severity not in {SeverityLevel.HIGH, SeverityLevel.CRITICAL}:
                enriched.append(finding)
                continue

            issue_text = finding.description or finding.rule
            fix = self.llm_service.generate_fix_safe(
                code,
                issue_text,
                severity=finding.severity,
            )
            explanation = self.llm_service.explain_issue_safe(
                code,
                issue_text,
                severity=finding.severity,
            )

            logger.info(
                "LLM invoked for finding rule=%s severity=%s",
                finding.rule,
                finding.severity.value,
            )
            if fix == "Fix suggestion unavailable" or explanation == "Explanation unavailable":
                logger.warning(
                    "LLM fallback used for finding rule=%s",
                    finding.rule,
                )

            enriched.append(
                replace(
                    finding,
                    fix_suggestion=fix,
                    explanation=explanation,
                )
            )

        logger.info("LLM calls made in request: %s", getattr(self.llm_service, "calls_made", "unknown"))
        return enriched
