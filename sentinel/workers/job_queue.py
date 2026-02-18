import asyncio


class JobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def enqueue(self, job: dict) -> None:
        await self._queue.put(job)
        print("Job enqueued", flush=True)

    async def dequeue(self) -> dict:
        return await self._queue.get()
