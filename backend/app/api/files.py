import asyncio
import os
import tempfile
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_actor_id
from app.audio.ffmpeg import MediaToolError
from app.audio.types import NoiseReduction, PreprocessingConfig
from app.audio.validation import AudioValidationError
from app.db.models import Job, JobOutput, User
from app.db.session import get_db_session
from app.services.user_service import resolve_user

router = APIRouter(prefix="/files", tags=["files"])


class InputFileResponse(BaseModel):
    input_storage_key: str
    duration_seconds: float
    sample_rate: int
    quality: dict[str, object]


def _copy_upload(source: BinaryIO, destination: Path, max_bytes: int) -> None:
    total = 0
    with destination.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise AudioValidationError("INVALID_SIZE", "업로드 파일이 너무 큽니다.")
            output.write(chunk)


@router.post("/inputs", response_model=InputFileResponse, status_code=201)
async def upload_job_input(
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    audio_file: Annotated[UploadFile, File()],
    noise_reduction: Annotated[NoiseReduction, Form()] = NoiseReduction.NORMAL,
) -> InputFileResponse:
    user = await resolve_user(session, actor_id, create=True)
    assert user is not None
    descriptor, temp_name = tempfile.mkstemp(prefix="job-input-", suffix=".media")
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        await asyncio.to_thread(
            _copy_upload,
            audio_file.file,
            temp_path,
            request.app.state.settings.max_upload_size,
        )
        artifact = await request.app.state.file_service.ingest_audio(
            temp_path,
            original_filename=audio_file.filename or "",
            content_type=audio_file.content_type or "application/octet-stream",
            namespace=f"users/{user.id}/inputs",
            config=PreprocessingConfig(noise_reduction=noise_reduction),
        )
        return InputFileResponse(
            input_storage_key=artifact.cleaned_storage_key,
            duration_seconds=artifact.probe.duration_seconds,
            sample_rate=artifact.preprocessing.target_sample_rate,
            quality=artifact.quality.to_dict(),
        )
    except AudioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": error.user_message},
        ) from error
    except MediaToolError as error:
        raise HTTPException(
            status_code=422, detail="손상되었거나 지원하지 않는 오디오 파일입니다."
        ) from error
    finally:
        await audio_file.close()
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)


@router.get("/{output_id}")
async def download_output(
    output_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    output = await session.scalar(
        select(JobOutput)
        .join(Job, Job.id == JobOutput.job_id)
        .join(User, User.id == Job.user_id)
        .where(JobOutput.id == output_id, User.external_id == actor_id)
    )
    if output is None:
        raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
    return StreamingResponse(
        request.app.state.file_service.storage.open(output.storage_key),
        media_type=output.content_type,
        headers={"Content-Disposition": f'attachment; filename="voice-{output.id}.wav"'},
    )
