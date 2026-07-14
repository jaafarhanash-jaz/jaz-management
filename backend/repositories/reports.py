import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Report


async def list_by_company(db: AsyncSession, company_id) -> List[Report]:
    result = await db.execute(
        select(Report).where(Report.company_id == company_id).order_by(Report.created_at.desc())
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Report:
    fields.setdefault("id", uuid.uuid4())
    report = Report(**fields)
    db.add(report)
    await db.flush()
    return report
