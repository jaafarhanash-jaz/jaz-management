import uuid
from typing import List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarEvent


async def create(db: AsyncSession, **fields) -> CalendarEvent:
    fields.setdefault("id", uuid.uuid4())
    event = CalendarEvent(**fields)
    db.add(event)
    await db.flush()
    return event


async def get_by_id(db: AsyncSession, event_id) -> Optional[CalendarEvent]:
    result = await db.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))
    return result.scalar_one_or_none()


async def get_in_company(db: AsyncSession, event_id, company_id) -> Optional[CalendarEvent]:
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_active_holiday_candidates_covering(db: AsyncSession, company_id, date_) -> List[CalendarEvent]:
    """Broad pre-filter for the single-date is_company_holiday check: active,
    not cancelled, starts on/before date_, and either ends on/after date_ or
    is a recurring rule - precise per-date membership is then decided by
    expand_event_occurrences downstream."""
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.company_id == company_id,
            CalendarEvent.category == "company_holiday",
            CalendarEvent.status.is_distinct_from("cancelled"),
            CalendarEvent.is_active.is_(True),
            CalendarEvent.start_date <= date_,
            or_(CalendarEvent.end_date >= date_, CalendarEvent.recurrence_type != "none"),
        )
    )
    return list(result.scalars().all())


async def list_holiday_conflict_candidates(db: AsyncSession, company_id, start_date, end_date) -> List[CalendarEvent]:
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.company_id == company_id,
            CalendarEvent.category == "company_holiday",
            CalendarEvent.status.is_distinct_from("cancelled"),
            CalendarEvent.start_date <= end_date,
            CalendarEvent.end_date >= start_date,
        )
    )
    return list(result.scalars().all())


async def list_conflict_candidates(db: AsyncSession, company_id, window_end_date, start_date,
                                    exclude_event_id=None, all_day_only=False) -> List[CalendarEvent]:
    conditions = [
        CalendarEvent.company_id == company_id,
        CalendarEvent.status.is_distinct_from("cancelled"),
        CalendarEvent.start_date <= window_end_date,
        CalendarEvent.end_date >= start_date,
    ]
    if exclude_event_id:
        conditions.append(CalendarEvent.id != exclude_event_id)
    if all_day_only:
        conditions.append(CalendarEvent.all_day.is_(True))
    result = await db.execute(select(CalendarEvent).where(*conditions))
    return list(result.scalars().all())


async def list_range_candidates(db: AsyncSession, company_id, view_start, view_end) -> List[CalendarEvent]:
    """Same pre-filter the old Mongo range query used for the calendar grid:
    events that could possibly have an occurrence inside [view_start,
    view_end]. Precise per-occurrence inclusion is decided by
    expand_event_occurrences downstream. view_start/view_end are date
    objects (compared against the Date-typed start_date column);
    recurrence_end_value is a String column (genuinely polymorphic - ISO
    date string or stringified count, see plan decision #9) so it's
    compared against the ISO string form instead."""
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.company_id == company_id,
            CalendarEvent.start_date <= view_end,
            or_(
                CalendarEvent.recurrence_end_type != "on_date",
                CalendarEvent.recurrence_end_value >= view_start.isoformat(),
                CalendarEvent.recurrence_end_value.is_(None),
            ),
        )
    )
    return list(result.scalars().all())


async def search_candidates(db: AsyncSession, company_id, range_from, range_to, *, reference_number=None,
                             title=None, category=None, priority=None, created_by_name=None,
                             event_ids=None) -> List[CalendarEvent]:
    conditions = [
        CalendarEvent.company_id == company_id,
        CalendarEvent.start_date <= range_to,
        CalendarEvent.end_date >= range_from,
    ]
    if reference_number:
        conditions.append(CalendarEvent.reference_number.ilike(f"%{reference_number}%"))
    if title:
        conditions.append(CalendarEvent.title.ilike(f"%{title}%"))
    if category:
        conditions.append(CalendarEvent.category == category)
    if priority:
        conditions.append(CalendarEvent.priority == priority)
    if created_by_name:
        conditions.append(CalendarEvent.created_by_name.ilike(f"%{created_by_name}%"))
    if event_ids is not None:
        conditions.append(CalendarEvent.id.in_(list(event_ids)))
    result = await db.execute(select(CalendarEvent).where(*conditions))
    return list(result.scalars().all())


async def list_holidays(db: AsyncSession, company_id) -> List[CalendarEvent]:
    result = await db.execute(
        select(CalendarEvent)
        .where(CalendarEvent.company_id == company_id, CalendarEvent.category == "company_holiday")
        .order_by(CalendarEvent.start_date.desc())
    )
    return list(result.scalars().all())


async def count_by_company(db: AsyncSession, company_id) -> int:
    result = await db.execute(select(func.count()).select_from(CalendarEvent).where(CalendarEvent.company_id == company_id))
    return result.scalar_one()


async def list_by_company_paginated(db: AsyncSession, company_id, page, page_size) -> List[CalendarEvent]:
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.company_id == company_id)
        .order_by(CalendarEvent.start_date.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all())


async def list_upcoming_window(db: AsyncSession, company_id, window_start, window_end,
                                event_ids=None) -> List[CalendarEvent]:
    conditions = [
        CalendarEvent.status.is_distinct_from("cancelled"),
        CalendarEvent.start_date <= window_end,
        CalendarEvent.end_date >= window_start,
    ]
    if event_ids is not None:
        conditions.append(CalendarEvent.id.in_(list(event_ids)))
    else:
        conditions.append(CalendarEvent.company_id == company_id)
    result = await db.execute(select(CalendarEvent).where(*conditions))
    return list(result.scalars().all())
