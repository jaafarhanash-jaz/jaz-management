from typing import List

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarEvent


async def list_company_holidays_covering(db: AsyncSession, company_id, date_) -> List[CalendarEvent]:
    """Non-recurring-aware overlap check: category='company_holiday',
    active, not cancelled, start_date<=date<=end_date OR a recurring rule
    starting on/before date. The Calendar module (not yet migrated) owns
    the full precise recurrence-expansion engine (expand_event_occurrences)
    that the old Mongo code used here; calendar_events has zero rows until
    Calendar's CRUD exists, so this simplified check is behaviorally
    equivalent to the full one today. Must be revisited (swapped for a
    ported expand_event_occurrences) when the Calendar module migrates -
    see Module 6's completion report."""
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.company_id == company_id,
            CalendarEvent.category == "company_holiday",
            CalendarEvent.status != "cancelled",
            CalendarEvent.is_active.is_(True),
            CalendarEvent.start_date <= date_,
            or_(CalendarEvent.end_date >= date_, CalendarEvent.recurrence_type != "none"),
        )
    )
    return list(result.scalars().all())
