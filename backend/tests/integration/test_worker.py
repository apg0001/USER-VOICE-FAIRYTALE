import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import RecordingQueue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import Base
from app.db.models import JobMode, JobOutput, JobStatus, VoiceProfile
from app.services.job_service import CreateJobCommand, JobService
from app.services.user_service import resolve_user
from app.storage import LocalObjectStorage
from app.workers.inference_worker import execute_job


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

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("USE_MOCK_INFERENCE", "true")
    get_settings.cache_clear()
    result = await execute_job(job.id)
    get_settings.cache_clear()

    async with factory() as session:
        completed = await JobService(session, RecordingQueue()).get_job(job.id)
        assert result["status"] == JobStatus.COMPLETED.value
        assert completed.status == JobStatus.COMPLETED
        assert completed.progress == 100
        assert completed.metrics["model"] == "mock-universal-v1"
        assert completed.metrics["processing_time"] >= 0
        assert completed.metrics["chunk_count"] == 1
        output = await session.scalar(select(JobOutput).where(JobOutput.job_id == job.id))
        assert output is not None
        assert output.content_type == "audio/wav"
        assert output.duration_seconds is not None and output.duration_seconds > 0
        output_path = tmp_path / "storage" / output.storage_key
        assert output_path.read_bytes().startswith(b"RIFF")
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
    await engine.dispose()

