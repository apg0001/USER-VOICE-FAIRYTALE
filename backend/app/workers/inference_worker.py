import asyncio
import time
from typing import Any

import structlog

from app.core.config import get_settings
from app.db.models import JobStatus
from app.db.session import create_session_factory
from app.models import build_model_registry
from app.queue import CeleryJobQueue
from app.services.job_service import InvalidJobTransitionError, JobService
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


async def execute_job(job_id: str) -> dict[str, str]:
    """Execute one persisted Job; every durable state change is committed to the DB."""

    settings = get_settings()
    engine, session_factory = create_session_factory(settings.database_url)
    model: Any | None = None
    started = time.monotonic()
    try:
        async with session_factory() as session:
            service = JobService(session, CeleryJobQueue())
            job = await service.get_job(job_id)
            if job.status != JobStatus.QUEUED:
                return {"job_id": job_id, "status": job.status.value}

            await service.transition(job_id, JobStatus.PREPROCESSING, progress=10)
            if await service.is_cancelled(job_id):
                return {"job_id": job_id, "status": JobStatus.CANCELLED.value}

            await service.transition(job_id, JobStatus.LOADING_MODEL, progress=30)
            registry = build_model_registry(include_mock=settings.use_mock_inference)
            model = registry.create(job.model_key)
            load_started = time.monotonic()
            model.load()
            load_time = time.monotonic() - load_started

            if await service.is_cancelled(job_id):
                return {"job_id": job_id, "status": JobStatus.CANCELLED.value}
            await service.transition(job_id, JobStatus.INFERENCE, progress=45)
            inference_started = time.monotonic()
            model_input = model.preprocess(job.input_text or job.input_storage_key)
            output = model.infer(model_input, voice_profile={})
            inference_time = time.monotonic() - inference_started

            if await service.is_cancelled(job_id):
                return {"job_id": job_id, "status": JobStatus.CANCELLED.value}
            await service.transition(job_id, JobStatus.POSTPROCESSING, progress=85)
            post_started = time.monotonic()
            model.postprocess(output)
            postprocess_time = time.monotonic() - post_started

            tracked_job = await service.get_job(job_id, for_update=True)
            tracked_job.metrics = {
                "input_duration": tracked_job.request_config.get("input_duration"),
                "model_loading_time": round(load_time, 6),
                "inference_time": round(inference_time, 6),
                "postprocessing_time": round(postprocess_time, 6),
                "processing_time": round(time.monotonic() - started, 6),
                "model": tracked_job.model_key,
            }
            await service.transition(job_id, JobStatus.COMPLETED, progress=100)
            return {"job_id": job_id, "status": JobStatus.COMPLETED.value}
    except InvalidJobTransitionError as error:
        log.info("worker_transition_skipped", job_id=job_id, reason=str(error))
        return {"job_id": job_id, "status": "transition-skipped"}
    except Exception as error:
        log.exception("worker_task_failed", job_id=job_id, error_type=type(error).__name__)
        try:
            async with session_factory() as recovery_session:
                service = JobService(recovery_session, CeleryJobQueue())
                await service.fail_job(
                    job_id,
                    code="INFERENCE_FAILED",
                    detail=type(error).__name__,
                )
        except Exception:
            log.exception("worker_failure_persistence_failed", job_id=job_id)
        return {"job_id": job_id, "status": JobStatus.FAILED.value}
    finally:
        if model is not None:
            try:
                model.unload()
            except Exception:
                log.exception("model_unload_failed", job_id=job_id)
        await engine.dispose()


@celery_app.task(name="voice.run_inference", bind=True, acks_late=True)  # type: ignore[untyped-decorator]
def run_inference(self: Any, job_id: str) -> dict[str, str]:
    log.info("worker_task_started", job_id=job_id, worker_id=self.request.hostname)
    return asyncio.run(execute_job(job_id))

