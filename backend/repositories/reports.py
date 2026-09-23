import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Report


async def list_by_company(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[Report]:
    query = select(Report).where(Report.company_id == company_id).order_by(Report.created_at.desc())
    if limit is not None:
        query = query.limit(limit).offset(offset or 0)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_by_id_and_company(db: AsyncSession, report_id, company_id) -> Optional[Report]:
    """Ownership-scoped lookup for the Owner's PDF-export detail pull -
    same convention as every other Owner resource in this codebase."""
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_by_employee(db: AsyncSession, employee_id) -> List[Report]:
    result = await db.execute(
        select(Report).where(Report.employee_id == employee_id).order_by(Report.created_at.desc())
    )
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Report:
    fields.setdefault("id", uuid.uuid4())
    report = Report(**fields)
    db.add(report)
    await db.flush()
    return report
