from datetime import date as date_type, datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

import repositories.calendar_events as calendar_events_repo
import repositories.companies as companies_repo
from services.calendar_occurrences import expand_event_occurrences


async def is_company_holiday(db: AsyncSession, company_id, date_: date_type) -> Optional[dict]:
    """Broad DB pre-filter (list_active_holiday_candidates_covering), then a
    precise per-date check via expand_event_occurrences - recurring holidays
    (e.g. a permanent weekly Friday holiday) only match candidate rows whose
    base start_date is on/before date_, so this correctly evaluates every
    later occurrence of the series, not just its first."""
    candidates = await calendar_events_repo.list_active_holiday_candidates_covering(db, company_id, date_)
    if not candidates:
        return None
    day_start = datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc)
    day_end = datetime.combine(date_, datetime.max.time(), tzinfo=timezone.utc)
    date_str = date_.isoformat()
    for holiday in candidates:
        for occ in await expand_event_occurrences(db, holiday, day_start, day_end):
            if occ["occurrence_date"] == date_str:
                return {"id": str(holiday.id), "title": holiday.title}
    return None


async def is_weekly_holiday(db: AsyncSession, company_id, date_: date_type, *, working_hours: Optional[dict] = None) -> bool:
    # working_hours is the same company row for every date in a given batch -
    # callers looping over many dates (annotate_holiday_dates,
    # _filter_out_holiday_attendance) fetch it once and pass it through here
    # instead of this function re-querying the company on every single date.
    if working_hours is None:
        working_hours = await companies_repo.get_working_hours(db, company_id)
    weekday = (date_.weekday() + 1) % 7  # 0=Sunday..6=Saturday
    return weekday not in working_hours.get("working_days", [0, 1, 2, 3, 4])


async def get_day_off_info(db: AsyncSession, company_id, date_: date_type, *, working_hours: Optional[dict] = None) -> Optional[dict]:
    holiday = await is_company_holiday(db, company_id, date_)
    if holiday:
        return {"type": "company_holiday", "title": holiday["title"]}
    if await is_weekly_holiday(db, company_id, date_, working_hours=working_hours):
        return {"type": "weekly_holiday", "title": None}
    return None


async def annotate_holiday_dates(db: AsyncSession, company_id, records: List[dict]) -> None:
    """Mutates response dicts in place, adding is_holiday/holiday_type/
    holiday_title - never changes the underlying stored status. One lookup
    per distinct date in the batch, not per record."""
    dates = {r["date"] for r in records if r.get("date")}
    working_hours = await companies_repo.get_working_hours(db, company_id) if dates else None
    off_by_date = {}
    for d in dates:
        info = await get_day_off_info(db, company_id, date_type.fromisoformat(d), working_hours=working_hours)
        if info:
            off_by_date[d] = info
    for r in records:
        info = off_by_date.get(r.get("date"))
        r["is_holiday"] = info is not None
        r["holiday_type"] = info["type"] if info else None
        r["holiday_title"] = info["title"] if info else None
