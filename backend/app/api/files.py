from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_actor_id
from app.db.models import Job, JobOutput, User
from app.db.session import get_db_session

router = APIRouter(prefix="/files", tags=["files"])


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
