import asyncio
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InputArtifact, Job, JobMode, JobStatus, VoiceProfile
from app.queue import JobQueue
from app.services.user_service import resolve_user


class JobServiceError(Exception):
    pass


class JobNotFoundError(JobServiceError):
    pass


class InvalidJobTransitionError(JobServiceError):
    pass


class JobNotRetryableError(JobServiceError):
    pass


class QueueUnavailableError(JobServiceError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"queue dispatch failed for job {job_id}")


class InvalidVoiceProfileError(JobServiceError):
    pass


class InvalidInputFileError(JobServiceError):
    pass


TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.PREPROCESSING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.PREPROCESSING: {
        JobStatus.LOADING_MODEL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.LOADING_MODEL: {
        JobStatus.INFERENCE,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.INFERENCE: {
        JobStatus.POSTPROCESSING,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.POSTPROCESSING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

STAGE_PROGRESS_RANGE: dict[JobStatus, tuple[int, int]] = {
    JobStatus.QUEUED: (0, 0),
    JobStatus.PREPROCESSING: (1, 29),
    JobStatus.LOADING_MODEL: (30, 39),
    JobStatus.INFERENCE: (40, 84),
    JobStatus.POSTPROCESSING: (85, 99),
    JobStatus.COMPLETED: (100, 100),
    JobStatus.FAILED: (0, 100),
    JobStatus.CANCELLED: (0, 100),
}


@dataclass(slots=True)
class CreateJobCommand:
    mode: JobMode
    model_key: str
    voice_profile_id: str | None = None
    input_text: str | None = None
    input_storage_key: str | None = None
    request_config: dict[str, Any] = field(default_factory=dict)
    retry_of_job_id: str | None = None
    attempt: int = 1


@dataclass(slots=True)
class JobPage:
    items: list[Job]
    total: int


class JobService:
    def __init__(self, session: AsyncSession, queue: JobQueue) -> None:
        self.session = session
        self.queue = queue

    async def create_job(
        self,
        external_user_id: str,
        command: CreateJobCommand,
        *,
        idempotency_key: str | None,
    ) -> tuple[Job, bool]:
        user = await resolve_user(self.session, external_user_id, create=True)
        assert user is not None
        user_id = user.id

        if command.voice_profile_id:
            profile = await self.session.scalar(
                select(VoiceProfile).where(
                    VoiceProfile.id == command.voice_profile_id,
                    VoiceProfile.user_id == user_id,
                )
            )
            if profile is None or profile.status != "READY":
                raise InvalidVoiceProfileError(command.voice_profile_id)

        if command.mode in {
            JobMode.SPEECH_VOICE_CONVERSION,
            JobMode.SINGING_VOICE_CONVERSION,
        }:
            artifact = (
                await self.session.scalar(
                    select(InputArtifact).where(
                        InputArtifact.user_id == user_id,
                        InputArtifact.cleaned_storage_key == command.input_storage_key,
                        InputArtifact.expires_at > datetime.now(UTC),
                    )
                )
                if command.input_storage_key
                else None
            )
            if artifact is None:
                raise InvalidInputFileError(command.input_storage_key or "")

        if idempotency_key:
            existing = await self.session.scalar(
                select(Job).where(
                    Job.user_id == user_id,
                    Job.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing, False

        estimate = await self._estimate_seconds(command)
        job = Job(
            user_id=user_id,
            voice_profile_id=command.voice_profile_id,
            mode=command.mode,
            status=JobStatus.QUEUED,
            progress=0,
            estimated_remaining_seconds=estimate,
            input_storage_key=command.input_storage_key,
            input_text=command.input_text,
            model_key=command.model_key,
            idempotency_key=idempotency_key,
            retry_of_job_id=command.retry_of_job_id,
            attempt=command.attempt,
            request_config=command.request_config,
            metrics={},
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if not idempotency_key:
                raise
            existing = await self.session.scalar(
                select(Job).where(
                    Job.user_id == user_id,
                    Job.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing, False
        await self.session.refresh(job)

        try:
            task_id = await asyncio.to_thread(self.queue.enqueue, job.id)
        except Exception as error:
            job.status = JobStatus.FAILED
            job.error_code = "QUEUE_UNAVAILABLE"
            job.error_detail = type(error).__name__
            job.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise QueueUnavailableError(job.id) from error

        job.celery_task_id = task_id
        await self.session.commit()
        await self.session.refresh(job)
        return job, True

    async def get_owned_job(self, external_user_id: str, job_id: str) -> Job:
        user = await resolve_user(self.session, external_user_id, create=False)
        if user is None:
            raise JobNotFoundError(job_id)
        job = await self.session.scalar(
            select(Job).where(Job.id == job_id, Job.user_id == user.id)
        )
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    async def get_job(self, job_id: str, *, for_update: bool = False) -> Job:
        statement = select(Job).where(Job.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        job = await self.session.scalar(statement)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    async def list_owned_jobs(
        self,
        external_user_id: str,
        *,
        status: JobStatus | None,
        limit: int,
        offset: int,
    ) -> JobPage:
        user = await resolve_user(self.session, external_user_id, create=False)
        if user is None:
            return JobPage(items=[], total=0)
        filters = [Job.user_id == user.id]
        if status is not None:
            filters.append(Job.status == status)
        total = await self.session.scalar(select(func.count(Job.id)).where(*filters))
        items = list(
            await self.session.scalars(
                select(Job)
                .where(*filters)
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return JobPage(items=items, total=total or 0)

    async def queue_position(self, job: Job) -> int | None:
        if job.status != JobStatus.QUEUED:
            return None
        ahead = await self.session.scalar(
            select(func.count(Job.id)).where(
                Job.id != job.id,
                Job.status == JobStatus.QUEUED,
                or_(
                    Job.created_at < job.created_at,
                    and_(Job.created_at == job.created_at, Job.id < job.id),
                ),
            )
        )
        return (ahead or 0) + 1

    async def cancel_job(self, external_user_id: str, job_id: str) -> Job:
        job = await self.get_owned_job(external_user_id, job_id)
        if job.status in TERMINAL_STATUSES:
            if job.status == JobStatus.CANCELLED:
                return job
            raise InvalidJobTransitionError(f"cannot cancel {job.status}")
        job.cancel_requested = True
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        await self.session.commit()
        if job.celery_task_id:
            await asyncio.to_thread(self.queue.revoke, job.celery_task_id)
        await self.session.refresh(job)
        return job

    async def retry_job(self, external_user_id: str, job_id: str) -> Job:
        source = await self.get_owned_job(external_user_id, job_id)
        if source.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise JobNotRetryableError(source.status)
        command = CreateJobCommand(
            mode=source.mode,
            model_key=source.model_key,
            voice_profile_id=source.voice_profile_id,
            input_text=source.input_text,
            input_storage_key=source.input_storage_key,
            request_config=source.request_config,
            retry_of_job_id=source.id,
            attempt=source.attempt + 1,
        )
        job, _ = await self.create_job(external_user_id, command, idempotency_key=None)
        return job

    async def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        progress: int,
    ) -> Job:
        job = await self.get_job(job_id, for_update=True)
        if job.cancel_requested or job.status == JobStatus.CANCELLED:
            if job.status != JobStatus.CANCELLED:
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now(UTC)
                await self.session.commit()
            return job
        if target not in ALLOWED_TRANSITIONS[job.status]:
            raise InvalidJobTransitionError(f"{job.status} -> {target}")
        low, high = STAGE_PROGRESS_RANGE[target]
        if not low <= progress <= high or progress < job.progress:
            raise InvalidJobTransitionError(f"invalid progress {progress} for {target}")
        job.status = target
        job.progress = progress
        if target == JobStatus.PREPROCESSING and job.started_at is None:
            job.started_at = datetime.now(UTC)
        if target in TERMINAL_STATUSES:
            job.finished_at = datetime.now(UTC)
            job.estimated_remaining_seconds = 0
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def fail_job(self, job_id: str, *, code: str, detail: str) -> Job:
        job = await self.get_job(job_id, for_update=True)
        if job.status in TERMINAL_STATUSES:
            return job
        job.status = JobStatus.FAILED
        job.error_code = code
        job.error_detail = detail[:2000]
        job.finished_at = datetime.now(UTC)
        job.estimated_remaining_seconds = 0
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def is_cancelled(self, job_id: str) -> bool:
        self.session.expire_all()
        job = await self.get_job(job_id)
        return job.cancel_requested or job.status == JobStatus.CANCELLED

    async def _estimate_seconds(self, command: CreateJobCommand) -> int | None:
        input_duration = command.request_config.get("input_duration")
        if not isinstance(input_duration, int | float) or input_duration <= 0:
            return None
        completed = list(
            await self.session.scalars(
                select(Job)
                .where(
                    Job.status == JobStatus.COMPLETED,
                    Job.mode == command.mode,
                    Job.model_key == command.model_key,
                )
                .order_by(Job.finished_at.desc())
                .limit(50)
            )
        )
        factors = []
        for job in completed:
            historical_duration = job.metrics.get("input_duration")
            processing_time = job.metrics.get("processing_time")
            if (
                isinstance(historical_duration, int | float)
                and historical_duration > 0
                and isinstance(processing_time, int | float)
                and processing_time > 0
            ):
                factors.append(processing_time / historical_duration)
        if len(factors) < 3:
            return None
        return max(1, round(float(input_duration) * statistics.median(factors)))

