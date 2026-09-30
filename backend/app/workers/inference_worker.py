import asyncio
import time
from typing import Any, cast

import structlog
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.models import JobMode, JobOutput, JobStatus, VoiceProfile
from app.db.session import create_session_factory
from app.models import build_model_registry
from app.models.base import VoiceModel
from app.models.tts import TTSModel, build_tts_model_registry
from app.pipelines import TTSPipeline
from app.queue import CeleryJobQueue
from app.services.job_service import InvalidJobTransitionError, JobService
from app.storage import LocalObjectStorage
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


async def execute_job(job_id: str) -> dict[str, str]:
    """Execute one persisted Job; every durable state change is committed to the DB."""

    settings = get_settings()
    engine, session_factory = create_session_factory(settings.database_url)
    model: Any | None = None
    generated_storage_key: str | None = None
    output_storage = LocalObjectStorage(settings.storage_path)
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
            is_tts = job.mode in {JobMode.GENERAL_TTS, JobMode.LONG_FORM_TTS}
            if is_tts:
                tts_registry = build_tts_model_registry(
                    include_mock=settings.use_mock_inference
                )
                model = tts_registry.create(job.model_key)
            else:
                registry = build_model_registry(include_mock=settings.use_mock_inference)
                model = registry.create(job.model_key)
            load_started = time.monotonic()
            model.load()
            load_time = time.monotonic() - load_started

            if await service.is_cancelled(job_id):
                return {"job_id": job_id, "status": JobStatus.CANCELLED.value}
            await service.transition(job_id, JobStatus.INFERENCE, progress=45)
            inference_started = time.monotonic()
            output_metadata: dict[str, Any] = {}
            output: Any | None = None
            if is_tts:
                if not job.voice_profile_id:
                    raise ValueError("ready voice profile required")
                profile = await session.scalar(
                    select(VoiceProfile).where(VoiceProfile.id == job.voice_profile_id)
                )
                if profile is None or profile.status != "READY":
                    raise ValueError("ready voice profile required")
                profile_data: dict[str, Any] = profile.profile_metadata.get(
                    "model_profiles", {}
                ).get(job.model_key, {})

                async def checkpoint(completed: int, total: int) -> None:
                    tracked = await service.get_job(job_id, for_update=True)
                    tracked.progress = 40 + round(40 * completed / total)
                    tracked.metrics = {
                        **tracked.metrics,
                        "tts_checkpoint": {"completed": completed, "total": total},
                    }
                    await session.commit()

                result = await TTSPipeline(output_storage).run(
                    job_id=job.id,
                    text=job.input_text or "",
                    model=cast(TTSModel, model),
                    voice_profile=profile_data,
                    max_chunk_chars=280 if job.mode == JobMode.LONG_FORM_TTS else 2_000,
                    on_checkpoint=checkpoint,
                )
                session.add(
                    JobOutput(
                        job_id=job.id,
                        storage_key=result.storage_key,
                        content_type="audio/wav",
                        duration_seconds=result.duration_seconds,
                        output_metadata={
                            "sample_rate": result.sample_rate,
                            "chunk_count": result.chunk_count,
                            "model": job.model_key,
                        },
                    )
                )
                generated_storage_key = result.storage_key
                output_metadata = {
                    "output_duration": result.duration_seconds,
                    "chunk_count": result.chunk_count,
                    "sample_rate": result.sample_rate,
                }
            else:
                voice_model = cast(VoiceModel, model)
                model_input = voice_model.preprocess(job.input_text or job.input_storage_key)
                output = voice_model.infer(model_input, voice_profile={})
            inference_time = time.monotonic() - inference_started

            if await service.is_cancelled(job_id):
                if generated_storage_key is not None:
                    await output_storage.delete(generated_storage_key)
                return {"job_id": job_id, "status": JobStatus.CANCELLED.value}
            await service.transition(job_id, JobStatus.POSTPROCESSING, progress=85)
            post_started = time.monotonic()
            if not is_tts:
                cast(VoiceModel, model).postprocess(output)
            postprocess_time = time.monotonic() - post_started

            tracked_job = await service.get_job(job_id, for_update=True)
            tracked_job.metrics = {
                "input_duration": tracked_job.request_config.get("input_duration"),
                "model_loading_time": round(load_time, 6),
                "inference_time": round(inference_time, 6),
                "postprocessing_time": round(postprocess_time, 6),
                "processing_time": round(time.monotonic() - started, 6),
                "model": tracked_job.model_key,
                **(
                    {"tts_checkpoint": tracked_job.metrics.get("tts_checkpoint")}
                    if is_tts
                    else {}
                ),
                **output_metadata,
            }
            await service.transition(job_id, JobStatus.COMPLETED, progress=100)
            return {"job_id": job_id, "status": JobStatus.COMPLETED.value}
    except InvalidJobTransitionError as error:
        if generated_storage_key is not None:
            await output_storage.delete(generated_storage_key)
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    delete(JobOutput).where(JobOutput.job_id == job_id)
                )
                await cleanup_session.commit()
        log.info("worker_transition_skipped", job_id=job_id, reason=str(error))
        return {"job_id": job_id, "status": "transition-skipped"}
    except Exception as error:
        log.exception("worker_task_failed", job_id=job_id, error_type=type(error).__name__)
        try:
            if generated_storage_key is not None:
                await output_storage.delete(generated_storage_key)
            async with session_factory() as recovery_session:
                await recovery_session.execute(
                    delete(JobOutput).where(JobOutput.job_id == job_id)
                )
                await recovery_session.commit()
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

