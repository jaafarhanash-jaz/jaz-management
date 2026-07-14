from datetime import date as date_type
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

import repositories.calendar_events as calendar_events_repo
import repositories.companies as companies_repo


async def is_company_holiday(db: AsyncSession, company_id, date_: date_type) -> Optional[dict]:
    holidays = await calendar_events_repo.list_company_holidays_covering(db, company_id, date_)
    if not holidays:
        return None
    return {"id": str(holidays[0].id), "title": holidays[0].title}


async def is_weekly_holiday(db: AsyncSession, company_id, date_: date_type) -> bool:
    working_hours = await companies_repo.get_working_hours(db, company_id)
    weekday = (date_.weekday() + 1) % 7  # 0=Sunday..6=Saturday
    return weekday not in working_hours.get("working_days", [0, 1, 2, 3, 4])


async def get_day_off_info(db: AsyncSession, company_id, date_: date_type) -> Optional[dict]:
    holiday = await is_company_holiday(db, company_id, date_)
    if holiday:
        return {"type": "company_holiday", "title": holiday["title"]}
    if await is_weekly_holiday(db, company_id, date_):
        return {"type": "weekly_holiday", "title": None}
    return None


async def annotate_holiday_dates(db: AsyncSession, company_id, records: List[dict]) -> None:
    """Mutates response dicts in place, adding is_holiday/holiday_type/
    holiday_title - never changes the underlying stored status. One lookup
    per distinct date in the batch, not per record."""
    dates = {r["date"] for r in records if r.get("date")}
    off_by_date = {}
    for d in dates:
        info = await get_day_off_info(db, company_id, date_type.fromisoformat(d))
        if info:
            off_by_date[d] = info
    for r in records:
        info = off_by_date.get(r.get("date"))
        r["is_holiday"] = info is not None
        r["holiday_type"] = info["type"] if info else None
        r["holiday_title"] = info["title"] if info else None
