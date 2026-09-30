from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def resolve_user(
    session: AsyncSession,
    external_id: str,
    *,
    create: bool,
) -> User | None:
    user = await session.scalar(select(User).where(User.external_id == external_id))
    if user is None and create:
        user = User(external_id=external_id, is_active=True)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await session.scalar(select(User).where(User.external_id == external_id))
            if user is None:
                raise
    return user
