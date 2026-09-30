import structlog

from app.core.config import get_settings
from app.models import build_model_registry
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="voice.run_inference", bind=True, acks_late=True)  # type: ignore[untyped-decorator]
def run_inference(self, job_id: str, model_key: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Phase 1 contract task; later phases attach DB state and real pipelines."""

    settings = get_settings()
    registry = build_model_registry(include_mock=settings.use_mock_inference)
    model = registry.create(model_key)
    log.info("worker_task_started", job_id=job_id, model=model_key, worker_id=self.request.hostname)
    model.load()
    try:
        output = model.infer({"job_id": job_id}, voice_profile={})
        model.postprocess(output)
        return {"job_id": job_id, "status": "mock-completed"}
    finally:
        model.unload()

