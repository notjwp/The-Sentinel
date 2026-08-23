import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from sentinel.config.settings import get_settings
from sentinel.monitoring.logger import get_logger
from sentinel.monitoring.metrics import metrics

router = APIRouter()
logger = get_logger(__name__)

_AUTH_HEADER = "Authorization"
_SCHEME = "Bearer "


async def verify_metrics_token(request: Request) -> None:
    """Route dependency. Enforces a bearer token only when one is configured.

    Mirrors ``webhook_security.verify_webhook_signature``: no METRICS_TOKEN means
    no enforcement (local scraping and the test suite are unaffected), which keeps
    the endpoint's historical behavior as the default. Set the token before
    exposing the service publicly — the series leak review volume, severity mix,
    and queue depth. Compared as bytes so a non-ASCII header returns 401 rather
    than raising a 500.
    """
    token = get_settings().METRICS_TOKEN
    if not token:
        return
    provided = request.headers.get(_AUTH_HEADER) or ""
    expected = f"{_SCHEME}{token}"
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        logger.warning("Rejected /metrics scrape: missing or invalid bearer token")
        raise HTTPException(status_code=401, detail="Invalid metrics token")


@router.get("/metrics", dependencies=[Depends(verify_metrics_token)])
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
