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

    def generate_pr_audit(self, code: str, findings: list) -> dict[int, dict[str, str]]: ...


class DocumentServicePort(Protocol):
    def analyze(
        self,
        files: list[str],
        file_contents: dict[str, str] | None = None,
        *,
        enable_llm_review: bool = False,
        llm_reviewer: object | None = None,
    ) -> list[Finding]: ...

    def analyze_code(
        self,
        code: str,
        *,
        source_label: str = "inline",
    ) -> list[Finding]: ...


class AuditOrchestrator:
    def __init__(
        self,
        queue: JobQueue,
        llm_service: LLMServicePort | None = None,
        document_service: DocumentServicePort | None = None,
    ) -> None:
        self.queue = queue
        self.llm_service = llm_service
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

        audit_response = self.llm_service.generate_pr_audit(code=code, findings=findings)

        enriched: list[Finding] = []
        for finding in findings:
            fid = id(finding)
            if fid in audit_response:
                enriched.append(
                    replace(
                        finding,
                        explanation=audit_response[fid]["explanation"],
                        fix_suggestion=audit_response[fid]["fix"],
                    )
                )
            else:
                enriched.append(finding)

        logger.info("LLM calls made in request: %s", getattr(self.llm_service, "call_count", "unknown"))
        return enriched

    def collect_document_findings(
        self,
        files: list[str] | None,
        *,
        file_contents: dict[str, str] | None = None,
        code: str | None = None,
    ) -> list[Finding]:
        settings = get_settings()
        if not settings.ENABLE_DOC_REVIEW:
            return []
        if self.document_service is None:
            return []

        findings: list[Finding] = []

        if files:
            try:
                findings.extend(
                    self.document_service.analyze(
                        files,
                        file_contents=file_contents,
                        enable_llm_review=False,
                        llm_reviewer=None,
                    )
                )
            except Exception:
                logger.exception("Document file review failed; continuing")

        if code and isinstance(code, str) and code.strip():
            try:
                findings.extend(
                    self.document_service.analyze_code(code)
                )
            except Exception:
                logger.exception("Document code review failed; continuing")

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
        risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk).upper()
        try:
            lines: list[str] = [
                "# Sentinel AI Code Review",
                "",
                f"## Risk Score: {risk_value}",
                "",
                "## Security Issues",
            ]
            security_findings = [finding for finding in findings if finding.type == "security"]
            if security_findings:
                for finding in security_findings:
                    lines.append(
                        f"- {finding.description or finding.rule} (Severity: {finding.severity.value})"
                    )
            else:
                lines.append("- No security issues detected.")

            lines.extend(["", "## Explanation"])
            explanations = [finding.explanation for finding in security_findings if finding.explanation]
            if explanations:
                for explanation in explanations:
                    lines.append(f"- {explanation}")
            else:
                lines.append("- No AI explanation available.")

            lines.extend(["", "## Fix Suggestion"])
            fixes = [finding.fix_suggestion for finding in security_findings if finding.fix_suggestion]
            if fixes:
                for fix in fixes:
                    lines.extend(["```python", fix, "```"])
            else:
                lines.append("- No fix suggestion available.")

            documentation_findings = [finding for finding in findings if finding.type == "documentation"]
            if documentation_findings:
                lines.extend(["", "## Documentation Issues"])
                for finding in documentation_findings:
                    lines.append(
                        f"- {finding.description or finding.rule} (Severity: {finding.severity.value})"
                    )

            lines.extend(["", "## Technical Debt"])
            if complexity is None and maintainability is None:
                lines.append("- Technical debt metrics unavailable.")
            else:
                if complexity is not None:
                    lines.append(f"- Complexity: {complexity}")
                if maintainability is not None:
                    lines.append(f"- Maintainability: {maintainability:.2f}")

            lines.extend(["", "## Semantic Similarity"])
            if semantic_findings_count is None:
                lines.append("- Semantic similarity metrics unavailable.")
            else:
                lines.append(f"- Similar findings detected: {semantic_findings_count}")

            return "\n".join(lines).strip()
        except Exception:
            logger.exception("Report formatting failed; returning safe fallback")
            return f"Sentinel AI Code Review\n\nRisk Score: {risk_value}"

    def append_translations(self, report: str, languages: list[str] | None = None) -> str:
        settings = get_settings()
        if not settings.ENABLE_TRANSLATION or self.llm_service is None:
            return report

        SUPPORTED_LANGUAGES = {
            "hindi": "Hindi",
            "kannada": "Kannada",
            "tamil": "Tamil",
            "telugu": "Telugu",
        }

        selected_languages = languages or ["Hindi", "Kannada"]
        translated_sections: list[str] = []

        for language in selected_languages:
            normalized_language = language.strip().lower()
            target_language = SUPPORTED_LANGUAGES.get(normalized_language)
            if target_language is None:
                continue

            try:
                instruction = (
                    "Translate the provided markdown report into "
                    f"{target_language}. Preserve headings, bullets, and code blocks."
                )
                if hasattr(self.llm_service, "explain_issue_safe"):
                    translated = self.llm_service.explain_issue_safe(
                        report,
                        instruction,
                        severity="HIGH",
                    )
                else:
                    return report
            except Exception:
                logger.exception("Translation failed for language=%s; skipping", language)
                continue

            fallback = getattr(
                self.llm_service,
                "FALLBACK_EXPLANATION",
                "Potential security issue detected. Review code manually.",
            )
            if not translated or translated == fallback or translated.strip() == "":
                continue

            translated_sections.append(f"## {target_language} Version\n\n{translated.strip()}")

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
        document_findings = self.collect_document_findings(files, file_contents=file_contents, code=code)
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
