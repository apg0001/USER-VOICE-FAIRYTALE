from typing import Annotated

from fastapi import Header, HTTPException, status


async def get_actor_id(
    x_user_id: Annotated[str, Header(alias="X-User-ID", min_length=1, max_length=255)],
) -> str:
    """Temporary trusted identity boundary; replace with verified auth claims before public use."""

    value = x_user_id.strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="X-User-ID 헤더가 필요합니다.",
        )
    return value

