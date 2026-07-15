"""In-memory dedup of GitHub webhook deliveries by X-GitHub-Delivery GUID.

GitHub re-sends deliveries (manual redelivery, retries, accidental double-sends),
each carrying the same X-GitHub-Delivery id. Remembering recently seen ids lets
the webhook skip re-running the full pipeline (LLM budget, GitHub calls) — the
idempotent upsert_comment only hides the visible symptom of redundant runs.

Per-process and in-memory by design, consistent with the in-memory JobQueue;
durable dedup belongs with a durable queue, not here.
"""

import time
from collections.abc import Callable


class DeliveryDeduper:
    """Remembers recently seen delivery ids (TTL + capacity bounded)."""

    def __init__(
        self,
        max_entries: int = 2048,
        ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # id -> first-seen timestamp; plain dict keeps insertion order, so the
        # first key is always the oldest entry when capacity eviction kicks in.
        self._seen: dict[str, float] = {}

    def is_duplicate(self, delivery_id: str | None) -> bool:
        """True when this id was already seen within the TTL; records it otherwise.

        Requests without a usable id (None/empty/non-str) are never deduped, so
        manual curl calls and the test suite pass through untouched.
        """
        if not isinstance(delivery_id, str) or not delivery_id.strip():
            return False

        now = self._clock()
        self._purge_expired(now)

        if delivery_id in self._seen:
            return True

        self._seen[delivery_id] = now
        while len(self._seen) > self._max_entries:
            self._seen.pop(next(iter(self._seen)))
        return False

    def _purge_expired(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        expired = [key for key, seen_at in self._seen.items() if seen_at <= cutoff]
        for key in expired:
            self._seen.pop(key, None)
