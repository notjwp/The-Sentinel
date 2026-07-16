"""The Sentinel — FastAPI composition root.

Wires the job queue (in-memory, or Redis when REDIS_URL is set), the audit
orchestrator, the background worker (started via lifespan), and the routers
(webhook, health, metrics).
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sentinel.api.health_controller import router as health_router
from sentinel.api.metrics_controller import router as metrics_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router
from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.config.settings import get_settings
from sentinel.infrastructure.redis.redis_job_queue import RedisJobQueue
from sentinel.monitoring.logger import get_logger
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue

logger = get_logger(__name__)

# Composition root: REDIS_URL set -> durable Redis queue; unset -> in-memory.
_settings = get_settings()
job_queue = RedisJobQueue(_settings.REDIS_URL) if _settings.REDIS_URL else JobQueue()
audit_orchestrator = AuditOrchestrator(job_queue)
background_worker = BackgroundWorker(job_queue)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting background worker")
    app.state.worker_task = asyncio.create_task(background_worker.start())
    settings = get_settings()
    logger.info(
        "Queue/dedup backend: %s",
        "redis (durable)" if settings.REDIS_URL else "in-memory (non-durable)",
    )
    if settings.ENABLE_GITHUB and not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning(
            "ENABLE_GITHUB is on but GITHUB_WEBHOOK_SECRET is unset — "
            "webhook signature verification is DISABLED. Set it before public exposure."
        )
    try:
        yield
    finally:
        worker_task = getattr(app.state, "worker_task", None)
        if worker_task:
            worker_task.cancel()


app = FastAPI(title="The Sentinel", lifespan=lifespan)

app.dependency_overrides[get_orchestrator] = lambda: audit_orchestrator
app.state.job_queue = job_queue  # /metrics reads live queue depth from here

app.include_router(webhook_router)
app.include_router(health_router)
app.include_router(metrics_router)


@app.get("/")
def root():
    return {"message": "The Sentinel is running"}
