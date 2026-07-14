import uuid
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DailyTaskAssignee


async def list_employee_ids(db: AsyncSession, daily_task_id) -> List:
    result = await db.execute(
        select(DailyTaskAssignee.employee_id).where(DailyTaskAssignee.daily_task_id == daily_task_id)
    )
    return [row[0] for row in result.all()]


async def list_employee_ids_for_templates(db: AsyncSession, daily_task_ids) -> Dict[str, List]:
    """Batched version of list_employee_ids for a list view - one query,
    grouped by template id, instead of one query per template."""
    if not daily_task_ids:
        return {}
    result = await db.execute(
        select(DailyTaskAssignee.daily_task_id, DailyTaskAssignee.employee_id).where(
            DailyTaskAssignee.daily_task_id.in_(list(daily_task_ids))
        )
    )
    grouped: Dict[str, List] = {}
    for daily_task_id, employee_id in result.all():
        grouped.setdefault(str(daily_task_id), []).append(employee_id)
    return grouped


async def replace_for_template(db: AsyncSession, daily_task_id, employee_ids) -> None:
    from sqlalchemy import delete
    await db.execute(delete(DailyTaskAssignee).where(DailyTaskAssignee.daily_task_id == daily_task_id))
    for employee_id in employee_ids:
        db.add(DailyTaskAssignee(id=uuid.uuid4(), daily_task_id=daily_task_id, employee_id=employee_id))
    await db.flush()
