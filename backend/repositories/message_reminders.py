import uuid
from typing import List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Message, MessageReminder


async def create(db: AsyncSession, **fields) -> MessageReminder:
    fields.setdefault("id", uuid.uuid4())
    reminder = MessageReminder(**fields)
    db.add(reminder)
    await db.flush()
    return reminder


async def list_due(db: AsyncSession, user_id, now) -> List[MessageReminder]:
    result = await db.execute(
        select(MessageReminder).where(
            MessageReminder.user_id == user_id,
            MessageReminder.notified_at.is_(None),
            MessageReminder.remind_at <= now,
        )
    )
    return list(result.scalars().all())


async def mark_notified(db: AsyncSession, reminder_id, now) -> None:
    await db.execute(update(MessageReminder).where(MessageReminder.id == reminder_id).values(notified_at=now))


async def get_message_subject(db: AsyncSession, message_id):
    result = await db.execute(select(Message.subject).where(Message.id == message_id))
    row = result.first()
    return row[0] if row else None
