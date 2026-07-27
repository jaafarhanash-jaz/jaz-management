import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Department


async def list_by_company(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[Department]:
    query = select(Department).where(
        Department.company_id == company_id, Department.deleted_at.is_(None)
    ).order_by(Department.created_at)
    if limit is not None:
        query = query.limit(limit).offset(offset or 0)
    result = await db.execute(query)
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Department:
    fields.setdefault("id", uuid.uuid4())
    department = Department(**fields)
    db.add(department)
    await db.flush()
    return department
