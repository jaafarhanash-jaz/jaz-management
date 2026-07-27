import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Attendance


async def get_for_employee_on_date(db: AsyncSession, employee_id, date_) -> Optional[Attendance]:
    result = await db.execute(
        select(Attendance).where(Attendance.employee_id == employee_id, Attendance.date == date_)
    )
    return result.scalar_one_or_none()


async def get_in_company(db: AsyncSession, attendance_id, company_id) -> Optional[Attendance]:
    result = await db.execute(
        select(Attendance).where(Attendance.id == attendance_id, Attendance.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_recent_for_employee(db: AsyncSession, employee_id, limit: int = 10) -> List[Attendance]:
    result = await db.execute(
        select(Attendance)
        .where(Attendance.employee_id == employee_id)
        .order_by(Attendance.date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_history_for_employee(db: AsyncSession, employee_id, limit: int = 30) -> List[Attendance]:
    result = await db.execute(
        select(Attendance)
        .where(Attendance.employee_id == employee_id)
        .order_by(Attendance.date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_by_company(db: AsyncSession, company_id, *, date_=None, date_from=None, date_to=None,
                           employee_id=None, checked_out=None, status=None, department=None,
                           schedule_id=None, late=None, overtime=None, missing_hours=None) -> List[Attendance]:
    query = select(Attendance).where(Attendance.company_id == company_id)
    if date_from is not None or date_to is not None:
        if date_from is not None:
            query = query.where(Attendance.date >= date_from)
        if date_to is not None:
            query = query.where(Attendance.date <= date_to)
    elif date_ is not None:
        query = query.where(Attendance.date == date_)
    if employee_id is not None:
        query = query.where(Attendance.employee_id == employee_id)
    if checked_out is True:
        query = query.where(Attendance.check_out_time.is_not(None))
    elif status is not None:
        query = query.where(Attendance.status == status)
    # employee_department is always populated at check-in (unlike the newer
    # employee_position snapshot, which is null on every pre-existing row) -
    # safe to filter directly, no fallback-to-live-employee gap to worry about.
    if department is not None:
        query = query.where(Attendance.employee_department == department)
    if schedule_id is not None:
        query = query.where(Attendance.schedule_id == schedule_id)
    if late:
        query = query.where(Attendance.late_minutes > 0)
    if overtime:
        query = query.where(Attendance.overtime_minutes > 0)
    if missing_hours:
        query = query.where(Attendance.missing_minutes > 0)
    query = query.order_by(Attendance.date.desc(), Attendance.check_in_time.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_by_company_date_range(db: AsyncSession, company_id, date_from, date_to) -> List[Attendance]:
    result = await db.execute(
        select(Attendance).where(
            Attendance.company_id == company_id, Attendance.date >= date_from, Attendance.date <= date_to
        )
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Attendance:
    fields.setdefault("id", uuid.uuid4())
    record = Attendance(**fields)
    db.add(record)
    await db.flush()
    return record


async def get_by_ids(db: AsyncSession, attendance_ids) -> List[Attendance]:
    if not attendance_ids:
        return []
    result = await db.execute(select(Attendance).where(Attendance.id.in_(list(attendance_ids))))
    return list(result.scalars().all())
