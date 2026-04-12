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


class DocumentServicePort(Protocol):
    def analyze(
        self,
        files: list[str],
        file_contents: dict[str, str] | None = None,
        *,
        enable_llm_review: bool = False,
        llm_reviewer: object | None = None,
    ) -> list[Finding]: ...


class ReportServicePort(Protocol):
    def format_report(
        self,
        findings: list[Finding],
        risk: SeverityLevel | str,
        *,
        complexity: int | None = None,
        maintainability: float | None = None,
        semantic_findings_count: int | None = None,
    ) -> str: ...


class TranslatorPort(Protocol):
    def translate(self, text: str, language: str) -> str: ...


class AuditOrchestrator:
    def __init__(
        self,
        queue: JobQueue,
        llm_service: LLMServicePort | None = None,
        report_service: ReportServicePort | None = None,
        translator: TranslatorPort | None = None,
        document_service: DocumentServicePort | None = None,
    ) -> None:
        self.queue = queue
        self.llm_service = llm_service
        self.report_service = report_service
        self.translator = translator
        self.document_service = document_service

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
            severity_name = finding.severity.value if isinstance(finding.severity, SeverityLevel) else str(finding.severity).upper()
            is_security_finding = finding.type == "security"
            is_meaningful_medium = severity_name != SeverityLevel.MEDIUM.value or bool(finding.recommendation)
            should_enrich = is_security_finding and severity_name != SeverityLevel.LOW.value and is_meaningful_medium

            if not should_enrich:
                enriched.append(finding)
                continue

            logger.info("Calling LLM for finding severity=%s", severity_name)
            issue_text = finding.description or finding.rule
            fix = self.llm_service.generate_fix_safe(
                code,
                issue_text,
                severity=severity_name,
            )
            explanation = self.llm_service.explain_issue_safe(
                code,
                issue_text,
                severity=severity_name,
            )

            logger.info(
                "LLM invoked for finding rule=%s severity=%s",
                finding.rule,
                severity_name,
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

        logger.info("LLM calls made in request: %s", getattr(self.llm_service, "call_count", "unknown"))
        return enriched

    def collect_document_findings(
        self,
        files: list[str] | None,
        *,
        file_contents: dict[str, str] | None = None,
    ) -> list[Finding]:
        settings = get_settings()
        if not settings.ENABLE_DOC_REVIEW:
            return []
        if self.document_service is None:
            return []
        if not files:
            return []

        try:
            findings = self.document_service.analyze(
                files,
                file_contents=file_contents,
                enable_llm_review=settings.ENABLE_LLM,
                llm_reviewer=self.llm_service,
            )
        except Exception:
            logger.exception("Document review failed; continuing without documentation findings")
            return []

        logger.info("Document review produced %s findings", len(findings))
        return findings

    def build_report(
        self,
        findings: list[Finding],
        risk: SeverityLevel | str,
        *,
        complexity: int | None = None,
        maintainability: float | None = None,
        semantic_findings_count: int | None = None,
    ) -> str:
        if self.report_service is None:
            risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk).upper()
            return f"Sentinel AI Code Review\n\nRisk Score: {risk_value}"

        try:
            return self.report_service.format_report(
                findings,
                risk,
                complexity=complexity,
                maintainability=maintainability,
                semantic_findings_count=semantic_findings_count,
            )
        except Exception:
            logger.exception("Report formatting failed; returning safe fallback")
            risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk).upper()
            return f"Sentinel AI Code Review\n\nRisk Score: {risk_value}"

    def append_translations(self, report: str, languages: list[str] | None = None) -> str:
        settings = get_settings()
        if not settings.ENABLE_TRANSLATION or self.translator is None:
            return report

        selected_languages = languages or ["Hindi", "Kannada"]
        translated_sections: list[str] = []

        for language in selected_languages:
            try:
                translated = self.translator.translate(report, language)
            except Exception:
                logger.exception("Translation failed for language=%s; skipping", language)
                continue

            if translated.strip() == "":
                continue
            translated_sections.append(f"## {language} Version\n\n{translated}")

        if not translated_sections:
            return report

        return report + "\n\n" + "\n\n".join(translated_sections)

    def run_full_review(
        self,
        *,
        code: str,
        findings: list[Finding],
        risk: SeverityLevel | str,
        files: list[str] | None = None,
        file_contents: dict[str, str] | None = None,
        complexity: int | None = None,
        maintainability: float | None = None,
        semantic_findings_count: int | None = None,
    ) -> tuple[list[Finding], str]:
        security_findings = self.enrich_findings_with_llm(code, findings)
        document_findings = self.collect_document_findings(files, file_contents=file_contents)
        all_findings = [*security_findings, *document_findings]

        report = self.build_report(
            all_findings,
            risk,
            complexity=complexity,
            maintainability=maintainability,
            semantic_findings_count=semantic_findings_count,
        )
        report = self.append_translations(report)
        return all_findings, report
