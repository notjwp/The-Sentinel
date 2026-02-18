from typing import Any

from fastapi import APIRouter, Body, Depends

from sentinel.application.audit_orchestrator import AuditOrchestrator

router = APIRouter()


def get_orchestrator() -> AuditOrchestrator:
    raise RuntimeError("AuditOrchestrator dependency is not configured")


@router.post("/webhook")
async def webhook(
    payload: dict[str, Any] = Body(...),
    orchestrator: AuditOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    await orchestrator.enqueue_pull_request(payload)
    return {"status": "queued"}
