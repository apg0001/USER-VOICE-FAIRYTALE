from pathlib import Path

import pytest
from conftest import RecordingQueue
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import Base
from app.db.models import JobMode, JobStatus
from app.services.job_service import CreateJobCommand, JobService
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
        service = JobService(session, RecordingQueue())
        job, _ = await service.create_job(
            "worker-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                input_text="worker contract",
            ),
            idempotency_key="worker-request-1",
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
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
    await engine.dispose()

