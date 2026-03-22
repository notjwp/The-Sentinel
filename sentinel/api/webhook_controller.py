from fastapi import APIRouter, Body, Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from sentinel.application.audit_orchestrator import AuditOrchestrator
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


@router.post("/webhook")
async def webhook(
    payload: WebhookPayload = Body(...),
    orchestrator: AuditOrchestrator = Depends(get_orchestrator),
) -> dict[str, str]:
    try:
        await orchestrator.enqueue_pull_request(payload.model_dump(exclude_none=True))
    except Exception:
        logger.exception("Failed to enqueue webhook payload")
        raise HTTPException(status_code=500, detail="Failed to queue webhook payload")
    return {"status": "queued"}
