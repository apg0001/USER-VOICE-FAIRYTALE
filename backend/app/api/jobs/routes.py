from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_actor_id
from app.api.jobs.schemas import JobCreateRequest, JobListResponse, JobResponse
from app.db.models import Job, JobStatus
from app.db.session import get_db_session
from app.models.base import ModelCapability
from app.queue import JobQueue
from app.services.job_service import (
    CreateJobCommand,
    InvalidJobTransitionError,
    InvalidVoiceProfileError,
    JobNotFoundError,
    JobNotRetryableError,
    JobService,
    QueueUnavailableError,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

MODE_CAPABILITY = {
    "general_tts": ModelCapability.GENERAL_TTS,
    "long_form_tts": ModelCapability.LONG_FORM_TTS,
    "speech_voice_conversion": ModelCapability.SPEECH_VOICE_CONVERSION,
    "singing_voice_conversion": ModelCapability.SINGING_VOICE_CONVERSION,
}

ERROR_MESSAGES = {
    "QUEUE_UNAVAILABLE": "작업 대기열에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "MODEL_NOT_FOUND": "요청한 음성 모델을 사용할 수 없습니다.",
    "INFERENCE_FAILED": "음성 처리 중 오류가 발생했습니다.",
}


def get_job_service(request: Request, session: AsyncSession) -> JobService:
    queue: JobQueue = request.app.state.job_queue
    return JobService(session, queue)


async def serialize_job(service: JobService, job: Job) -> JobResponse:
    response = JobResponse.model_validate(job)
    response.queue_position = await service.queue_position(job)
    response.user_message = ERROR_MESSAGES.get(job.error_code or "")
    return response


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: JobCreateRequest,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ] = None,
) -> JobResponse:
    capability = MODE_CAPABILITY[payload.mode.value]
    available = {item.key for item in request.app.state.model_registry.descriptors(capability)}
    if payload.model_key not in available:
        raise HTTPException(status_code=422, detail="작업 유형에 맞는 모델이 아닙니다.")
    service = get_job_service(request, session)
    try:
        job, _created = await service.create_job(
            actor_id,
            CreateJobCommand(
                mode=payload.mode,
                model_key=payload.model_key,
                voice_profile_id=payload.voice_profile_id,
                input_text=payload.input_text,
                input_storage_key=payload.input_storage_key,
                request_config=payload.request_config,
            ),
            idempotency_key=idempotency_key,
        )
    except QueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "QUEUE_UNAVAILABLE", "job_id": error.job_id},
        ) from error
    except InvalidVoiceProfileError as error:
        raise HTTPException(
            status_code=422,
            detail="사용 가능한 본인 음성 프로필을 선택해 주세요.",
        ) from error
    return await serialize_job(service, job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    service = get_job_service(request, session)
    page = await service.list_owned_jobs(
        actor_id, status=job_status, limit=limit, offset=offset
    )
    return JobListResponse(
        items=[await serialize_job(service, item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResponse:
    service = get_job_service(request, session)
    try:
        job = await service.get_owned_job(actor_id, job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.") from error
    return await serialize_job(service, job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResponse:
    service = get_job_service(request, session)
    try:
        job = await service.cancel_job(actor_id, job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.") from error
    except InvalidJobTransitionError as error:
        raise HTTPException(status_code=409, detail="완료된 작업은 취소할 수 없습니다.") from error
    return await serialize_job(service, job)


@router.post("/{job_id}/retry", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_job(
    job_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResponse:
    service = get_job_service(request, session)
    try:
        job = await service.retry_job(actor_id, job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.") from error
    except JobNotRetryableError as error:
        raise HTTPException(
            status_code=409,
            detail="실패하거나 취소된 작업만 재시도할 수 있습니다.",
        ) from error
    except QueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "job_id": error.job_id},
        ) from error
    return await serialize_job(service, job)

