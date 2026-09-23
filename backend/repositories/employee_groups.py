import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EmployeeGroup, EmployeeGroupAssignment, EmployeeGroupLocation, User, WorkLocation


async def create(db: AsyncSession, *, company_id, **fields) -> EmployeeGroup:
    group = EmployeeGroup(id=uuid.uuid4(), company_id=company_id, **fields)
    db.add(group)
    await db.flush()
    return group


async def get_by_id_and_company(db: AsyncSession, group_id, company_id) -> Optional[EmployeeGroup]:
    """Ownership-scoped lookup, mirroring work_schedules_repo/work_locations_repo."""
    result = await db.execute(
        select(EmployeeGroup).where(
            EmployeeGroup.id == group_id, EmployeeGroup.company_id == company_id,
            EmployeeGroup.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_by_company(db: AsyncSession, company_id, *, active_only: bool = False) -> List[EmployeeGroup]:
    query = select(EmployeeGroup).where(EmployeeGroup.company_id == company_id, EmployeeGroup.deleted_at.is_(None))
    if active_only:
        query = query.where(EmployeeGroup.is_active.is_(True))
    result = await db.execute(query.order_by(EmployeeGroup.name))
    return list(result.scalars().all())


async def count_members(db: AsyncSession, group_id) -> int:
    """Distinct employees with a currently OPEN membership row for this
    group - see EmployeeGroupAssignment, the sole source of truth for
    membership (an employee can have an open row here for more than one
    group at once)."""
    result = await db.execute(
        select(func.count(func.distinct(EmployeeGroupAssignment.employee_id)))
        .select_from(EmployeeGroupAssignment)
        .join(User, User.id == EmployeeGroupAssignment.employee_id)
        .where(
            EmployeeGroupAssignment.group_id == group_id, EmployeeGroupAssignment.effective_to.is_(None),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one()


async def list_members(db: AsyncSession, group_id) -> List[User]:
    result = await db.execute(
        select(User)
        .join(EmployeeGroupAssignment, EmployeeGroupAssignment.employee_id == User.id)
        .where(
            EmployeeGroupAssignment.group_id == group_id, EmployeeGroupAssignment.effective_to.is_(None),
            User.deleted_at.is_(None),
        )
        .order_by(User.name)
    )
    return list(result.scalars().all())


# ---- Group <-> Location join ----

async def list_locations_for_group(db: AsyncSession, group_id) -> List[WorkLocation]:
    result = await db.execute(
        select(WorkLocation)
        .join(EmployeeGroupLocation, EmployeeGroupLocation.location_id == WorkLocation.id)
        .where(EmployeeGroupLocation.group_id == group_id, WorkLocation.deleted_at.is_(None))
        .order_by(WorkLocation.name)
    )
    return list(result.scalars().all())


async def set_locations(db: AsyncSession, group_id, location_ids: List) -> None:
    """Delete+bulk-insert replace semantics - the join table has no other
    meaningful state per row, so there's nothing worth diffing (same
    pattern as the recovered attendance_policies_repo.set_locations)."""
    await db.execute(delete(EmployeeGroupLocation).where(EmployeeGroupLocation.group_id == group_id))
    for location_id in location_ids:
        db.add(EmployeeGroupLocation(id=uuid.uuid4(), group_id=group_id, location_id=location_id))
    await db.flush()


# ---- Membership (EmployeeGroupAssignment - the authoritative employee<->
# group relationship, many-to-many with history; see the model's own
# docstring for the 2026-08-31 architecture correction this codifies) ----

async def get_open_assignment(db: AsyncSession, employee_id, group_id) -> Optional[EmployeeGroupAssignment]:
    """The open row for one SPECIFIC (employee, group) pair, if any -
    used to check "is this employee already in this group" before adding,
    and to find the exact row to close when removing."""
    result = await db.execute(
        select(EmployeeGroupAssignment).where(
            EmployeeGroupAssignment.employee_id == employee_id,
            EmployeeGroupAssignment.group_id == group_id,
            EmployeeGroupAssignment.effective_to.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_open_assignments_for_employee(db: AsyncSession, employee_id) -> List[EmployeeGroupAssignment]:
    """Every group this employee currently belongs to - zero, one, or
    many rows, all with effective_to IS NULL."""
    result = await db.execute(
        select(EmployeeGroupAssignment).where(
            EmployeeGroupAssignment.employee_id == employee_id, EmployeeGroupAssignment.effective_to.is_(None),
        )
    )
    return list(result.scalars().all())


async def add_assignment(db: AsyncSession, *, employee_id, company_id, group_id, assigned_by) -> EmployeeGroupAssignment:
    """Opens a new membership row for (employee, group) - caller must
    already have checked get_open_assignment returns None for this exact
    pair (services/employee_groups.py enforces "already a member" as a
    400, not a silent no-op or a duplicate row - the partial unique index
    on (employee_id, group_id) WHERE effective_to IS NULL backs this up
    at the DB level regardless)."""
    assignment = EmployeeGroupAssignment(
        id=uuid.uuid4(), employee_id=employee_id, company_id=company_id, group_id=group_id,
        effective_from=datetime.now(timezone.utc), assigned_by=assigned_by,
    )
    db.add(assignment)
    await db.flush()
    return assignment


async def close_assignment(db: AsyncSession, assignment: EmployeeGroupAssignment) -> None:
    """Ends one specific membership - never touches any other open row
    for the same employee, so removing one group membership can never
    affect a different, still-active one."""
    assignment.effective_to = datetime.now(timezone.utc)
    await db.flush()


async def list_assignment_history_for_employee(db: AsyncSession, employee_id) -> List[EmployeeGroupAssignment]:
    """Full history, including overlapping date ranges across different
    groups when the employee belonged to more than one at the same time -
    that overlap is expected and correct, not a data quality issue."""
    result = await db.execute(
        select(EmployeeGroupAssignment)
        .where(EmployeeGroupAssignment.employee_id == employee_id)
        .order_by(EmployeeGroupAssignment.effective_from.desc())
    )
    return list(result.scalars().all())
