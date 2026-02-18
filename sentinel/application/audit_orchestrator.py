from sentinel.workers.job_queue import JobQueue


class AuditOrchestrator:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def enqueue_pull_request(self, payload: dict) -> None:
        repo = payload.get("repo")
        pr_number = payload.get("pr_number")
        job = {
            "repo": repo,
            "pr_number": pr_number,
        }
        await self.queue.enqueue(job)
