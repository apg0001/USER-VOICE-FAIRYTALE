import asyncio
import os
import tempfile
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_actor_id
from app.api.voices.schemas import (
    VoiceConsentResponse,
    VoiceProfileListResponse,
    VoiceProfileResponse,
    VoiceSampleSummary,
)
from app.audio.ffmpeg import MediaToolError
from app.audio.types import NoiseReduction, PreprocessingConfig
from app.audio.validation import AudioValidationError
from app.db.models import VoiceProfile, VoiceSample
from app.db.session import get_db_session
from app.services.voice_service import (
    ConsentRequiredError,
    InsufficientVoiceError,
    VoiceConsent,
    VoiceProfileNotFoundError,
    VoiceService,
)

router = APIRouter(prefix="/voices", tags=["voices"])

CONSENT_STATEMENT = (
    "등록 음성이 본인의 음성이거나 명시적 사용 권한이 있으며, "
    "Voice Profile 생성과 음성 변환 처리에 동의합니다."
)


def _copy_upload(source: BinaryIO, destination: Path, max_bytes: int) -> None:
    total = 0
    with destination.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise AudioValidationError("INVALID_SIZE", "업로드 파일이 너무 큽니다.")
            output.write(chunk)


def get_voice_service(request: Request, session: AsyncSession) -> VoiceService:
    settings = request.app.state.settings
    return VoiceService(
        session,
        request.app.state.file_service,
        request.app.state.profile_builder_registry,
        required_consent_version=settings.voice_consent_version,
        min_speech_seconds=settings.min_voice_profile_speech_seconds,
    )


async def serialize_profile(session: AsyncSession, profile: VoiceProfile) -> VoiceProfileResponse:
    samples = list(
        await session.scalars(select(VoiceSample).where(VoiceSample.voice_profile_id == profile.id))
    )
    model_profiles = profile.profile_metadata.get("model_profiles", {})
    return VoiceProfileResponse(
        id=profile.id,
        name=profile.name,
        status=profile.status,
        consent_version=profile.consent_version,
        consented_at=profile.consented_at,
        created_at=profile.created_at,
        samples=[
            VoiceSampleSummary(
                id=sample.id,
                duration_seconds=sample.duration_seconds,
                sample_rate=sample.sample_rate,
                quality=sample.quality_metadata,
            )
            for sample in samples
        ],
        available_models=list(model_profiles),
    )


@router.post("", response_model=VoiceProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_voice_profile(
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
    consent_accepted: Annotated[bool, Form()],
    owns_voice_or_has_permission: Annotated[bool, Form()],
    consent_version: Annotated[str, Form(max_length=30)],
    voice_sample: Annotated[UploadFile, File()],
    noise_reduction: Annotated[NoiseReduction, Form()] = NoiseReduction.NORMAL,
    sample_transcript: Annotated[str | None, Form(max_length=1000)] = None,
) -> VoiceProfileResponse:
    settings = request.app.state.settings
    normalized_name = name.strip()
    if not normalized_name:
        raise HTTPException(status_code=422, detail="음성 프로필 이름이 필요합니다.")
    filename = voice_sample.filename or ""
    content_type = voice_sample.content_type or "application/octet-stream"
    file_descriptor, temp_name = tempfile.mkstemp(prefix="voice-upload-", suffix=".media")
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        await asyncio.to_thread(
            _copy_upload,
            voice_sample.file,
            temp_path,
            settings.max_upload_size,
        )
        service = get_voice_service(request, session)
        profile = await service.create_profile(
            actor_id,
            name=normalized_name,
            source=temp_path,
            original_filename=filename,
            content_type=content_type,
            consent=VoiceConsent(
                accepted=consent_accepted,
                owns_voice_or_has_permission=owns_voice_or_has_permission,
                version=consent_version,
            ),
            preprocessing=PreprocessingConfig(noise_reduction=noise_reduction),
            sample_transcript=(sample_transcript or "").strip() or None,
        )
        return await serialize_profile(session, profile)
    except ConsentRequiredError as error:
        raise HTTPException(
            status_code=422,
            detail="현재 동의문 확인과 음성 사용 권한 선언이 모두 필요합니다.",
        ) from error
    except InsufficientVoiceError as error:
        raise HTTPException(
            status_code=422,
            detail=(
                f"선명한 음성이 최소 {settings.min_voice_profile_speech_seconds:g}초 필요합니다."
            ),
        ) from error
    except AudioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": error.user_message},
        ) from error
    except MediaToolError as error:
        raise HTTPException(
            status_code=422,
            detail="손상되었거나 지원하지 않는 오디오 파일입니다.",
        ) from error
    finally:
        await voice_sample.close()
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)


@router.get("", response_model=VoiceProfileListResponse)
async def list_voice_profiles(
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VoiceProfileListResponse:
    service = get_voice_service(request, session)
    profiles = await service.list_profiles(actor_id)
    return VoiceProfileListResponse(
        items=[await serialize_profile(session, profile) for profile in profiles]
    )


@router.get("/consent", response_model=VoiceConsentResponse)
async def get_voice_consent(request: Request) -> VoiceConsentResponse:
    return VoiceConsentResponse(
        version=request.app.state.settings.voice_consent_version,
        statement=CONSENT_STATEMENT,
    )


@router.get("/{profile_id}", response_model=VoiceProfileResponse)
async def get_voice_profile(
    profile_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VoiceProfileResponse:
    service = get_voice_service(request, session)
    try:
        profile = await service.get_profile(actor_id, profile_id)
    except VoiceProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="음성 프로필을 찾을 수 없습니다.") from error
    return await serialize_profile(session, profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_profile(
    profile_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    service = get_voice_service(request, session)
    try:
        await service.delete_profile(actor_id, profile_id)
    except VoiceProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="음성 프로필을 찾을 수 없습니다.") from error
