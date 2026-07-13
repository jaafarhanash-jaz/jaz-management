from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Company


async def get_by_id(db: AsyncSession, company_id) -> Optional[Company]:
    result = await db.execute(select(Company).where(Company.id == company_id, Company.deleted_at.is_(None)))
    return result.scalar_one_or_none()
