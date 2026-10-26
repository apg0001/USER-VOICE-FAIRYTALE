from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_actor_id
from app.db.session import get_db_session
from app.services.cleanup_service import CleanupService

router = APIRouter(prefix="/users", tags=["users"])


class UserDeletionResponse(BaseModel):
    status: Literal["deleted", "pending"]


@router.delete("/me", response_model=UserDeletionResponse, status_code=status.HTTP_202_ACCEPTED)
async def delete_current_user(
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserDeletionResponse:
    settings = request.app.state.settings
    service = CleanupService(
        session,
        request.app.state.file_service.storage,
        request.app.state.job_queue,
        batch_size=settings.cleanup_batch_size,
        output_retention_hours=settings.output_retention_hours,
        orphan_grace_hours=settings.orphan_grace_hours,
    )
    deleted = await service.request_user_deletion(actor_id)
    return UserDeletionResponse(status="deleted" if deleted else "pending")
