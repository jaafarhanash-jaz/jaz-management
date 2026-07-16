import uuid
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Notification


async def create(
    db: AsyncSession,
    *,
    user_id,
    company_id,
    type: str,
    title: str,
    message: str,
    category: str = "system",
    entity_type: Optional[str] = None,
    entity_id=None,
    action_url: Optional[str] = None,
    sender_id=None,
    sender_name: Optional[str] = None,
) -> Notification:
    notification = Notification(
        id=uuid.uuid4(),
        user_id=user_id,
        company_id=company_id,
        type=type,
        category=category,
        title=title,
        message=message,
        read_status=False,
        entity_type=entity_type,
        entity_id=entity_id,
        action_url=action_url,
        sender_id=sender_id,
        sender_name=sender_name,
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


async def count_unread(db: AsyncSession, user_id) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_status.is_(False))
    )
    return result.scalar_one()


async def mark_read(db: AsyncSession, notification_id, user_id) -> bool:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .values(read_status=True)
    )
    return result.rowcount > 0


async def exists_since(db: AsyncSession, user_id, type: str, since) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.type == type, Notification.created_at >= since)
    )
    return result.scalar_one() > 0


async def mark_all_read(db: AsyncSession, user_id) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_status.is_(False))
        .values(read_status=True)
    )
    return result.rowcount
