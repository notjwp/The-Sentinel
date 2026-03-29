from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.application.risk_engine import RiskEngine
from sentinel.config.settings import get_settings
from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.llm.llm_service import LLMService
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


@router.post("/webhook")
async def webhook(
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
) -> dict[str, Any]:
    logger.info(
        "Received webhook payload for repo=%s pr_number=%s has_code=%s",
        payload.repo,
        payload.pr_number,
        bool(payload.code),
    )

    if payload.code:
        try:
            logger.info("Processing webhook synchronously for repo=%s pr_number=%s", payload.repo, payload.pr_number)
            orchestrator.llm_service = llm_service
            security_result = security_service.analyze(payload.code)
            findings = security_result.get("findings", [])
            findings = orchestrator.enrich_findings_with_llm(payload.code, findings)
            risk_result = risk_engine.assess(code=payload.code)
            risk = risk_result.get("severity", SeverityLevel.LOW)
            risk_value = risk.value if isinstance(risk, SeverityLevel) else str(risk)

            serialized_findings = [
                {
                    "type": finding.type,
                    "category": finding.category,
                    "owasp_category": finding.owasp_category,
                    "severity": finding.severity.value,
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
            }
        except Exception as exc:
            logger.exception("Synchronous webhook processing failed")
            return {
                "status": "error",
                "message": str(exc),
            }

    try:
        await orchestrator.enqueue_pull_request(payload.model_dump(exclude_none=True))
    except Exception:
        logger.exception("Failed to enqueue webhook payload")
        raise HTTPException(status_code=500, detail="Failed to queue webhook payload")
    return {"status": "queued"}
