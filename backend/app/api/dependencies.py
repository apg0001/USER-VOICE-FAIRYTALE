import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, status


async def get_actor_id(
    request: Request,
    x_user_id: Annotated[
        str | None, Header(alias="X-User-ID", min_length=1, max_length=255)
    ] = None,
    x_authenticated_user: Annotated[
        str | None, Header(alias="X-Authenticated-User", min_length=1, max_length=255)
    ] = None,
    x_proxy_secret: Annotated[str | None, Header(alias="X-Proxy-Secret")] = None,
) -> str:
    """Resolve either a development identity or a subject asserted by a trusted proxy."""

    settings = request.app.state.settings
    if settings.auth_mode == "trusted_proxy":
        configured = settings.trusted_proxy_secret
        if (
            configured is None
            or x_proxy_secret is None
            or not secrets.compare_digest(configured.get_secret_value(), x_proxy_secret)
            or x_authenticated_user is None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="인증이 필요합니다.",
            )
        value = x_authenticated_user.strip()
    else:
        value = (x_user_id or "").strip()

    if not value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
        )
    return value

