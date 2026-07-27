import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AttendanceEvent


async def create(db: AsyncSession, **fields) -> AttendanceEvent:
    fields.setdefault("id", uuid.uuid4())
    event = AttendanceEvent(**fields)
    db.add(event)
    await db.flush()
    return event


async def list_for_record(db: AsyncSession, attendance_record_id) -> List[AttendanceEvent]:
    result = await db.execute(
        select(AttendanceEvent)
        .where(AttendanceEvent.attendance_record_id == attendance_record_id)
        .order_by(AttendanceEvent.created_at.asc())
    )
    return list(result.scalars().all())


async def list_for_company(db: AsyncSession, company_id, *, date_from=None, date_to=None, limit: int = 500) -> List[AttendanceEvent]:
    query = select(AttendanceEvent).where(AttendanceEvent.company_id == company_id)
    if date_from is not None:
        query = query.where(AttendanceEvent.event_date >= date_from)
    if date_to is not None:
        query = query.where(AttendanceEvent.event_date <= date_to)
    query = query.order_by(AttendanceEvent.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())
