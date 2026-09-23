import uuid
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EmployeeGroupLocation, WorkLocation


async def create(db: AsyncSession, *, company_id, **fields) -> WorkLocation:
    location = WorkLocation(id=uuid.uuid4(), company_id=company_id, **fields)
    db.add(location)
    await db.flush()
    return location


async def get_by_id_and_company(db: AsyncSession, location_id, company_id) -> Optional[WorkLocation]:
    """Ownership-scoped lookup, mirroring work_schedules_repo's own - every
    owner-facing read/write on a location goes through this so an owner
    can never reach into another company's locations by guessing an id."""
    result = await db.execute(
        select(WorkLocation).where(
            WorkLocation.id == location_id, WorkLocation.company_id == company_id, WorkLocation.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def list_by_company(db: AsyncSession, company_id, *, active_only: bool = False) -> List[WorkLocation]:
    query = select(WorkLocation).where(WorkLocation.company_id == company_id, WorkLocation.deleted_at.is_(None))
    if active_only:
        query = query.where(WorkLocation.is_active.is_(True))
    result = await db.execute(query.order_by(WorkLocation.name))
    return list(result.scalars().all())


async def get_by_ids_and_company(db: AsyncSession, location_ids, company_id) -> List[WorkLocation]:
    if not location_ids:
        return []
    result = await db.execute(
        select(WorkLocation).where(
            WorkLocation.id.in_(location_ids), WorkLocation.company_id == company_id,
            WorkLocation.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def count_groups_using_location(db: AsyncSession, location_id) -> int:
    """Used by services/work_locations.py to block deactivating a location
    still linked to an active group - mirrors
    work_schedules_repo.count_employees_assigned's role."""
    from models import EmployeeGroup

    result = await db.execute(
        select(func.count())
        .select_from(EmployeeGroupLocation)
        .join(EmployeeGroup, EmployeeGroup.id == EmployeeGroupLocation.group_id)
        .where(EmployeeGroupLocation.location_id == location_id, EmployeeGroup.is_active.is_(True))
    )
    return result.scalar_one()
