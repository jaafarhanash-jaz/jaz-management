import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarEventException


async def create(db: AsyncSession, **fields) -> CalendarEventException:
    fields.setdefault("id", uuid.uuid4())
    exception = CalendarEventException(**fields)
    db.add(exception)
    await db.flush()
    return exception


async def get_for_event_and_date(db: AsyncSession, event_id, occurrence_date) -> Optional[CalendarEventException]:
    result = await db.execute(
        select(CalendarEventException).where(
            CalendarEventException.event_id == event_id,
            CalendarEventException.occurrence_date == occurrence_date,
        )
    )
    return result.scalar_one_or_none()


async def list_for_event(db: AsyncSession, event_id) -> List[CalendarEventException]:
    result = await db.execute(select(CalendarEventException).where(CalendarEventException.event_id == event_id))
    return list(result.scalars().all())
