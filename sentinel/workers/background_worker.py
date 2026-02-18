import asyncio

from sentinel.workers.job_queue import JobQueue


class BackgroundWorker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def start(self) -> None:
        print("Background worker started", flush=True)
        while True:
            job = await self.queue.dequeue()
            print("Processing PR", job["pr_number"], flush=True)
            await asyncio.sleep(2)
            print("Audit complete", flush=True)
