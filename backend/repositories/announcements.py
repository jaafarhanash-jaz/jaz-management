import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Announcement


async def create(db: AsyncSession, **fields) -> Announcement:
    fields.setdefault("id", uuid.uuid4())
    announcement = Announcement(**fields)
    db.add(announcement)
    await db.flush()
    return announcement


async def list_by_company(db: AsyncSession, company_id, limit: int = 50) -> List[Announcement]:
    result = await db.execute(
        select(Announcement)
        .where(Announcement.company_id == company_id)
        .order_by(Announcement.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
