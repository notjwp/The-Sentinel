"""Redis-backed dedup of GitHub webhook deliveries by X-GitHub-Delivery GUID.

Durable counterpart to ``sentinel.api.delivery_dedup.DeliveryDeduper``: the
seen-set lives in Redis (SET NX EX — one atomic op), so dedup survives restarts
and is correct across replicas by construction. Selected by the webhook's
``_build_deduper`` when ``REDIS_URL`` is set.

Fails OPEN on Redis errors: a webhook is processed (possibly twice) rather than
dropped — the idempotent review upsert absorbs the visible effect.
"""

from typing import Any

import redis.asyncio as redis_asyncio

from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


class RedisDeliveryDeduper:
    KEY_PREFIX = "sentinel:delivery:"

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = 600.0,
        client: Any | None = None,
    ) -> None:
        # ``client`` is a test seam (fakeredis.FakeAsyncRedis); from_url is lazy.
        self._client = client if client is not None else redis_asyncio.from_url(
            url, decode_responses=True
        )
        self._ttl_seconds = ttl_seconds

    async def is_duplicate(self, delivery_id: str | None) -> bool:
        """True when this id was already seen within the TTL; records it otherwise.

        Same contract as the in-memory deduper: requests without a usable id
        (None/empty/non-str) are never deduped.
        """
        if not isinstance(delivery_id, str) or not delivery_id.strip():
            return False

        key = f"{self.KEY_PREFIX}{delivery_id}"
        try:
            was_set = await self._client.set(
                key, "1", nx=True, ex=max(1, int(self._ttl_seconds))
            )
        except Exception:
            logger.exception("Redis dedup check failed; failing open for %s", delivery_id)
            return False
        return not was_set
