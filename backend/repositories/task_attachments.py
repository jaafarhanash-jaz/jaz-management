import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import TaskAttachment


async def list_by_task(db: AsyncSession, task_id) -> List[TaskAttachment]:
    result = await db.execute(select(TaskAttachment).where(TaskAttachment.task_id == task_id))
    return list(result.scalars().all())


async def list_by_tasks(db: AsyncSession, task_ids) -> List[TaskAttachment]:
    if not task_ids:
        return []
    result = await db.execute(select(TaskAttachment).where(TaskAttachment.task_id.in_(list(task_ids))))
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> TaskAttachment:
    fields.setdefault("id", uuid.uuid4())
    attachment = TaskAttachment(**fields)
    db.add(attachment)
    await db.flush()
    return attachment
