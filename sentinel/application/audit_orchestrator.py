from sentinel.workers.job_queue import JobQueue
from sentinel.monitoring.logger import get_logger

logger = get_logger(__name__)


class AuditOrchestrator:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def enqueue_pull_request(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")

        repo = payload.get("repo")
        pr_number = payload.get("pr_number")

        job = {
            "repo": repo,
            "pr_number": pr_number,
        }

        if "author" in payload:
            job["author"] = payload["author"]
        if "files" in payload:
            job["files"] = payload["files"]
        if "code" in payload:
            job["code"] = payload["code"]

        await self.queue.enqueue(job)
        logger.info("Queued PR audit job for repo=%s pr_number=%s", repo, pr_number)
