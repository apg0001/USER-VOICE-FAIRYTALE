import structlog
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.config import get_settings

settings = get_settings()
log = structlog.get_logger(__name__)
celery_app = Celery(
    "voice_fairy_tale",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.inference_worker", "app.workers.cleanup_worker"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=20,
    timezone="UTC",
    beat_schedule={
        "cleanup-expired-artifacts": {
            "task": "voice.cleanup_expired",
            "schedule": 900.0,
        }
    },
)


@worker_process_init.connect  # type: ignore[untyped-decorator]
def report_worker_device(**_: object) -> None:
    if settings.use_mock_inference:
        log.info(
            "worker_device_ready",
            device=settings.cuda_device,
            gpu_required=False,
            reason="mock-inference",
        )
        return
    from app.workers.model_runtime import get_worker_model_manager

    snapshot = get_worker_model_manager(settings).diagnose()
    log.info("worker_device_ready", gpu_required=True, **snapshot.to_dict())


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def release_worker_models(**_: object) -> None:
    from app.workers.model_runtime import reset_worker_model_manager

    reset_worker_model_manager()

