"""
Context Update Checklist:
- Update PROJECT_CONTEXT.md after meaningful code/config/dependency changes.
- Add a short Working Log entry with date, summary, files changed, and follow-ups.
- Keep run/test command changes documented.
"""

import asyncio

from fastapi import FastAPI
from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.health_controller import router as health_router
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue

app = FastAPI(title="The Sentinel")

job_queue = JobQueue()
audit_orchestrator = AuditOrchestrator(job_queue)
background_worker = BackgroundWorker(job_queue)

app.dependency_overrides[get_orchestrator] = lambda: audit_orchestrator

app.include_router(webhook_router)
app.include_router(health_router)


@app.on_event("startup")
async def startup_event() -> None:
    print("Starting background worker...", flush=True)
    app.state.worker_task = asyncio.create_task(background_worker.start())

@app.get("/")
def root():
    return {"message": "The Sentinel is running"}
