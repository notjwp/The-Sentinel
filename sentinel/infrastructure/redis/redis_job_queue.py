"""Durable FIFO job queue on Redis (LPUSH + LMOVE reliable-queue pattern).

Drop-in counterpart to the in-memory ``sentinel.workers.job_queue.JobQueue``:
same ``enqueue``/``dequeue``/``ack``/``recover_pending`` surface, but jobs live
in a Redis list, so they survive process restarts and are shared across
replicas. Selected by the composition root when ``REDIS_URL`` is set.

Delivery semantics are **at-least-once**: ``dequeue`` atomically moves the
message to a processing list, ``ack`` removes it after the job completes, and
``recover_pending`` (run at worker start) re-queues anything a crashed worker
left behind. A re-run after a crash is safe — the review post is an idempotent
upsert and assessment is deterministic.
"""

import asyncio
import json
import uuid
from typing import Any

import redis.asyncio as redis_asyncio

from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


class RedisJobQueue:
    QUEUE_KEY = "sentinel:jobs"
    PROCESSING_KEY = "sentinel:jobs:processing"

    def __init__(
        self,
        url: str,
        *,
        client: Any | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        # ``client`` is a test seam (fakeredis.FakeAsyncRedis). from_url does not
        # connect — redis-py dials lazily on the first command.
        self._client = client if client is not None else redis_asyncio.from_url(
            url, decode_responses=True
        )
        self._poll_interval = poll_interval
        # (job, raw message) for jobs handed out but not yet acked, keyed by the
        # returned dict's id(). Holding the job itself keeps the dict alive, so
        # its id() can never be reused by a later job while the entry exists;
        # ack pops the entry, bounding this to the jobs in flight.
        self._inflight: dict[int, tuple[dict, str]] = {}

    async def enqueue(self, job: dict) -> None:
        if not isinstance(job, dict):
            raise TypeError("job must be a dictionary")
        # Envelope gives every message a distinct identity, so LREM on ack can
        # never remove a different-but-identical job payload.
        raw = json.dumps({"id": uuid.uuid4().hex, "job": job})
        # Redis errors propagate: the webhook route turns enqueue failures into
        # HTTP 500, and GitHub redelivers later.
        await self._client.lpush(self.QUEUE_KEY, raw)
        logger.info("Job enqueued (redis)")

    async def dequeue(self) -> dict:
        # Poll with non-blocking LMOVE rather than BLMOVE: cancellation-friendly,
        # resilient to Redis restarts, and exercisable on fakeredis.
        while True:
            try:
                raw = await self._client.lmove(
                    self.QUEUE_KEY, self.PROCESSING_KEY, "RIGHT", "LEFT"
                )
            except Exception:
                logger.exception("Redis dequeue failed; retrying")
                await asyncio.sleep(self._poll_interval)
                continue

            if raw is not None:
                job = self._parse_job(raw)
                if job is None:
                    # Poison pill: drop it from processing so it can't loop forever.
                    await self._discard_processing(raw)
                    continue
                self._inflight[id(job)] = (job, raw)
                return job

            await asyncio.sleep(self._poll_interval)

    async def ack(self, job: dict) -> None:
        """Mark a dequeued job as done, removing it from the processing list.

        Failure-safe: on Redis error the message stays in processing and is
        re-queued by ``recover_pending`` on the next start — never lost.
        """
        entry = self._inflight.pop(id(job), None)
        if entry is None:
            return
        _, raw = entry
        try:
            await self._client.lrem(self.PROCESSING_KEY, 1, raw)
        except Exception:
            logger.exception("Redis ack failed; job will be re-queued on next restart")

    async def recover_pending(self) -> int:
        """Re-queue jobs a crashed worker left in the processing list."""
        recovered = 0
        while True:
            try:
                raw = await self._client.lmove(
                    self.PROCESSING_KEY, self.QUEUE_KEY, "RIGHT", "LEFT"
                )
            except Exception:
                logger.exception("Redis recovery failed; continuing with %s recovered", recovered)
                break
            if raw is None:
                break
            recovered += 1
        if recovered:
            logger.info("Recovered %s orphaned job(s) from the processing list", recovered)
        return recovered

    @staticmethod
    def _parse_job(raw: str) -> dict | None:
        """Extract the job dict from an envelope string; None if malformed."""
        try:
            envelope = json.loads(raw)
        except Exception:
            logger.warning("Discarding unparseable queue message")
            return None
        job = envelope.get("job") if isinstance(envelope, dict) else None
        if not isinstance(job, dict):
            logger.warning("Discarding queue message without a job dict")
            return None
        return job

    async def _discard_processing(self, raw: str) -> None:
        try:
            await self._client.lrem(self.PROCESSING_KEY, 1, raw)
        except Exception:
            logger.exception("Failed to discard poison message from processing list")
