import uuid
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, WorkSchedule


async def create(db: AsyncSession, *, company_id, **fields) -> WorkSchedule:
    schedule = WorkSchedule(id=uuid.uuid4(), company_id=company_id, **fields)
    db.add(schedule)
    await db.flush()
    return schedule


async def get_by_id_and_company(db: AsyncSession, schedule_id, company_id) -> Optional[WorkSchedule]:
    """Ownership-scoped lookup, mirroring users_repo.get_employee_in_company
    - every owner-facing read/write on a schedule goes through this so an
    owner can never reach into another company's schedules by guessing an id."""
    result = await db.execute(
        select(WorkSchedule).where(WorkSchedule.id == schedule_id, WorkSchedule.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_by_company(db: AsyncSession, company_id, *, active_only: bool = False) -> List[WorkSchedule]:
    query = select(WorkSchedule).where(WorkSchedule.company_id == company_id)
    if active_only:
        query = query.where(WorkSchedule.is_active.is_(True))
    result = await db.execute(query.order_by(WorkSchedule.name))
    return list(result.scalars().all())


async def get_default_for_company(db: AsyncSession, company_id) -> Optional[WorkSchedule]:
    result = await db.execute(
        select(WorkSchedule).where(WorkSchedule.company_id == company_id, WorkSchedule.is_default.is_(True))
    )
    return result.scalar_one_or_none()


async def count_employees_assigned(db: AsyncSession, schedule_id) -> int:
    """Used by services/work_schedules.py to block deactivating a schedule
    that's still directly assigned to someone (company-default-only
    dependents are covered separately by is_default itself)."""
    result = await db.execute(
        select(func.count()).select_from(User).where(User.schedule_id == schedule_id, User.deleted_at.is_(None))
    )
    return result.scalar_one()


async def unset_current_default(db: AsyncSession, company_id) -> None:
    result = await db.execute(
        select(WorkSchedule).where(WorkSchedule.company_id == company_id, WorkSchedule.is_default.is_(True))
    )
    for schedule in result.scalars().all():
        schedule.is_default = False
    await db.flush()
