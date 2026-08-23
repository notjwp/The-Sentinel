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

Retries are **bounded**: each recovery bumps the envelope's attempt count, and a
job that burns ``MAX_ATTEMPTS`` is parked on ``DEAD_KEY`` rather than re-queued,
so a job that reliably kills the worker cannot be resurrected forever.
"""

import asyncio
import json
import time
import uuid
from typing import Any

import redis.asyncio as redis_asyncio

from sentinel.monitoring.logger import get_logger
from sentinel.monitoring.metrics import metrics

logger = get_logger(__name__)


class RedisJobQueue:
    QUEUE_KEY = "sentinel:jobs"
    PROCESSING_KEY = "sentinel:jobs:processing"
    # Jobs that exhausted their retries. Recovery is what makes a crash safe, but
    # unbounded recovery makes a poison job immortal: one that kills the process
    # would be re-queued at every start, forever. Parked here instead, for a human.
    DEAD_KEY = "sentinel:jobs:dead"
    MAX_ATTEMPTS = 3
    # Outage hygiene: one traceback per failing streak, then at most one summary
    # line per suppress window; retry sleeps back off exponentially to the cap.
    LOG_SUPPRESS_SECONDS = 60.0
    MAX_BACKOFF_SECONDS = 5.0

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
        self._consecutive_failures = 0
        self._last_failure_log = 0.0
        # (job, raw message) for jobs handed out but not yet acked, keyed by the
        # returned dict's id(). Holding the job itself keeps the dict alive, so
        # its id() can never be reused by a later job while the entry exists;
        # ack pops the entry, bounding this to the jobs in flight.
        self._inflight: dict[int, tuple[dict, str]] = {}

    async def enqueue(self, job: dict) -> None:
        if not isinstance(job, dict):
            raise TypeError("job must be a dictionary")
        # Envelope gives every message a distinct identity, so LREM on ack can
        # never remove a different-but-identical job payload. ``attempts`` lives
        # on the envelope, never inside ``job`` — the dict handed to the worker
        # must stay exactly what the producer enqueued.
        raw = json.dumps({"id": uuid.uuid4().hex, "attempts": 0, "job": job})
        # Redis errors propagate: the webhook route turns enqueue failures into
        # HTTP 500, and GitHub redelivers later.
        await self._client.lpush(self.QUEUE_KEY, raw)
        logger.info("Job enqueued (redis)")

    def _backoff_seconds(self) -> float:
        exponent = min(self._consecutive_failures, 10)  # cap 2**n growth
        return min(self._poll_interval * (2**exponent), self.MAX_BACKOFF_SECONDS)

    def _log_dequeue_failure(self) -> None:
        """Traceback once per failing streak, then one line per suppress window."""
        now = time.monotonic()
        if self._consecutive_failures == 1:
            logger.exception("Redis dequeue failed; backing off and retrying")
            self._last_failure_log = now
        elif now - self._last_failure_log >= self.LOG_SUPPRESS_SECONDS:
            logger.error(
                "Redis still unreachable (attempt %s); continuing to retry",
                self._consecutive_failures,
            )
            self._last_failure_log = now

    async def dequeue(self) -> dict:
        # Poll with non-blocking LMOVE rather than BLMOVE: cancellation-friendly,
        # resilient to Redis restarts, and exercisable on fakeredis.
        while True:
            try:
                raw = await self._client.lmove(
                    self.QUEUE_KEY, self.PROCESSING_KEY, "RIGHT", "LEFT"
                )
            except Exception:
                self._consecutive_failures += 1
                metrics.counter_inc("sentinel_redis_errors_total", {"op": "dequeue"})
                self._log_dequeue_failure()
                await asyncio.sleep(self._backoff_seconds())
                continue

            if self._consecutive_failures:
                logger.info(
                    "Redis reconnected after %s failed attempt(s)",
                    self._consecutive_failures,
                )
                self._consecutive_failures = 0
                self._last_failure_log = 0.0

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
            metrics.counter_inc("sentinel_redis_errors_total", {"op": "ack"})
            logger.exception("Redis ack failed; job will be re-queued on next restart")

    async def recover_pending(self) -> int:
        """Re-queue jobs a crashed worker left in the processing list.

        Each recovery bumps the envelope's ``attempts``; a job that has burned
        MAX_ATTEMPTS is moved to DEAD_KEY instead of being re-queued, so a job
        that reliably kills the worker stops taking the process down with it.
        Returns the number **re-queued** (interface parity with the in-memory
        queue's no-op); dead-lettered jobs are logged and counted separately.
        """
        recovered = 0
        dead_lettered = 0
        while True:
            try:
                # LMOVE first, exactly as before: the message is never in flight
                # between two keys. Rewriting it afterwards means a crash mid-
                # recovery leaves the job queued with a stale attempt count —
                # one extra retry at worst, never a lost job.
                raw = await self._client.lmove(
                    self.PROCESSING_KEY, self.QUEUE_KEY, "RIGHT", "LEFT"
                )
                if raw is None:
                    break
                envelope = self._parse_envelope(raw)
                if envelope is None:
                    await self._client.lrem(self.QUEUE_KEY, 1, raw)  # poison, drop it
                    continue
                attempts = envelope.get("attempts")
                envelope["attempts"] = (attempts if isinstance(attempts, int) else 0) + 1
                if envelope["attempts"] >= self.MAX_ATTEMPTS:
                    await self._client.lrem(self.QUEUE_KEY, 1, raw)
                    await self._client.lpush(self.DEAD_KEY, json.dumps(envelope))
                    dead_lettered += 1
                    metrics.counter_inc("sentinel_jobs_deadlettered_total")
                    continue
                await self._client.lrem(self.QUEUE_KEY, 1, raw)
                await self._client.lpush(self.QUEUE_KEY, json.dumps(envelope))
                recovered += 1
            except Exception:
                metrics.counter_inc("sentinel_redis_errors_total", {"op": "recover"})
                logger.exception("Redis recovery failed; continuing with %s recovered", recovered)
                break
        if recovered:
            logger.info("Recovered %s orphaned job(s) from the processing list", recovered)
        if dead_lettered:
            logger.error(
                "Dead-lettered %s job(s) after %s attempts; inspect %s",
                dead_lettered,
                self.MAX_ATTEMPTS,
                self.DEAD_KEY,
            )
        return recovered

    async def depth(self) -> int:
        """Jobs currently waiting (the /metrics queue-depth gauge). -1 on error."""
        try:
            return int(await self._client.llen(self.QUEUE_KEY))
        except Exception:
            return -1

    @staticmethod
    def _parse_envelope(raw: str) -> dict | None:
        """Parse a queue message into its envelope dict; None if malformed."""
        try:
            envelope = json.loads(raw)
        except Exception:
            logger.warning("Discarding unparseable queue message")
            return None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("job"), dict):
            logger.warning("Discarding queue message without a job dict")
            return None
        return envelope

    @staticmethod
    def _parse_job(raw: str) -> dict | None:
        """Extract the job dict from an envelope string; None if malformed."""
        envelope = RedisJobQueue._parse_envelope(raw)
        return envelope["job"] if envelope is not None else None

    async def _discard_processing(self, raw: str) -> None:
        try:
            await self._client.lrem(self.PROCESSING_KEY, 1, raw)
        except Exception:
            logger.exception("Failed to discard poison message from processing list")
