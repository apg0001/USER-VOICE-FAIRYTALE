from pathlib import Path

import pytest
from conftest import RecordingQueue
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.models import JobMode, JobStatus
from app.services.job_service import (
    CreateJobCommand,
    InvalidJobTransitionError,
    JobService,
)


@pytest.mark.asyncio
async def test_state_machine_requires_ordered_monotonic_progress(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        service = JobService(session, RecordingQueue())
        job, _ = await service.create_job(
            "state-user",
            CreateJobCommand(
                mode=JobMode.GENERAL_TTS,
                model_key="mock-universal-v1",
                input_text="hello",
            ),
            idempotency_key="state-request-1",
        )

        with pytest.raises(InvalidJobTransitionError):
            await service.transition(job.id, JobStatus.INFERENCE, progress=50)

        await service.transition(job.id, JobStatus.PREPROCESSING, progress=10)
        await service.transition(job.id, JobStatus.LOADING_MODEL, progress=30)
        await service.transition(job.id, JobStatus.INFERENCE, progress=50)
        await service.transition(job.id, JobStatus.POSTPROCESSING, progress=90)
        completed = await service.transition(job.id, JobStatus.COMPLETED, progress=100)

        assert completed.status == JobStatus.COMPLETED
        assert completed.progress == 100
        assert completed.started_at is not None
        assert completed.finished_at is not None
    await engine.dispose()
