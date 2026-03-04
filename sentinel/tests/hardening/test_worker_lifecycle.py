import asyncio

from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue
import sentinel.workers.background_worker as bw_module


# --- Worker Start ---


def test_worker_starts_and_processes_single_job(capsys):
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        await queue.enqueue({"repo": "lifecycle", "pr_number": 1})

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(500):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #1 Risk:" in output


# --- Worker Graceful Shutdown Via Cancellation ---


def test_worker_cancellation_is_clean():
    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        await queue.enqueue({"repo": "cancel", "pr_number": 1})

        real_sleep = asyncio.sleep

        async def fast_sleep(_: float) -> None:
            await real_sleep(0)

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(500):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True

        bw_module.asyncio.sleep = original_sleep
        assert cancelled

    asyncio.run(_run())


# --- Queue Drain On Shutdown ---


def test_queue_drains_before_cancellation():
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        for i in range(10):
            await queue.enqueue({"repo": "drain", "pr_number": i})

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(2000):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep
        assert queue._queue.qsize() == 0

    asyncio.run(_run())


# --- Multiple Worker Instances ---


def test_multiple_workers_drain_shared_queue(capsys):
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        for i in range(20):
            await queue.enqueue({"repo": "multi", "pr_number": i})

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        worker1 = BackgroundWorker(queue)
        worker2 = BackgroundWorker(queue)
        task1 = asyncio.create_task(worker1.start())
        task2 = asyncio.create_task(worker2.start())

        for _ in range(3000):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task1.cancel()
        task2.cancel()
        for task in [task1, task2]:
            try:
                await task
            except asyncio.CancelledError:
                pass

        bw_module.asyncio.sleep = original_sleep
        assert queue._queue.qsize() == 0

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #" in output


# --- No Orphan Tasks After Shutdown ---


def test_no_orphan_tasks_after_worker_shutdown():
    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        await queue.enqueue({"repo": "orphan", "pr_number": 1})

        real_sleep = asyncio.sleep

        async def fast_sleep(_: float) -> None:
            await real_sleep(0)

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        for _ in range(500):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep

        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        assert len(pending) == 0

    asyncio.run(_run())


# --- Worker Handles Empty Queue Block ---


def test_worker_blocks_on_empty_queue_and_resumes():
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)

        original_sleep = bw_module.asyncio.sleep
        bw_module.asyncio.sleep = fast_sleep

        task = asyncio.create_task(worker.start())
        await real_sleep(0.05)

        await queue.enqueue({"repo": "delayed", "pr_number": 42})
        for _ in range(1000):
            if queue._queue.qsize() == 0:
                break
            await real_sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        bw_module.asyncio.sleep = original_sleep
        assert queue._queue.qsize() == 0

    asyncio.run(_run())
