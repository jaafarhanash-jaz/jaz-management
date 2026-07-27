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


async def list_active_by_company(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[Task]:
    """Task History feature: the Owner Tasks page's main list now shows
    only active work - every task except those already completed. Same
    Task table/rows as list_by_company, just one extra status exclusion;
    completed tasks aren't deleted or moved, they simply belong to the
    separate Task History archive read (list_completed_by_company)."""
    query = select(Task).where(
        Task.company_id == company_id,
        Task.deleted_at.is_(None),
        Task.status != "completed",
    ).order_by(Task.created_at)
    if limit is not None:
        query = query.limit(limit).offset(offset or 0)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_completed_by_company(
    db: AsyncSession, company_id, *,
    search=None, employee_id=None, created_by=None, priority=None,
    created_from=None, created_to=None, completed_from=None, completed_to=None,
) -> List[Task]:
    """Task History archive: the same Task rows, status='completed', never
    moved or copied anywhere - a differently-filtered read of the same
    table, same style as attendance's list_by_company filters. Ordered
    newest-completed-first by default; the service layer re-sorts in
    memory for the two name/priority-derived sort options that have no
    matching indexed column."""
    query = select(Task).where(
        Task.company_id == company_id,
        Task.deleted_at.is_(None),
        Task.status == "completed",
    )
    if search:
        query = query.where(Task.title.ilike(f"%{search}%"))
    if employee_id is not None:
        query = query.where(Task.assigned_to == employee_id)
    if created_by is not None:
        query = query.where(Task.created_by == created_by)
    if priority is not None:
        query = query.where(Task.priority == priority)
    if created_from is not None:
        query = query.where(Task.created_at >= created_from)
    if created_to is not None:
        query = query.where(Task.created_at < created_to)
    if completed_from is not None:
        query = query.where(Task.completed_at >= completed_from)
    if completed_to is not None:
        query = query.where(Task.completed_at < completed_to)
    query = query.order_by(Task.completed_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


HIDDEN_STATUSES = ("scheduled", "pending_sequence")


async def list_for_employee(db: AsyncSession, employee_id) -> List[Task]:
    """No company_id filter, matching the old query exactly - a task's
    assigned_to always belongs to the company that created it, so this is
    safe as-is. Excludes tasks not yet activated (future-scheduled, or a
    later step in a sequential workflow that hasn't been reached) - the
    employee must not see these at all, not just have them visually hidden."""
    result = await db.execute(
        select(Task).where(
            Task.assigned_to == employee_id,
            Task.deleted_at.is_(None),
            Task.status.notin_(HIDDEN_STATUSES),
        )
    )
    return list(result.scalars().all())


async def list_due_scheduled(db: AsyncSession, company_id, now) -> List[Task]:
    """Self-heal source: Scheduled Tasks whose activation time has arrived.
    Called from every task-list/dashboard read path (see services/tasks.py)
    - no scheduler process, same pattern as message/calendar reminders."""
    result = await db.execute(
        select(Task).where(
            Task.company_id == company_id,
            Task.status == "scheduled",
            Task.scheduled_activation_at <= now,
            Task.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def get_next_workflow_step(db: AsyncSession, batch_id, sequence_order: int) -> Optional[Task]:
    result = await db.execute(
        select(Task).where(
            Task.batch_id == batch_id,
            Task.sequence_order == sequence_order,
            Task.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_workflow_steps(db: AsyncSession, batch_id) -> List[Task]:
    result = await db.execute(
        select(Task)
        .where(Task.batch_id == batch_id, Task.sequence_order.is_not(None), Task.deleted_at.is_(None))
        .order_by(Task.sequence_order)
    )
    return list(result.scalars().all())


async def list_active_workflow_batch_ids(db: AsyncSession, company_id) -> List:
    """Distinct batch_ids for sequential workflows that still have at least
    one non-terminal step - powers the owner dashboard's workflow-progress
    widget. A workflow "finishes" (drops out of this list) once every step
    is completed/cancelled/rejected."""
    result = await db.execute(
        select(Task.batch_id)
        .where(
            Task.company_id == company_id,
            Task.sequence_order.is_not(None),
            Task.deleted_at.is_(None),
        )
        .group_by(Task.batch_id)
        .having(func.bool_or(Task.status.notin_(("completed", "cancelled", "rejected"))))
    )
    return [row[0] for row in result.all()]


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
    """Array append, equivalent to the old Mongo $push onto proof_files.

    Layer 3 of the proof-upload hardening (defense in depth - see the
    incident report): this is the last line of defense before the
    write. A blank/whitespace-only file_url must never reach
    array_append - raising here (rather than silently returning False,
    which would be misread as "task not found" by the caller) makes a
    contract violation from any future/bypassing caller loud and
    traceable instead of silently corrupting the column again."""
    if not file_url or not file_url.strip():
        raise ValueError("append_proof_file: file_url must not be empty or whitespace-only")

    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.assigned_to == employee_id)
        .values(proof_files=func.array_append(Task.proof_files, file_url))
    )
    return result.rowcount > 0
