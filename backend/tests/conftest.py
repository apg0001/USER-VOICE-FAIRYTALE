import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import Base
from app.main import create_app


@dataclass
class RecordingQueue:
    enqueued: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    def enqueue(self, job_id: str) -> str:
        self.enqueued.append(job_id)
        return job_id

    def revoke(self, task_id: str) -> None:
        self.revoked.append(task_id)


async def create_test_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        storage_path=tmp_path / "storage",
        model_path=tmp_path / "models",
        use_mock_inference=True,
        sse_poll_interval_seconds=0.01,
        sse_heartbeat_seconds=0.02,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def recording_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def job_client(tmp_path, recording_queue: RecordingQueue) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"
    asyncio.run(create_test_schema(database_url))
    settings = Settings(
        app_env="test",
        database_url=database_url,
        storage_path=tmp_path / "storage",
        model_path=tmp_path / "models",
        use_mock_inference=True,
        sse_poll_interval_seconds=0.01,
        sse_heartbeat_seconds=0.02,
    )
    with TestClient(create_app(settings, job_queue=recording_queue)) as test_client:
        yield test_client
