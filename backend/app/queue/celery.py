from app.queue.base import JobQueue
from app.workers.celery_app import celery_app


class CeleryJobQueue(JobQueue):
    def enqueue(self, job_id: str) -> str:
        result = celery_app.send_task("voice.run_inference", args=[job_id], task_id=job_id)
        return str(result.id)

    def revoke(self, task_id: str) -> None:
        celery_app.control.revoke(task_id, terminate=False)
