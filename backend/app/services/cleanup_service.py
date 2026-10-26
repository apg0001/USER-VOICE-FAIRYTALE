import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    InputArtifact,
    Job,
    JobOutput,
    JobStatus,
    User,
    VoiceProfile,
    VoiceSample,
)
from app.queue import JobQueue
from app.services.job_service import TERMINAL_STATUSES
from app.storage import ObjectStorage

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class CleanupReport:
    inputs_deleted: int = 0
    outputs_deleted: int = 0
    profiles_deleted: int = 0
    users_deleted: int = 0
    orphans_deleted: int = 0
    skipped_active: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "inputs_deleted": self.inputs_deleted,
            "outputs_deleted": self.outputs_deleted,
            "profiles_deleted": self.profiles_deleted,
            "users_deleted": self.users_deleted,
            "orphans_deleted": self.orphans_deleted,
            "skipped_active": self.skipped_active,
            "failures": self.failures,
        }


class CleanupService:
    """Idempotent storage cleanup; metadata remains when object deletion fails."""

    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        queue: JobQueue,
        *,
        batch_size: int = 100,
        output_retention_hours: int = 168,
        orphan_grace_hours: int = 2,
    ) -> None:
        self.session = session
        self.storage = storage
        self.queue = queue
        self.batch_size = batch_size
        self.output_retention_hours = output_retention_hours
        self.orphan_grace_hours = orphan_grace_hours

    async def cleanup_expired(self, *, now: datetime | None = None) -> CleanupReport:
        cutoff = now or datetime.now(UTC)
        report = CleanupReport()
        await self._cleanup_inputs(cutoff, report)
        await self._cleanup_outputs(cutoff, report)
        await self._cleanup_pending_profiles(report)
        await self._cleanup_pending_users(report)
        await self._reconcile_orphans(cutoff, report)
        log.info("retention_cleanup_completed", **report.to_dict())
        return report

    async def request_user_deletion(self, external_user_id: str) -> bool:
        import hashlib

        actor_hash = hashlib.sha256(external_user_id.encode()).hexdigest()[:16]
        log.info("user_data_deletion_requested", actor_hash=actor_hash)
        user = await self.session.scalar(select(User).where(User.external_id == external_user_id))
        if user is None:
            log.info("user_data_deletion_completed", actor_hash=actor_hash, already_absent=True)
            return True
        user.is_active = False
        user.deletion_requested_at = datetime.now(UTC)
        active_jobs = list(
            await self.session.scalars(
                select(Job).where(
                    Job.user_id == user.id,
                    Job.status.not_in(TERMINAL_STATUSES),
                )
            )
        )
        for job in active_jobs:
            job.cancel_requested = True
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
        await self.session.commit()
        for job in active_jobs:
            if job.celery_task_id:
                try:
                    await asyncio.to_thread(self.queue.revoke, job.celery_task_id)
                except Exception as error:
                    log.warning(
                        "user_deletion_queue_revoke_failed",
                        job_id=job.id,
                        error_type=type(error).__name__,
                    )
        deleted = await self._purge_user(user)
        log.info(
            "user_data_deletion_completed" if deleted else "user_data_deletion_pending",
            actor_hash=actor_hash,
        )
        return deleted

    async def _cleanup_inputs(self, cutoff: datetime, report: CleanupReport) -> None:
        artifacts = list(
            await self.session.scalars(
                select(InputArtifact)
                .where(InputArtifact.expires_at <= cutoff)
                .order_by(InputArtifact.expires_at)
                .limit(self.batch_size)
            )
        )
        for artifact in artifacts:
            active_references = await self.session.scalar(
                select(func.count(Job.id)).where(
                    Job.input_storage_key == artifact.cleaned_storage_key,
                    Job.status.not_in(TERMINAL_STATUSES),
                )
            )
            if active_references:
                report.skipped_active += 1
                continue
            if await self._delete_keys(
                [artifact.cleaned_storage_key, artifact.original_storage_key]
            ):
                await self.session.delete(artifact)
                report.inputs_deleted += 1
            else:
                artifact.deletion_attempts += 1
                artifact.last_error = "storage_delete_failed"
                report.failures += 1
            await self.session.commit()

    async def _cleanup_outputs(self, cutoff: datetime, report: CleanupReport) -> None:
        outputs = list(
            await self.session.scalars(
                select(JobOutput)
                .where(
                    (JobOutput.expires_at.is_not(None) & (JobOutput.expires_at <= cutoff))
                    | (
                        JobOutput.expires_at.is_(None)
                        & (
                            JobOutput.created_at
                            <= cutoff - timedelta(hours=self.output_retention_hours)
                        )
                    )
                )
                .order_by(JobOutput.expires_at)
                .limit(self.batch_size)
            )
        )
        for output in outputs:
            if await self._delete_keys([output.storage_key]):
                await self.session.delete(output)
                report.outputs_deleted += 1
            else:
                cleanup = output.output_metadata.get("cleanup", {})
                attempts = int(cleanup.get("attempts", 0)) + 1
                output.output_metadata = {
                    **output.output_metadata,
                    "cleanup": {"attempts": attempts, "last_error": "storage_delete_failed"},
                }
                report.failures += 1
            await self.session.commit()

    async def _reconcile_orphans(self, now: datetime, report: CleanupReport) -> None:
        tracked = set(await self.session.scalars(select(InputArtifact.original_storage_key)))
        tracked.update(await self.session.scalars(select(InputArtifact.cleaned_storage_key)))
        tracked.update(await self.session.scalars(select(VoiceSample.original_storage_key)))
        tracked.update(
            key
            for key in await self.session.scalars(select(VoiceSample.cleaned_storage_key))
            if key
        )
        tracked.update(await self.session.scalars(select(JobOutput.storage_key)))
        grace_cutoff = now - timedelta(hours=self.orphan_grace_hours)
        candidates = []
        for prefix in ("users", "voice-profiles", "jobs"):
            candidates.extend(await self.storage.list_objects(prefix))
        orphans = [
            item
            for item in candidates
            if item.key not in tracked and item.modified_at <= grace_cutoff
        ][: self.batch_size]
        for item in orphans:
            if await self._delete_keys([item.key]):
                report.orphans_deleted += 1
            else:
                report.failures += 1

    async def _cleanup_pending_profiles(self, report: CleanupReport) -> None:
        profiles = list(
            await self.session.scalars(
                select(VoiceProfile)
                .where(VoiceProfile.status == "DELETION_PENDING")
                .limit(self.batch_size)
            )
        )
        for profile in profiles:
            samples = list(
                await self.session.scalars(
                    select(VoiceSample).where(VoiceSample.voice_profile_id == profile.id)
                )
            )
            keys = [
                key
                for sample in samples
                for key in (sample.cleaned_storage_key, sample.original_storage_key)
                if key
            ]
            if await self._delete_keys(keys):
                await self.session.delete(profile)
                await self.session.commit()
                report.profiles_deleted += 1
            else:
                report.failures += 1

    async def _cleanup_pending_users(self, report: CleanupReport) -> None:
        users = list(
            await self.session.scalars(
                select(User).where(User.deletion_requested_at.is_not(None)).limit(self.batch_size)
            )
        )
        for user in users:
            if await self._purge_user(user):
                report.users_deleted += 1
            else:
                report.failures += 1

    async def _purge_user(self, user: User) -> bool:
        samples = list(
            await self.session.scalars(
                select(VoiceSample)
                .join(VoiceProfile, VoiceProfile.id == VoiceSample.voice_profile_id)
                .where(VoiceProfile.user_id == user.id)
            )
        )
        inputs = list(
            await self.session.scalars(
                select(InputArtifact).where(InputArtifact.user_id == user.id)
            )
        )
        outputs = list(
            await self.session.scalars(
                select(JobOutput)
                .join(Job, Job.id == JobOutput.job_id)
                .where(Job.user_id == user.id)
            )
        )
        keys = [
            *(
                key
                for sample in samples
                for key in (sample.cleaned_storage_key, sample.original_storage_key)
                if key
            ),
            *(
                key
                for item in inputs
                for key in (item.cleaned_storage_key, item.original_storage_key)
            ),
            *(output.storage_key for output in outputs),
        ]
        if not await self._delete_keys(keys):
            return False
        await self.session.delete(user)
        await self.session.commit()
        return True

    async def _delete_keys(self, keys: list[str]) -> bool:
        succeeded = True
        for key in dict.fromkeys(keys):
            try:
                await self.storage.delete(key)
            except Exception as error:
                succeeded = False
                log.warning(
                    "storage_object_delete_failed",
                    key_digest=self._key_digest(key),
                    error_type=type(error).__name__,
                )
        return succeeded

    @staticmethod
    def _key_digest(key: str) -> str:
        import hashlib

        return hashlib.sha256(key.encode()).hexdigest()[:16]
