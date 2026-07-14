from typing import List, Optional

from sqlalchemy import select, update
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
