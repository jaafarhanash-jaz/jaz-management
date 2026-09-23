import uuid
from datetime import date as date_type
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Leave


async def create(db: AsyncSession, **fields) -> Leave:
    fields.setdefault("id", uuid.uuid4())
    leave = Leave(**fields)
    db.add(leave)
    await db.flush()
    return leave


async def get_by_id_and_company(db: AsyncSession, leave_id, company_id) -> Optional[Leave]:
    """Ownership-scoped lookup, same convention as every other Owner
    resource in this codebase - never a bare get-by-id."""
    result = await db.execute(
        select(Leave).where(Leave.id == leave_id, Leave.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def get_by_id_and_employee(db: AsyncSession, leave_id, employee_id) -> Optional[Leave]:
    result = await db.execute(
        select(Leave).where(Leave.id == leave_id, Leave.employee_id == employee_id)
    )
    return result.scalar_one_or_none()


async def list_for_employee(db: AsyncSession, employee_id, *, status: Optional[str] = None) -> List[Leave]:
    query = select(Leave).where(Leave.employee_id == employee_id)
    if status:
        query = query.where(Leave.status == status)
    query = query.order_by(Leave.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_for_company(
    db: AsyncSession, company_id, *,
    status: Optional[str] = None, employee_id=None, duration_type: Optional[str] = None,
    date_from: Optional[date_type] = None, date_to: Optional[date_type] = None,
) -> List[Leave]:
    """date_from/date_to are real date objects, never ISO strings - Date
    columns compared against a plain str fail at the asyncpg layer
    (`expected a datetime.date instance, got str`), so callers must parse
    any incoming string query param before calling this, same convention
    as repositories.attendance.list_by_company_date_range."""
    query = select(Leave).where(Leave.company_id == company_id)
    if status:
        query = query.where(Leave.status == status)
    if employee_id:
        query = query.where(Leave.employee_id == employee_id)
    if duration_type:
        query = query.where(Leave.duration_type == duration_type)
    # Date-range filter is a coarse overlap test against the filter window,
    # same shape as the overlap pre-filter used for validation - not an
    # exact-match filter, so a multi-day leave that merely touches the
    # requested window is still surfaced.
    if date_from:
        query = query.where(Leave.end_date >= date_from)
    if date_to:
        query = query.where(Leave.start_date <= date_to)
    query = query.order_by(Leave.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_approved_overlapping(
    db: AsyncSession, employee_id, start_at, end_at, *, exclude_id=None,
) -> List[Leave]:
    """Every APPROVED leave for this employee whose [start_at, end_at)
    instant interval intersects the given one - the application-level
    overlap check backing both create-time validation and the
    approval-time transactional revalidation. The database EXCLUDE
    constraint (models.py) is the final, unconditional backstop; this
    query is what produces a friendly, specific error before that."""
    query = select(Leave).where(
        Leave.employee_id == employee_id,
        Leave.status == "approved",
        Leave.start_at < end_at,
        Leave.end_at > start_at,
    )
    if exclude_id:
        query = query.where(Leave.id != exclude_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_pending_overlapping_for_info(db: AsyncSession, employee_id, start_at, end_at) -> List[Leave]:
    """Overlapping PENDING requests - never a rejection reason on their
    own (Rule: pending requests may coexist), but surfaced to the Owner at
    approval time so approving one can flag the others as now-conflicting
    if the design ever wants that; unused by validation itself today."""
    result = await db.execute(
        select(Leave).where(
            Leave.employee_id == employee_id,
            Leave.status == "pending",
            Leave.start_at < end_at,
            Leave.end_at > start_at,
        )
    )
    return list(result.scalars().all())


async def list_approved_for_employee_on_date(db: AsyncSession, employee_id, date_: date_type) -> List[Leave]:
    """Every APPROVED leave (any duration_type) whose shift-owning date
    range includes date_ - powers the per-record leave-interval
    annotation (services/leave_attendance.py) shown in attendance
    history."""
    result = await db.execute(
        select(Leave).where(
            Leave.employee_id == employee_id,
            Leave.status == "approved",
            Leave.start_date <= date_,
            Leave.end_date >= date_,
        )
    )
    return list(result.scalars().all())


async def list_approved_full_day_in_range(
    db: AsyncSession, company_id, employee_ids, date_from: date_type, date_to: date_type,
) -> List[Leave]:
    """Every APPROVED full_day/multi-day leave for the given employees
    that overlaps [date_from, date_to] at all - the batch query behind
    the unified expected-attendance/absence-denominator fix
    (services/leave_attendance.py)."""
    if not employee_ids:
        return []
    result = await db.execute(
        select(Leave).where(
            Leave.company_id == company_id,
            Leave.employee_id.in_(list(employee_ids)),
            Leave.status == "approved",
            Leave.duration_type == "full_day",
            Leave.start_date <= date_to,
            Leave.end_date >= date_from,
        )
    )
    return list(result.scalars().all())


async def lock_approved_for_employee(db: AsyncSession, employee_id) -> List[Leave]:
    """SELECT ... FOR UPDATE on this employee's currently-approved leaves -
    used as part of the approval-time transactional revalidation
    (services/leaves.py). Combined with a transaction-scoped Postgres
    advisory lock keyed on employee_id (acquired by the caller before this
    runs), this serializes concurrent approval attempts for the same
    employee so the overlap re-check below is never racing another
    in-flight approval. The EXCLUDE constraint on the leaves table is the
    unconditional final backstop regardless of whether this path is ever
    bypassed."""
    result = await db.execute(
        select(Leave)
        .where(Leave.employee_id == employee_id, Leave.status == "approved")
        .with_for_update()
    )
    return list(result.scalars().all())
