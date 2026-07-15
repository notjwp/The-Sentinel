from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from sentinel.monitoring.metrics import metrics

router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    """Prometheus text exposition of the in-process metrics.

    Queue depth is computed live at scrape time from the queue the composition
    root stashed on ``app.state`` (works for both the in-memory and Redis
    queues); a broken queue never fails the scrape.
    """
    queue = getattr(request.app.state, "job_queue", None)
    if queue is not None:
        try:
            metrics.gauge_set("sentinel_queue_depth", await queue.depth())
        except Exception:
            pass
    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4",
    )
