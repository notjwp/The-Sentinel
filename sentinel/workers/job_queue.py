import asyncio

from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


class JobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def enqueue(self, job: dict) -> None:
        if not isinstance(job, dict):
            raise TypeError("job must be a dictionary")
        await self._queue.put(job)
        logger.info("Job enqueued")

    async def dequeue(self) -> dict:
        return await self._queue.get()
