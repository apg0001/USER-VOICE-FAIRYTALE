from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("voice_fairy_tale", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=20,
    timezone="UTC",
)
celery_app.autodiscover_tasks(["app.workers"])

