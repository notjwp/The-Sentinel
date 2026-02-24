import asyncio

import sentinel.workers.background_worker as background_worker_module
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue


def test_queue_enqueue_dequeue_1000_jobs_no_deadlock_or_duplicates():
    async def _run() -> None:
        queue = JobQueue()
        jobs = [{"repo": "stress", "pr_number": index} for index in range(1000)]

        for job in jobs:
            await queue.enqueue(job)

        assert queue._queue.qsize() == 1000

        seen: set[int] = set()
        for _ in range(1000):
            job = await asyncio.wait_for(queue.dequeue(), timeout=1.0)
            seen.add(job["pr_number"])

        assert len(seen) == 1000
        assert queue._queue.qsize() == 0

    asyncio.run(_run())


def test_worker_loop_does_not_crash_under_load(monkeypatch, capsys):
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(background_worker_module.asyncio, "sleep", fast_sleep)

    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        for index in range(300):
            await queue.enqueue({"repo": "load", "pr_number": index})

        task = asyncio.create_task(worker.start())
        await real_sleep(0)
        for _ in range(2000):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert queue._queue.qsize() == 0

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #" in output
