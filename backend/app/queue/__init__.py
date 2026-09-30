from app.queue.base import JobQueue
from app.queue.celery import CeleryJobQueue

__all__ = ["CeleryJobQueue", "JobQueue"]
