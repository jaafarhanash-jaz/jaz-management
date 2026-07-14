import uuid
from typing import List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Notification


async def create(db: AsyncSession, *, user_id, company_id, type: str, title: str, message: str) -> Notification:
    notification = Notification(
        id=uuid.uuid4(),
        user_id=user_id,
        company_id=company_id,
        type=type,
        title=title,
        message=message,
        read_status=False,
    )
    db.add(notification)
    await db.flush()
    return notification


async def list_for_user(db: AsyncSession, user_id, limit: int = 50) -> List[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_read(db: AsyncSession, notification_id, user_id) -> bool:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .values(read_status=True)
    )
    return result.rowcount > 0
