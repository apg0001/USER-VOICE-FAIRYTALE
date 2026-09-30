import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import RecordingQueue

from app.db import Base
from app.db.models import InputArtifact, Job, JobMode, JobOutput, JobStatus, User
from app.db.session import create_session_factory
from app.services.cleanup_service import CleanupService
from app.storage import LocalObjectStorage, ObjectStorage, StoredObject


class FlakyStorage(ObjectStorage):
    def __init__(self, delegate: LocalObjectStorage) -> None:
        self.delegate = delegate
        self.fail = True

    async def put(self, source: Path, *, namespace: str, suffix: str) -> str:
        return await self.delegate.put(source, namespace=namespace, suffix=suffix)

    def open(self, key: str) -> AsyncIterator[bytes]:
        return self.delegate.open(key)

    async def delete(self, key: str) -> None:
        if self.fail:
            raise OSError("simulated storage outage")
        await self.delegate.delete(key)

    async def healthcheck(self) -> None:
        await self.delegate.healthcheck()

    async def list_objects(self, prefix: str) -> list[StoredObject]:
        return await self.delegate.list_objects(prefix)


async def put(storage: ObjectStorage, source: Path, namespace: str) -> str:
    return await storage.put(source, namespace=namespace, suffix="wav")


@pytest.mark.asyncio
async def test_expired_cleanup_retries_storage_failure_without_losing_metadata(
    tmp_path: Path,
) -> None:
    engine, factory = create_session_factory(
        f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFFcleanup")
    local = LocalObjectStorage(tmp_path / "objects")
    storage = FlakyStorage(local)
    original = await put(storage, source, "users/a/inputs/original")
    cleaned = await put(storage, source, "users/a/inputs/cleaned")

    async with factory() as session:
        user = User(external_id="cleanup-owner", is_active=True)
        session.add(user)
        await session.flush()
        artifact = InputArtifact(
            user_id=user.id,
            kind="speech",
            original_storage_key=original,
            cleaned_storage_key=cleaned,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(artifact)
        await session.commit()

        first = await CleanupService(session, storage, RecordingQueue()).cleanup_expired()
        await session.refresh(artifact)
        assert first.failures == 1
        assert artifact.deletion_attempts == 1

        storage.fail = False
        second = await CleanupService(session, storage, RecordingQueue()).cleanup_expired()
        assert second.inputs_deleted == 1
        assert await session.get(InputArtifact, artifact.id) is None
        with pytest.raises(FileNotFoundError):
            _ = b"".join([chunk async for chunk in storage.open(cleaned)])
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_user_deletion_removes_only_owned_objects_and_rows(tmp_path: Path) -> None:
    engine, factory = create_session_factory(
        f"sqlite+aiosqlite:///{tmp_path / 'delete-user.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFFaccount")
    storage = LocalObjectStorage(tmp_path / "objects")

    async with factory() as session:
        alice = User(external_id="alice", is_active=True)
        bob = User(external_id="bob", is_active=True)
        session.add_all([alice, bob])
        await session.flush()
        alice_original = await put(storage, source, "users/alice/inputs/original")
        alice_cleaned = await put(storage, source, "users/alice/inputs/cleaned")
        bob_original = await put(storage, source, "users/bob/inputs/original")
        bob_cleaned = await put(storage, source, "users/bob/inputs/cleaned")
        session.add_all(
            [
                InputArtifact(
                    user_id=alice.id,
                    kind="speech",
                    original_storage_key=alice_original,
                    cleaned_storage_key=alice_cleaned,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
                InputArtifact(
                    user_id=bob.id,
                    kind="speech",
                    original_storage_key=bob_original,
                    cleaned_storage_key=bob_cleaned,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        )
        job = Job(
            user_id=alice.id,
            mode=JobMode.GENERAL_TTS,
            status=JobStatus.COMPLETED,
            progress=100,
            input_text="sensitive text",
            model_key="mock-universal-v1",
            request_config={},
            metrics={},
        )
        session.add(job)
        await session.flush()
        output_key = await put(storage, source, "jobs/alice/outputs")
        session.add(
            JobOutput(
                job_id=job.id,
                storage_key=output_key,
                content_type="audio/wav",
                output_metadata={},
            )
        )
        await session.commit()

        deleted = await CleanupService(
            session, storage, RecordingQueue()
        ).request_user_deletion("alice")
        assert deleted is True
        assert await session.get(User, alice.id) is None
        assert await session.get(User, bob.id) is not None
        with pytest.raises(FileNotFoundError):
            _ = b"".join([chunk async for chunk in storage.open(alice_cleaned)])
        with pytest.raises(FileNotFoundError):
            _ = b"".join([chunk async for chunk in storage.open(output_key)])
        assert b"".join([chunk async for chunk in storage.open(bob_cleaned)]) == b"RIFFaccount"
    await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_skips_active_input_and_reconciles_only_old_known_orphans(
    tmp_path: Path,
) -> None:
    engine, factory = create_session_factory(
        f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFFreconcile")
    storage = LocalObjectStorage(tmp_path / "objects")
    now = datetime.now(UTC)

    async with factory() as session:
        user = User(external_id="active-owner", is_active=True)
        session.add(user)
        await session.flush()
        original = await put(storage, source, "users/active/inputs/original")
        cleaned = await put(storage, source, "users/active/inputs/cleaned")
        session.add(
            InputArtifact(
                user_id=user.id,
                kind="speech",
                original_storage_key=original,
                cleaned_storage_key=cleaned,
                expires_at=now - timedelta(minutes=1),
            )
        )
        session.add(
            Job(
                user_id=user.id,
                mode=JobMode.SPEECH_VOICE_CONVERSION,
                status=JobStatus.INFERENCE,
                progress=50,
                input_storage_key=cleaned,
                model_key="mock-universal-v1",
                request_config={},
                metrics={},
            )
        )
        await session.commit()

        old_orphan = await put(storage, source, "jobs/orphan/outputs")
        recent_orphan = await put(storage, source, "jobs/recent/outputs")
        old_timestamp = (now - timedelta(hours=3)).timestamp()
        os.utime(storage._safe_path(old_orphan), (old_timestamp, old_timestamp))

        report = await CleanupService(
            session,
            storage,
            RecordingQueue(),
            orphan_grace_hours=2,
        ).cleanup_expired(now=now)
        assert report.skipped_active == 1
        assert report.orphans_deleted == 1
        assert b"".join([chunk async for chunk in storage.open(cleaned)]) == b"RIFFreconcile"
        with pytest.raises(FileNotFoundError):
            _ = b"".join([chunk async for chunk in storage.open(old_orphan)])
        assert b"".join([chunk async for chunk in storage.open(recent_orphan)]) == b"RIFFreconcile"
    await engine.dispose()
