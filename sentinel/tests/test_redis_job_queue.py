"""M4: RedisJobQueue — durable reliable-queue semantics, proven on fakeredis.

No live Redis needed: fakeredis.FakeAsyncRedis is injected through the client
seam. A shared FakeServer stands in for "the same Redis across two processes"
in the crash-recovery test.
"""

import asyncio
import json

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
