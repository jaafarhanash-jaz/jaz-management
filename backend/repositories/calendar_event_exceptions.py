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


async def list_for_events(db: AsyncSession, event_ids) -> List[CalendarEventException]:
    """Batched form of list_for_event - one query for every event in
    event_ids instead of one query per event, for callers expanding
    occurrences for a whole list of candidate events at once."""
    event_ids = list(event_ids)
    if not event_ids:
        return []
    result = await db.execute(select(CalendarEventException).where(CalendarEventException.event_id.in_(event_ids)))
    return list(result.scalars().all())
