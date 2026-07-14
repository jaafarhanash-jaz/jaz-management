import uuid
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DailyTask


async def list_by_company(db: AsyncSession, company_id) -> List[DailyTask]:
    result = await db.execute(select(DailyTask).where(DailyTask.company_id == company_id))
    return list(result.scalars().all())


async def get_in_company(db: AsyncSession, template_id, company_id) -> Optional[DailyTask]:
    result = await db.execute(
        select(DailyTask).where(DailyTask.id == template_id, DailyTask.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_active_for_employee(db: AsyncSession, company_id, employee_id) -> List[DailyTask]:
    from models import DailyTaskAssignee
    result = await db.execute(
        select(DailyTask)
        .join(DailyTaskAssignee, DailyTaskAssignee.daily_task_id == DailyTask.id)
        .where(
            DailyTask.company_id == company_id,
            DailyTask.is_active.is_(True),
            DailyTaskAssignee.employee_id == employee_id,
        )
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> DailyTask:
    fields.setdefault("id", uuid.uuid4())
    template = DailyTask(**fields)
    db.add(template)
    await db.flush()
    return template


async def delete_by_id(db: AsyncSession, template_id, company_id) -> bool:
    result = await db.execute(
        delete(DailyTask).where(DailyTask.id == template_id, DailyTask.company_id == company_id)
    )
    return result.rowcount > 0
