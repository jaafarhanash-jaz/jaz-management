from typing import List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarEvent, CalendarEventReminder


async def list_due(db: AsyncSession, user_id, now) -> List[CalendarEventReminder]:
    result = await db.execute(
        select(CalendarEventReminder).where(
            CalendarEventReminder.user_id == user_id,
            CalendarEventReminder.notified_at.is_(None),
            CalendarEventReminder.remind_at <= now,
        )
    )
    return list(result.scalars().all())


async def mark_notified(db: AsyncSession, reminder_id, now) -> None:
    await db.execute(
        update(CalendarEventReminder).where(CalendarEventReminder.id == reminder_id).values(notified_at=now)
    )


async def get_event_title(db: AsyncSession, event_id):
    result = await db.execute(select(CalendarEvent.title).where(CalendarEvent.id == event_id))
    row = result.first()
    return row[0] if row else None
