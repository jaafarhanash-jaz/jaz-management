import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User


async def get_by_id(db: AsyncSession, user_id) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_by_email_or_phone(db: AsyncSession, identifier: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(
            (User.email == identifier) | (User.phone == identifier),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> User:
    fields.setdefault("id", uuid.uuid4())
    user = User(**fields)
    db.add(user)
    await db.flush()
    return user


async def update_last_seen(db: AsyncSession, user_id, last_seen_at) -> None:
    user = await get_by_id(db, user_id)
    if user:
        user.last_seen_at = last_seen_at
