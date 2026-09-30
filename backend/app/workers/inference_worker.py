import asyncio
import time
from contextlib import ExitStack
from typing import Any, cast

import structlog
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.models import JobMode, JobOutput, JobStatus, VoiceProfile
from app.db.session import create_session_factory
from app.models import build_model_registry
from app.models.base import VoiceModel
from app.models.manager import (
    GPUUnavailableError,
    ModelCacheKey,
    ModelCapacityError,
    VRAMAdmissionError,
)
from app.models.separation import SeparationModel, build_separation_registry
from app.models.singing import SingingVoiceModel, build_singing_registry
from app.models.tts import TTSModel, build_tts_model_registry
from app.models.voice_conversion import (
    VoiceConversionModel,
    build_voice_conversion_registry,
)
from app.pipelines import SingingPipeline, TTSPipeline, VoiceConversionPipeline
from app.queue import CeleryJobQueue
from app.services.job_service import InvalidJobTransitionError, JobService
from app.storage import LocalObjectStorage
from app.workers.celery_app import celery_app
from app.workers.model_runtime import get_worker_model_manager

log = structlog.get_logger(__name__)


class JobExecutionCancelled(Exception):
    pass


def _failure_code(error: Exception) -> str:
    if isinstance(error, GPUUnavailableError):
        return "GPU_UNAVAILABLE"
    if isinstance(error, VRAMAdmissionError):
        return "VRAM_ADMISSION_FAILED"
    if isinstance(error, ModelCapacityError):
        return "MODEL_CAPACITY_EXCEEDED"
    return "INFERENCE_FAILED"


async def execute_job(job_id: str) -> dict[str, str]:
    """Execute one persisted Job; every durable state change is committed to the DB."""

    settings = get_settings()
    engine, session_factory = create_session_factory(settings.database_url)
    manager = get_worker_model_manager(settings)
    lease_stack = ExitStack()
    model: Any | None = None
    active_cache_keys: set[ModelCacheKey] = set()
    model_cache_metrics: list[dict[str, object]] = []
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
            is_speech_vc = job.mode == JobMode.SPEECH_VOICE_CONVERSION
            is_singing_vc = job.mode == JobMode.SINGING_VOICE_CONVERSION
            separator: SeparationModel | None = None
            load_time = 0.0
            if is_tts:
                tts_registry = build_tts_model_registry(
                    include_mock=settings.use_mock_inference
                )
                tts_lease = lease_stack.enter_context(
                    manager.lease("tts", lambda: tts_registry.create(job.model_key))
                )
                model = tts_lease.model
                active_cache_keys.add(tts_lease.cache_key)
                model_cache_metrics.append(tts_lease.metrics())
                load_time += tts_lease.load_seconds
            elif is_speech_vc:
                conversion_registry = build_voice_conversion_registry(
                    include_mock=settings.use_mock_inference
                )
                conversion_lease = lease_stack.enter_context(
                    manager.lease(
                        "speech-vc", lambda: conversion_registry.create(job.model_key)
                    )
                )
                model = conversion_lease.model
                active_cache_keys.add(conversion_lease.cache_key)
                model_cache_metrics.append(conversion_lease.metrics())
                load_time += conversion_lease.load_seconds
            elif is_singing_vc:
                separation_registry = build_separation_registry(
                    include_mock=settings.use_mock_inference
                )
                singing_registry = build_singing_registry(
                    include_mock=settings.use_mock_inference
                )
                separator_lease = lease_stack.enter_context(
                    manager.lease(
                        "separation",
                        lambda: separation_registry.create("mock-separator-v1"),
                    )
                )
                singing_lease = lease_stack.enter_context(
                    manager.lease(
                        "singing-vc", lambda: singing_registry.create(job.model_key)
                    )
                )
                separator = separator_lease.model
                model = singing_lease.model
                for acquired_lease in (separator_lease, singing_lease):
                    active_cache_keys.add(acquired_lease.cache_key)
                    model_cache_metrics.append(acquired_lease.metrics())
                    load_time += acquired_lease.load_seconds
            else:
                registry = build_model_registry(include_mock=settings.use_mock_inference)
                generic_lease = lease_stack.enter_context(
                    manager.lease("generic", lambda: registry.create(job.model_key))
                )
                model = generic_lease.model
                active_cache_keys.add(generic_lease.cache_key)
                model_cache_metrics.append(generic_lease.metrics())
                load_time += generic_lease.load_seconds

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
                    if await service.is_cancelled(job_id):
                        raise JobExecutionCancelled
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
            elif is_speech_vc:
                if not job.voice_profile_id or not job.input_storage_key:
                    raise ValueError("speech conversion input and voice profile required")
                profile = await session.scalar(
                    select(VoiceProfile).where(VoiceProfile.id == job.voice_profile_id)
                )
                if profile is None or profile.status != "READY":
                    raise ValueError("ready voice profile required")
                profile_data = profile.profile_metadata.get("model_profiles", {}).get(
                    job.model_key, {}
                )

                async def conversion_checkpoint(completed: int, total: int) -> None:
                    if await service.is_cancelled(job_id):
                        raise JobExecutionCancelled
                    tracked = await service.get_job(job_id, for_update=True)
                    tracked.progress = 40 + round(40 * completed / total)
                    tracked.metrics = {
                        **tracked.metrics,
                        "conversion_checkpoint": {
                            "completed": completed,
                            "total": total,
                        },
                    }
                    await session.commit()

                conversion = await VoiceConversionPipeline(
                    output_storage,
                    max_duration_seconds=settings.max_audio_duration_seconds,
                ).run(
                    job_id=job.id,
                    input_storage_key=job.input_storage_key,
                    model=cast(VoiceConversionModel, model),
                    voice_profile=profile_data,
                    on_checkpoint=conversion_checkpoint,
                )
                session.add(
                    JobOutput(
                        job_id=job.id,
                        storage_key=conversion.storage_key,
                        content_type="audio/wav",
                        duration_seconds=conversion.duration_seconds,
                        output_metadata={
                            "sample_rate": conversion.sample_rate,
                            "chunk_count": conversion.chunk_count,
                            "model": job.model_key,
                            "preserved": ["duration", "sample_rate", "timing"],
                        },
                    )
                )
                generated_storage_key = conversion.storage_key
                output_metadata = {
                    "input_duration": conversion.duration_seconds,
                    "output_duration": conversion.duration_seconds,
                    "chunk_count": conversion.chunk_count,
                    "sample_rate": conversion.sample_rate,
                    "preserved": ["duration", "sample_rate", "timing"],
                }
            elif is_singing_vc:
                if not job.voice_profile_id or not job.input_storage_key:
                    raise ValueError("singing conversion input and voice profile required")
                if separator is None:
                    raise RuntimeError("singing separator is not initialized")
                profile = await session.scalar(
                    select(VoiceProfile).where(VoiceProfile.id == job.voice_profile_id)
                )
                if profile is None or profile.status != "READY":
                    raise ValueError("ready voice profile required")
                profile_data = profile.profile_metadata.get("model_profiles", {}).get(
                    job.model_key, {}
                )

                async def singing_checkpoint(completed: int, total: int) -> None:
                    if await service.is_cancelled(job_id):
                        raise JobExecutionCancelled
                    tracked = await service.get_job(job_id, for_update=True)
                    tracked.progress = 40 + round(40 * completed / total)
                    tracked.metrics = {
                        **tracked.metrics,
                        "singing_checkpoint": {
                            "completed": completed,
                            "total": total,
                        },
                    }
                    await session.commit()

                singing = await SingingPipeline(
                    output_storage,
                    max_duration_seconds=settings.max_audio_duration_seconds,
                ).run(
                    job_id=job.id,
                    input_storage_key=job.input_storage_key,
                    separator=separator,
                    singing_model=cast(SingingVoiceModel, model),
                    voice_profile=profile_data,
                    on_checkpoint=singing_checkpoint,
                )
                preserved = [
                    "duration",
                    "sample_rate",
                    "channels",
                    "frame_count",
                    "timing",
                    "pitch",
                    "melody",
                ]
                session.add(
                    JobOutput(
                        job_id=job.id,
                        storage_key=singing.storage_key,
                        content_type="audio/wav",
                        duration_seconds=singing.duration_seconds,
                        output_metadata={
                            "sample_rate": singing.sample_rate,
                            "channels": singing.channels,
                            "chunk_count": singing.chunk_count,
                            "model": job.model_key,
                            "separator": separator.descriptor.key,
                            "preserved": preserved,
                            "manifest": singing.manifest,
                        },
                    )
                )
                generated_storage_key = singing.storage_key
                output_metadata = {
                    "input_duration": singing.duration_seconds,
                    "output_duration": singing.duration_seconds,
                    "chunk_count": singing.chunk_count,
                    "sample_rate": singing.sample_rate,
                    "channels": singing.channels,
                    "separator": separator.descriptor.key,
                    "preserved": preserved,
                    "intermediates_cleaned": singing.manifest["mixing"][
                        "intermediates_cleaned"
                    ],
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
            if not is_tts and not is_speech_vc and not is_singing_vc:
                cast(VoiceModel, model).postprocess(output)
            postprocess_time = time.monotonic() - post_started

            tracked_job = await service.get_job(job_id, for_update=True)
            tracked_job.metrics = {
                "input_duration": tracked_job.request_config.get("input_duration"),
                "model_loading_time": round(load_time, 6),
                "model_cache": model_cache_metrics,
                "gpu": model_cache_metrics[-1]["gpu"] if model_cache_metrics else {},
                "inference_time": round(inference_time, 6),
                "postprocessing_time": round(postprocess_time, 6),
                "processing_time": round(time.monotonic() - started, 6),
                "model": tracked_job.model_key,
                **(
                    {"tts_checkpoint": tracked_job.metrics.get("tts_checkpoint")}
                    if is_tts
                    else {}
                ),
                **(
                    {
                        "conversion_checkpoint": tracked_job.metrics.get(
                            "conversion_checkpoint"
                        )
                    }
                    if is_speech_vc
                    else {}
                ),
                **(
                    {
                        "singing_checkpoint": tracked_job.metrics.get(
                            "singing_checkpoint"
                        )
                    }
                    if is_singing_vc
                    else {}
                ),
                **output_metadata,
            }
            await service.transition(job_id, JobStatus.COMPLETED, progress=100)
            return {"job_id": job_id, "status": JobStatus.COMPLETED.value}
    except JobExecutionCancelled:
        if generated_storage_key is not None:
            await output_storage.delete(generated_storage_key)
        return {"job_id": job_id, "status": JobStatus.CANCELLED.value}
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
        is_oom = manager.is_out_of_memory(error)
        oom_recovery: dict[str, object] | None = None
        if is_oom:
            lease_stack.close()
            snapshot = manager.recover_after_oom(active_cache_keys)
            oom_recovery = {
                "recovered": True,
                "device": snapshot.to_dict(),
            }
        log.exception(
            "worker_task_failed",
            job_id=job_id,
            error_type=type(error).__name__,
            cuda_oom=is_oom,
        )
        try:
            if generated_storage_key is not None:
                await output_storage.delete(generated_storage_key)
            async with session_factory() as recovery_session:
                await recovery_session.execute(
                    delete(JobOutput).where(JobOutput.job_id == job_id)
                )
                await recovery_session.commit()
                service = JobService(recovery_session, CeleryJobQueue())
                if oom_recovery is not None:
                    tracked = await service.get_job(job_id, for_update=True)
                    tracked.metrics = {
                        **tracked.metrics,
                        "oom_recovery": oom_recovery,
                        "model_cache": model_cache_metrics,
                    }
                    await recovery_session.commit()
                await service.fail_job(
                    job_id,
                    code="CUDA_OOM" if is_oom else _failure_code(error),
                    detail=type(error).__name__,
                )
        except Exception:
            log.exception("worker_failure_persistence_failed", job_id=job_id)
        return {"job_id": job_id, "status": JobStatus.FAILED.value}
    finally:
        lease_stack.close()
        await engine.dispose()


@celery_app.task(name="voice.run_inference", bind=True, acks_late=True)  # type: ignore[untyped-decorator]
def run_inference(self: Any, job_id: str) -> dict[str, str]:
    log.info("worker_task_started", job_id=job_id, worker_id=self.request.hostname)
    return asyncio.run(execute_job(job_id))

