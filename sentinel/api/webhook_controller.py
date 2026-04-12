from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.application.report_service import ReportService
from sentinel.application.risk_engine import RiskEngine
from sentinel.config.settings import get_settings
from sentinel.domain.services.document_service import DocumentService
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.github.github_client import GitHubClient
from sentinel.infrastructure.llm.llm_service import LLMService
from sentinel.infrastructure.translation.translator import Translator
from sentinel.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str | None = Field(default=None, min_length=1, max_length=1024 * 1024)
    pr_number: int | None = None
    author: str | None = Field(default=None, max_length=256)
    files: list[str] | None = None
    code: str | None = None


def get_orchestrator() -> AuditOrchestrator:
    raise RuntimeError("AuditOrchestrator dependency is not configured")


def get_security_service() -> SecurityService:
    return SecurityService()


def get_risk_engine() -> RiskEngine:
    return RiskEngine()


def get_llm_service() -> LLMService:
    settings = get_settings()
    return LLMService(
        enable_llm=settings.ENABLE_LLM,
        max_calls=settings.LLM_MAX_CALLS,
        timeout=settings.LLM_TIMEOUT,
        api_key=settings.NVIDIA_API_KEY,
    )


def get_report_service() -> ReportService:
    return ReportService()


def get_document_service() -> DocumentService:
    return DocumentService()


def get_translator(
    llm_service: LLMService = Depends(get_llm_service),
) -> Translator:
    return Translator(llm_service=llm_service)


def get_github_client() -> GitHubClient | None:
    settings = get_settings()
    if not settings.ENABLE_GITHUB:
        return None

    return GitHubClient(
        app_id=settings.GITHUB_APP_ID,
        installation_id=settings.GITHUB_INSTALLATION_ID,
        private_key=settings.GITHUB_PRIVATE_KEY,
        api_base_url=settings.GITHUB_API_BASE_URL,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _extract_repo_name(raw_payload: dict[str, Any], fallback_repo: str | None) -> str | None:
    if fallback_repo:
        return fallback_repo

    repository = _as_dict(raw_payload.get("repository"))
    full_name = repository.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        return full_name.split("/", 1)[1]

    name = repository.get("name")
    if isinstance(name, str) and name.strip() != "":
        return name

    return None


def _extract_owner(raw_payload: dict[str, Any], fallback_repo: str | None) -> str | None:
    repository = _as_dict(raw_payload.get("repository"))
    owner_info = _as_dict(repository.get("owner"))
    owner = owner_info.get("login")
    if isinstance(owner, str) and owner.strip() != "":
        return owner

    full_name = repository.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        return full_name.split("/", 1)[0]

    if isinstance(fallback_repo, str) and "/" in fallback_repo:
        return fallback_repo.split("/", 1)[0]

    return None


def _extract_pr_number(raw_payload: dict[str, Any], fallback_pr_number: int | None) -> int | None:
    if isinstance(fallback_pr_number, int):
        return fallback_pr_number

    pull_request = _as_dict(raw_payload.get("pull_request"))
    number = pull_request.get("number")
    if isinstance(number, int):
        return number

    top_level_number = raw_payload.get("number")
    if isinstance(top_level_number, int):
        return top_level_number

    return None


def _extract_author(raw_payload: dict[str, Any], fallback_author: str | None) -> str | None:
    if isinstance(fallback_author, str) and fallback_author.strip() != "":
        return fallback_author

    pull_request = _as_dict(raw_payload.get("pull_request"))
    user = _as_dict(pull_request.get("user"))
    login = user.get("login")
    if isinstance(login, str) and login.strip() != "":
        return login

    sender = _as_dict(raw_payload.get("sender"))
    sender_login = sender.get("login")
    if isinstance(sender_login, str) and sender_login.strip() != "":
        return sender_login

    return None


def _extract_files(raw_payload: dict[str, Any], fallback_files: list[str] | None) -> list[str]:
    if isinstance(fallback_files, list) and fallback_files:
        return [file_name for file_name in fallback_files if isinstance(file_name, str)]

    raw_files = raw_payload.get("files")
    if not isinstance(raw_files, list):
        return []

    files: list[str] = []
    for item in raw_files:
        if isinstance(item, str):
            files.append(item)
            continue

        if isinstance(item, dict):
            file_name = item.get("filename") or item.get("path")
            if isinstance(file_name, str):
                files.append(file_name)

    return files


def _extract_file_contents(raw_payload: dict[str, Any]) -> dict[str, str]:
    raw_files = raw_payload.get("files")
    if not isinstance(raw_files, list):
        return {}

    file_contents: dict[str, str] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            continue

        file_name = item.get("filename") or item.get("path")
        if not isinstance(file_name, str):
            continue

        content = item.get("content")
        if isinstance(content, str):
            file_contents[file_name] = content
            continue

        patch = item.get("patch")
        if isinstance(patch, str):
            file_contents[file_name] = patch

    return file_contents


def _append_report_translations(
    report: str,
    translator: Translator,
    *,
    enable_translation: bool,
) -> str:
    if not enable_translation:
        return report

    translated_sections: list[str] = []
    for language in ("Hindi", "Kannada"):
        try:
            translated = translator.translate(report, language)
        except Exception:
            continue
        if translated.strip() == "":
            continue
        translated_sections.append(f"## {language} Version\n\n{translated}")

    if not translated_sections:
        return report

    return report + "\n\n" + "\n\n".join(translated_sections)


@router.post("/webhook")
async def webhook(
    request: Request,
    payload: WebhookPayload = Body(
        ...,
        examples={
            "phase2_demo": {
                "summary": "Synchronous vulnerability classification demo",
                "value": {
                    "repo": "demo",
                    "pr_number": 1,
                    "author": "user",
                    "code": "print('hello')",
                },
            }
        },
    ),
    orchestrator: AuditOrchestrator = Depends(get_orchestrator),
    security_service: SecurityService = Depends(get_security_service),
    risk_engine: RiskEngine = Depends(get_risk_engine),
    llm_service: LLMService = Depends(get_llm_service),
    report_service: ReportService = Depends(get_report_service),
    document_service: DocumentService = Depends(get_document_service),
    translator: Translator = Depends(get_translator),
    github_client: GitHubClient | None = Depends(get_github_client),
) -> dict[str, Any]:
    try:
        raw_payload = await request.json()
    except Exception:
        raw_payload = payload.model_dump(exclude_none=True)

    if not isinstance(raw_payload, dict):
        raw_payload = {}

    repo_name = _extract_repo_name(raw_payload, payload.repo)
    owner = _extract_owner(raw_payload, payload.repo)
    pr_number = _extract_pr_number(raw_payload, payload.pr_number)
    author = _extract_author(raw_payload, payload.author)
    if owner is None:
        owner = author
    files = _extract_files(raw_payload, payload.files)
    file_contents = _extract_file_contents(raw_payload)

    code = payload.code
    if not isinstance(code, str) or code.strip() == "":
        raw_code = raw_payload.get("code")
        code = raw_code if isinstance(raw_code, str) else None

    logger.info(
        "Received webhook payload for repo=%s pr_number=%s has_code=%s",
        repo_name,
        pr_number,
        bool(code),
    )

    if code:
        try:
            logger.info("Processing webhook synchronously for repo=%s pr_number=%s", repo_name, pr_number)
            orchestrator.llm_service = llm_service
            if hasattr(orchestrator, "report_service"):
                orchestrator.report_service = report_service
            if hasattr(orchestrator, "document_service"):
                orchestrator.document_service = document_service
            if hasattr(orchestrator, "translator"):
                orchestrator.translator = translator

            security_result = security_service.analyze(code)
            findings = security_result.get("findings", [])
            risk_result = risk_engine.assess(code=code)
            risk = risk_result.get("severity", SeverityLevel.LOW)
            risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk)

            if hasattr(orchestrator, "run_full_review"):
                findings, formatted_report = orchestrator.run_full_review(
                    code=code,
                    findings=findings,
                    risk=risk,
                    files=files,
                    file_contents=file_contents,
                    complexity=risk_result.get("complexity"),
                    maintainability=risk_result.get("maintainability"),
                    semantic_findings_count=risk_result.get("semantic_findings_count"),
                )
            else:
                findings = orchestrator.enrich_findings_with_llm(code, findings)
                settings = get_settings()
                if settings.ENABLE_DOC_REVIEW:
                    findings.extend(
                        document_service.analyze(
                            files,
                            file_contents=file_contents,
                            enable_llm_review=settings.ENABLE_LLM,
                            llm_reviewer=llm_service,
                        )
                    )
                formatted_report = report_service.format_report(
                    findings,
                    risk,
                    complexity=risk_result.get("complexity"),
                    maintainability=risk_result.get("maintainability"),
                    semantic_findings_count=risk_result.get("semantic_findings_count"),
                )
                formatted_report = _append_report_translations(
                    formatted_report,
                    translator,
                    enable_translation=settings.ENABLE_TRANSLATION,
                )

            settings = get_settings()
            if (
                settings.ENABLE_GITHUB
                and github_client is not None
                and owner
                and repo_name
                and pr_number is not None
            ):
                try:
                    posted = github_client.post_comment(owner, repo_name, pr_number, formatted_report)
                    logger.info(
                        "GitHub comment posted=%s owner=%s repo=%s pr=%s",
                        posted,
                        owner,
                        repo_name,
                        pr_number,
                    )
                except Exception:
                    logger.exception("GitHub comment posting failed; continuing without crash")
            else:
                logger.info("GitHub comment skipped for this webhook request")

            serialized_findings = [
                {
                    "type": finding.type,
                    "category": finding.category,
                    "owasp_category": finding.owasp_category,
                    "severity": finding.severity.value if isinstance(finding.severity, SeverityLevel) else str(finding.severity),
                    "description": finding.description,
                    "file": finding.file or "unknown",
                    "line": finding.line if finding.line is not None else 1,
                    "recommendation": finding.recommendation,
                    "explanation": finding.explanation,
                    "fix_suggestion": finding.fix_suggestion,
                }
                for finding in findings
            ]

            logger.info(
                "Synchronous processing completed findings=%s risk=%s",
                len(serialized_findings),
                risk_value,
            )
            return {
                "status": "processed",
                "risk": risk_value,
                "findings": serialized_findings,
                "report": formatted_report,
            }
        except Exception as exc:
            logger.exception("Synchronous webhook processing failed")
            return {
                "status": "error",
                "message": str(exc),
            }

    queued_payload = payload.model_dump(exclude_none=True)
    if not queued_payload:
        if repo_name is not None:
            queued_payload["repo"] = repo_name
        if pr_number is not None:
            queued_payload["pr_number"] = pr_number
        if author is not None:
            queued_payload["author"] = author
        if files:
            queued_payload["files"] = files

    try:
        await orchestrator.enqueue_pull_request(queued_payload)
    except Exception:
        logger.exception("Failed to enqueue webhook payload")
        raise HTTPException(status_code=500, detail="Failed to queue webhook payload")
    return {"status": "queued"}
