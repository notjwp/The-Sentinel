"""
Context Update Checklist:
- Update PROJECT_CONTEXT.md after meaningful code/config/dependency changes.
- Add a short Working Log entry with date, summary, files changed, and follow-ups.
- Keep run/test command changes documented.
- Keep .env usage and env-variable expectations documented.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.health_controller import router as health_router
from sentinel.monitoring.logger import get_logger
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue

logger = get_logger(__name__)

job_queue = JobQueue()
audit_orchestrator = AuditOrchestrator(job_queue)
background_worker = BackgroundWorker(job_queue)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting background worker")
    app.state.worker_task = asyncio.create_task(background_worker.start())
    try:
        yield
    finally:
        worker_task = getattr(app.state, "worker_task", None)
        if worker_task:
            worker_task.cancel()


app = FastAPI(title="The Sentinel", lifespan=lifespan)

app.dependency_overrides[get_orchestrator] = lambda: audit_orchestrator

app.include_router(webhook_router)
app.include_router(health_router)


@app.get("/")
def root():
    return {"message": "The Sentinel is running"}
