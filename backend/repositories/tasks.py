import uuid
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Task


async def get_pending_critical_for_employee(db: AsyncSession, employee_id, limit: int = 50) -> List[Task]:
    result = await db.execute(
        select(Task)
        .where(
            Task.assigned_to == employee_id,
            Task.priority == "critical",
            Task.status.in_(["new", "seen"]),
            Task.deleted_at.is_(None),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def claim_alert_delivery(db: AsyncSession, task_id, delivered_at) -> bool:
    """Atomic claim: only the first concurrent caller flips alert_delivered_at
    from NULL, so the 'delivered' notification fires exactly once (same
    semantics as the old Mongo find_one_and_update with the NULL guard)."""
    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.alert_delivered_at.is_(None))
        .values(alert_delivered_at=delivered_at)
    )
    return result.rowcount > 0


async def list_by_company(db: AsyncSession, company_id) -> List[Task]:
    result = await db.execute(
        select(Task).where(Task.company_id == company_id, Task.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def list_for_employee(db: AsyncSession, employee_id) -> List[Task]:
    """No company_id filter, matching the old query exactly - a task's
    assigned_to always belongs to the company that created it, so this is
    safe as-is."""
    result = await db.execute(
        select(Task).where(Task.assigned_to == employee_id, Task.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Task:
    fields.setdefault("id", uuid.uuid4())
    task = Task(**fields)
    db.add(task)
    await db.flush()
    return task


async def get_in_company(db: AsyncSession, task_id, company_id) -> Optional[Task]:
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.company_id == company_id, Task.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_for_employee(db: AsyncSession, task_id, employee_id) -> Optional[Task]:
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.assigned_to == employee_id, Task.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_daily_instance(db: AsyncSession, daily_task_id, employee_id, occurrence_date) -> Optional[Task]:
    result = await db.execute(
        select(Task).where(
            Task.daily_task_id == daily_task_id,
            Task.assigned_to == employee_id,
            Task.occurrence_date == occurrence_date,
        )
    )
    return result.scalar_one_or_none()


async def append_proof_file(db: AsyncSession, task_id, employee_id, file_url: str) -> bool:
    """Array append, equivalent to the old Mongo $push onto proof_files."""
    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.assigned_to == employee_id)
        .values(proof_files=func.array_append(Task.proof_files, file_url))
    )
    return result.rowcount > 0
