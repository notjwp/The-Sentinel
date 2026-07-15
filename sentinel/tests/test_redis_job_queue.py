"""M4: RedisJobQueue — durable reliable-queue semantics, proven on fakeredis.

No live Redis needed: fakeredis.FakeAsyncRedis is injected through the client
seam. A shared FakeServer stands in for "the same Redis across two processes"
in the crash-recovery test. M7 adds outage log-hygiene/backoff and depth tests.
"""

import asyncio
import json
import logging

import fakeredis
import pytest

from sentinel.infrastructure.redis.redis_job_queue import RedisJobQueue


def _fake_client(server: fakeredis.FakeServer | None = None) -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)


def _queue(client: fakeredis.FakeAsyncRedis | None = None) -> RedisJobQueue:
    return RedisJobQueue(
        "redis://unused:6379/0",
        client=client if client is not None else _fake_client(),
        poll_interval=0.01,
    )


def test_constructor_does_not_connect():
    # from_url is lazy; building against a dead address must not raise.
    RedisJobQueue("redis://127.0.0.1:1/0")


def test_enqueue_rejects_non_dict():
    async def _run() -> None:
        queue = _queue()
        with pytest.raises(TypeError):
            await queue.enqueue("bad")  # type: ignore[arg-type]

    asyncio.run(_run())


def test_fifo_round_trip_preserves_nested_payload():
    async def _run() -> None:
        queue = _queue()
        jobs = [
            {
                "owner": "octo",
                "repo": "hello",
                "pr_number": 1,
                "files": ["a.py", "README.md"],
                "file_contents": {"a.py": "+x = 1"},
            },
            {"repo": "hello", "pr_number": 2},
            {"repo": "hello", "pr_number": 3},
        ]
        for job in jobs:
            await queue.enqueue(job)
        dequeued = [await queue.dequeue() for _ in jobs]
        assert dequeued == jobs

    asyncio.run(_run())


def test_dequeue_moves_message_to_processing_and_ack_clears_it():
    async def _run() -> None:
        client = _fake_client()
        queue = _queue(client)
        await queue.enqueue({"repo": "hello", "pr_number": 7})

        job = await queue.dequeue()
        assert await client.llen(RedisJobQueue.QUEUE_KEY) == 0
        assert await client.llen(RedisJobQueue.PROCESSING_KEY) == 1

        await queue.ack(job)
        assert await client.llen(RedisJobQueue.PROCESSING_KEY) == 0

    asyncio.run(_run())


def test_crash_recovery_requeues_unacked_job():
    async def _run() -> None:
        server = fakeredis.FakeServer()  # one "real" Redis shared by both instances
        first = _queue(_fake_client(server))
        await first.enqueue({"repo": "hello", "pr_number": 42})
        job = await first.dequeue()  # dequeued but never acked -> simulated crash

        second = _queue(_fake_client(server))  # fresh process after restart
        assert await second.recover_pending() == 1
        recovered = await asyncio.wait_for(second.dequeue(), timeout=2.0)
        assert recovered == job

    asyncio.run(_run())


def test_ack_is_noop_for_unknown_job():
    async def _run() -> None:
        queue = _queue()
        await queue.ack({"repo": "never-dequeued", "pr_number": 1})  # must not raise

    asyncio.run(_run())


def test_dequeue_waits_until_a_job_arrives():
    async def _run() -> None:
        queue = _queue()

        async def _enqueue_later() -> None:
            await asyncio.sleep(0.05)
            await queue.enqueue({"repo": "late", "pr_number": 9})

        enqueue_task = asyncio.create_task(_enqueue_later())
        job = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        await enqueue_task
        assert job == {"repo": "late", "pr_number": 9}

    asyncio.run(_run())


def test_depth_reports_waiting_jobs_and_minus_one_on_error():
    async def _run() -> None:
        queue = _queue()
        assert await queue.depth() == 0
        for pr in (1, 2, 3):
            await queue.enqueue({"repo": "d", "pr_number": pr})
        assert await queue.depth() == 3

        class _Broken:
            async def llen(self, *a):
                raise ConnectionError("down")

        broken = RedisJobQueue("redis://unused", client=_Broken())
        assert await broken.depth() == -1

    asyncio.run(_run())


class _AlwaysFailingClient:
    async def lmove(self, *args, **kwargs):
        raise ConnectionError("redis is down")


_LOGGER_NAME = "sentinel.infrastructure.redis.redis_job_queue"


def _drive_failing_dequeue(queue: RedisJobQueue, min_sleeps: int, monkeypatch) -> list[float]:
    """Run dequeue against a dead client until it has backed off min_sleeps times."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def _run() -> None:
        task = asyncio.create_task(queue.dequeue())
        while len(sleeps) < min_sleeps:
            await real_sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    return sleeps


def test_outage_backoff_grows_and_traceback_logged_once(caplog, monkeypatch):
    queue = RedisJobQueue(
        "redis://unused", client=_AlwaysFailingClient(), poll_interval=0.01
    )
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        sleeps = _drive_failing_dequeue(queue, min_sleeps=12, monkeypatch=monkeypatch)

    # Backoff: 0.01 * 2**n, capped, never shrinking while failing.
    assert sleeps[0] == pytest.approx(0.02)
    assert max(sleeps) == RedisJobQueue.MAX_BACKOFF_SECONDS
    assert sleeps == sorted(sleeps)

    # One traceback for the whole streak; summaries suppressed inside the window.
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1
    assert sum("still unreachable" in r.message for r in caplog.records) == 0


def test_outage_summary_line_after_suppress_window(caplog, monkeypatch):
    monkeypatch.setattr(RedisJobQueue, "LOG_SUPPRESS_SECONDS", 0.0)
    queue = RedisJobQueue("redis://unused", client=_AlwaysFailingClient(), poll_interval=0.01)
    with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
        _drive_failing_dequeue(queue, min_sleeps=5, monkeypatch=monkeypatch)
    assert sum("still unreachable" in r.message for r in caplog.records) >= 1


class _FlakyClient:
    def __init__(self, failures: int, raw: str) -> None:
        self._failures = failures
        self._raw = raw

    async def lmove(self, *args, **kwargs):
        if self._failures > 0:
            self._failures -= 1
            raise ConnectionError("redis is down")
        return self._raw

    async def lrem(self, *args, **kwargs):
        return 1


def test_dequeue_recovers_and_logs_reconnect(caplog, monkeypatch):
    raw = json.dumps({"id": "r1", "job": {"repo": "back", "pr_number": 9}})
    queue = RedisJobQueue(
        "redis://unused", client=_FlakyClient(failures=3, raw=raw), poll_interval=0.01
    )
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        job = asyncio.run(queue.dequeue())

    assert job == {"repo": "back", "pr_number": 9}
    assert len(sleeps) == 3  # one backoff per failure, then success
    assert any("Redis reconnected after 3 failed attempt(s)" in r.message for r in caplog.records)


def test_poison_messages_are_discarded_and_valid_job_still_served():
    async def _run() -> None:
        client = _fake_client()
        queue = _queue(client)
        # Oldest first: raw garbage, then an envelope whose job isn't a dict.
        await client.lpush(RedisJobQueue.QUEUE_KEY, "not json{")
        await client.lpush(RedisJobQueue.QUEUE_KEY, json.dumps({"id": "x", "job": "flat"}))
        await queue.enqueue({"repo": "ok", "pr_number": 1})

        job = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        assert job == {"repo": "ok", "pr_number": 1}
        # Both poison messages were dropped from processing; only the live job remains.
        assert await client.llen(RedisJobQueue.PROCESSING_KEY) == 1
        assert await client.llen(RedisJobQueue.QUEUE_KEY) == 0

    asyncio.run(_run())
