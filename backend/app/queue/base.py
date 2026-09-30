from typing import Protocol


class JobQueue(Protocol):
    """Small queue contract kept independent from Celery in API tests."""

    def enqueue(self, job_id: str) -> str:
        """Schedule a job and return the broker task identifier."""
        ...

    def revoke(self, task_id: str) -> None:
        """Request cooperative cancellation without killing the worker process."""
        ...

