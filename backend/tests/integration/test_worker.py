import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import RecordingQueue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.gpu import GPUSnapshot
from app.db import Base
from app.db.models import JobMode, JobOutput, JobStatus, VoiceProfile
from app.models.base import ModelCapability, ModelDescriptor
from app.models.manager import ModelManager
from app.models.tts import TTSAudio
from app.services.job_service import CreateJobCommand, JobService
from app.services.user_service import resolve_user
from app.storage import LocalObjectStorage
from app.workers.inference_worker import execute_job
from app.workers.model_runtime import reset_worker_model_manager


class SimulatedCudaOutOfMemoryError(RuntimeError):
    pass


class WorkerFakeRuntime:
    def __init__(self) -> None:
        self.empty_cache_calls = 0

    def probe(self, device: str) -> GPUSnapshot:
        return GPUSnapshot(
            device=device,
            available=True,
            name="Fake GPU",
            total_memory_mb=16_000,
            free_memory_mb=12_000,
        )

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, SimulatedCudaOutOfMemoryError)

    def empty_cache(self, device: str) -> None:
        self.empty_cache_calls += 1


class RecoveringTTSModel:
    descriptor = ModelDescriptor(
        key="mock-universal-v1",
        display_name="Recovering TTS",
        version="oom-test",
        capabilities=(ModelCapability.GENERAL_TTS,),
        requires_gpu=False,
        is_mock=True,
    )
    inference_attempts = 0

    def load(self) -> None:
        pass

    def synthesize(self, text: str, voice_profile: dict[str, Any]) -> TTSAudio:
        type(self).inference_attempts += 1
        if type(self).inference_attempts == 1:
            raise SimulatedCudaOutOfMemoryError("simulated CUDA out of memory")
        return TTSAudio(b"\x00\x00" * 2_400, 24_000)

    def unload(self) -> None:
        pass


class RecoveringTTSRegistry:
    def create(self, key: str) -> RecoveringTTSModel:
        assert key == "mock-universal-v1"
        return RecoveringTTSModel()


@pytest.mark.asyncio
async def test_mock_worker_completes_persisted_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = await resolve_user(session, "worker-user", create=True)
        assert user is not None
        profile = VoiceProfile(
            user_id=user.id,
            name="worker voice",
            status="READY",
            consent_version="2026-09-01",
            consented_at=datetime.now(UTC),
            profile_metadata={
                "model_profiles": {
                    "mock-universal-v1": {"source_sha256": "worker-profile-sha"}
                }
            },
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        service = JobService(session, RecordingQueue())
        job, _ = await service.create_job(
            "worker-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                voice_profile_id=profile.id,
                input_text="worker contract",
            ),
            idempotency_key="worker-request-1",
        )
        cached_job, _ = await service.create_job(
            "worker-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                voice_profile_id=profile.id,
                input_text="worker cache contract",
            ),
            idempotency_key="worker-request-2",
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("USE_MOCK_INFERENCE", "true")
    get_settings.cache_clear()
    reset_worker_model_manager()
    result = await execute_job(job.id)
    cached_result = await execute_job(cached_job.id)
    get_settings.cache_clear()

    async with factory() as session:
        completed = await JobService(session, RecordingQueue()).get_job(job.id)
        cached = await JobService(session, RecordingQueue()).get_job(cached_job.id)
        assert result["status"] == JobStatus.COMPLETED.value
        assert cached_result["status"] == JobStatus.COMPLETED.value
        assert completed.status == JobStatus.COMPLETED
        assert completed.progress == 100
        assert completed.metrics["model"] == "mock-universal-v1"
        assert completed.metrics["processing_time"] >= 0
        assert completed.metrics["chunk_count"] == 1
        assert completed.metrics["model_cache"][0]["cache_hit"] is False
        assert cached.metrics["model_cache"][0]["cache_hit"] is True
        output = await session.scalar(select(JobOutput).where(JobOutput.job_id == job.id))
        assert output is not None
        assert output.content_type == "audio/wav"
        assert output.duration_seconds is not None and output.duration_seconds > 0
        output_path = tmp_path / "storage" / output.storage_key
        assert output_path.read_bytes().startswith(b"RIFF")
    reset_worker_model_manager()
    await engine.dispose()


@pytest.mark.asyncio
async def test_mock_worker_completes_singing_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'singing-worker.db'}"
    storage_path = tmp_path / "storage"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = await resolve_user(session, "singing-worker-user", create=True)
        assert user is not None
        profile = VoiceProfile(
            user_id=user.id,
            name="singing voice",
            status="READY",
            consent_version="2026-09-01",
            consented_at=datetime.now(UTC),
            profile_metadata={
                "model_profiles": {
                    "mock-universal-v1": {"source_sha256": "singing-profile-sha"}
                }
            },
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)

        source = tmp_path / "singing.wav"
        samples = array("h", [12_000, -12_000] * 8_000)
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(8_000)
            stream.writeframes(samples.tobytes())
        input_key = await LocalObjectStorage(storage_path).put(
            source,
            namespace=f"users/{user.id}/inputs/cleaned",
            suffix="wav",
        )
        service = JobService(session, RecordingQueue())
        job, _ = await service.create_job(
            "singing-worker-user",
            CreateJobCommand(
                mode=JobMode.SINGING_VOICE_CONVERSION,
                model_key="mock-universal-v1",
                voice_profile_id=profile.id,
                input_storage_key=input_key,
                request_config={"input_duration": 1.0},
            ),
            idempotency_key="singing-worker-request-1",
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("STORAGE_PATH", str(storage_path))
    monkeypatch.setenv("USE_MOCK_INFERENCE", "true")
    get_settings.cache_clear()
    reset_worker_model_manager()
    result = await execute_job(job.id)
    get_settings.cache_clear()

    async with factory() as session:
        completed = await JobService(session, RecordingQueue()).get_job(job.id)
        output = await session.scalar(select(JobOutput).where(JobOutput.job_id == job.id))
        assert result["status"] == JobStatus.COMPLETED.value
        assert completed.status == JobStatus.COMPLETED
        assert completed.metrics["singing_checkpoint"] == {"completed": 1, "total": 1}
        assert completed.metrics["separator"] == "mock-separator-v1"
        assert completed.metrics["intermediates_cleaned"] is True
        assert output is not None
        assert output.output_metadata["channels"] == 2
        assert output.output_metadata["manifest"]["vocal"]["retained"] is False
        output_path = storage_path / output.storage_key
        with wave.open(str(output_path), "rb") as stream:
            assert stream.getnchannels() == 2
            assert stream.getnframes() == 8_000
        assert sorted(path.suffix for path in storage_path.rglob("*.wav")) == [
            ".wav",
            ".wav",
        ]
    reset_worker_model_manager()
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_recovers_from_inference_oom_and_runs_next_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'oom-worker.db'}"
    storage_path = tmp_path / "storage"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = await resolve_user(session, "oom-worker-user", create=True)
        assert user is not None
        profile = VoiceProfile(
            user_id=user.id,
            name="oom voice",
            status="READY",
            consent_version="2026-09-01",
            consented_at=datetime.now(UTC),
            profile_metadata={
                "model_profiles": {
                    "mock-universal-v1": {"source_sha256": "oom-profile-sha"}
                }
            },
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        service = JobService(session, RecordingQueue())
        failed_job, _ = await service.create_job(
            "oom-worker-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                voice_profile_id=profile.id,
                input_text="first job should exhaust memory",
            ),
            idempotency_key="oom-worker-1",
        )
        healthy_job, _ = await service.create_job(
            "oom-worker-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                voice_profile_id=profile.id,
                input_text="second job should recover",
            ),
            idempotency_key="oom-worker-2",
        )

    runtime = WorkerFakeRuntime()
    manager = ModelManager(
        device="cuda:0",
        max_cached_models=2,
        vram_reserve_mb=512,
        gpu_runtime=runtime,
    )
    RecoveringTTSModel.inference_attempts = 0
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("STORAGE_PATH", str(storage_path))
    monkeypatch.setenv("USE_MOCK_INFERENCE", "true")
    monkeypatch.setattr(
        "app.workers.inference_worker.get_worker_model_manager",
        lambda settings: manager,
    )
    monkeypatch.setattr(
        "app.workers.inference_worker.build_tts_model_registry",
        lambda include_mock: RecoveringTTSRegistry(),
    )
    get_settings.cache_clear()

    failed_result = await execute_job(failed_job.id)
    healthy_result = await execute_job(healthy_job.id)
    get_settings.cache_clear()

    async with factory() as session:
        service = JobService(session, RecordingQueue())
        failed = await service.get_job(failed_job.id)
        healthy = await service.get_job(healthy_job.id)
        assert failed_result["status"] == JobStatus.FAILED.value
        assert failed.status == JobStatus.FAILED
        assert failed.error_code == "CUDA_OOM"
        assert failed.metrics["oom_recovery"]["recovered"] is True
        assert healthy_result["status"] == JobStatus.COMPLETED.value
        assert healthy.status == JobStatus.COMPLETED
        assert runtime.empty_cache_calls >= 1
    manager.shutdown()
    await engine.dispose()

