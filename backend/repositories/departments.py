import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Department


async def list_by_company(db: AsyncSession, company_id) -> List[Department]:
    result = await db.execute(
        select(Department).where(Department.company_id == company_id, Department.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Department:
    fields.setdefault("id", uuid.uuid4())
    department = Department(**fields)
    db.add(department)
    await db.flush()
    return department
