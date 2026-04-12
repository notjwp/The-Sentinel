from typing import Protocol

from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class DocumentLLMReviewer(Protocol):
    def explain_issue_safe(
        self,
        code: str,
        issue: str,
        *,
        severity: SeverityLevel | str | None = None,
    ) -> str: ...


class DocumentService:
    DOC_EXTENSIONS = (".md", ".txt")

    @staticmethod
    def _is_document_file(file_name: str) -> bool:
        normalized = file_name.strip().lower()
        return normalized.endswith(DocumentService.DOC_EXTENSIONS)

    @staticmethod
    def _content_for(file_name: str, file_contents: dict[str, str] | None) -> str | None:
        if not file_contents:
            return None

        if file_name in file_contents:
            return file_contents[file_name]

        lowered_name = file_name.lower()
        for key, value in file_contents.items():
            if key.lower().endswith(lowered_name):
                return value

        return None

    @staticmethod
    def _doc_finding(
        *,
        rule: str,
        severity: SeverityLevel,
        description: str,
        recommendation: str,
        file_name: str,
        explanation: str | None = None,
    ) -> Finding:
        return Finding(
            rule=rule,
            match=file_name,
            severity=severity,
            finding_type="documentation",
            category="Documentation",
            owasp_category="N/A",
            description=description,
            file=file_name,
            line=1,
            recommendation=recommendation,
            explanation=explanation,
        )

    def analyze(
        self,
        files: list[str],
        file_contents: dict[str, str] | None = None,
        *,
        enable_llm_review: bool = False,
        llm_reviewer: DocumentLLMReviewer | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []

        for file_name in files:
            if not isinstance(file_name, str) or not self._is_document_file(file_name):
                continue

            content = self._content_for(file_name, file_contents)
            if content is None:
                continue
            content_lower = content.lower()
            normalized_file_name = file_name.strip().lower()

            if normalized_file_name.startswith("readme") and content.strip() == "":
                findings.append(
                    self._doc_finding(
                        rule="empty_readme",
                        severity=SeverityLevel.MEDIUM,
                        description="README file is empty.",
                        recommendation="Add installation, usage, and architecture notes to README.",
                        file_name=file_name,
                    )
                )

            if content.strip() != "":
                if "install" not in content_lower and "pip install" not in content_lower:
                    findings.append(
                        self._doc_finding(
                            rule="missing_installation",
                            severity=SeverityLevel.MEDIUM,
                            description="Documentation is missing installation instructions.",
                            recommendation="Add a clear installation section with setup commands.",
                            file_name=file_name,
                        )
                    )

                if (
                    "usage" not in content_lower
                    and "example" not in content_lower
                    and "how to run" not in content_lower
                ):
                    findings.append(
                        self._doc_finding(
                            rule="missing_usage",
                            severity=SeverityLevel.MEDIUM,
                            description="Documentation is missing usage guidance.",
                            recommendation="Add usage examples and common execution workflows.",
                            file_name=file_name,
                        )
                    )

            if enable_llm_review and llm_reviewer is not None and content.strip() != "":
                review = llm_reviewer.explain_issue_safe(
                    content,
                    "Review this documentation for clarity and completeness. Keep feedback concise.",
                    severity=SeverityLevel.MEDIUM,
                )
                if review and review != "Explanation unavailable":
                    findings.append(
                        self._doc_finding(
                            rule="doc_clarity_review",
                            severity=SeverityLevel.LOW,
                            description="AI review identified potential documentation clarity improvements.",
                            recommendation="Address clarity and completeness gaps highlighted by the review.",
                            file_name=file_name,
                            explanation=review,
                        )
                    )

        return findings
