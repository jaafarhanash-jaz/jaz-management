import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CompanyNotification


async def create(db: AsyncSession, **fields) -> CompanyNotification:
    fields.setdefault("id", uuid.uuid4())
    broadcast = CompanyNotification(**fields)
    db.add(broadcast)
    await db.flush()
    return broadcast


async def list_all(db: AsyncSession, limit: int = 50, offset: int = 0) -> List[CompanyNotification]:
    result = await db.execute(
        select(CompanyNotification)
        .order_by(CompanyNotification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, broadcast_id) -> Optional[CompanyNotification]:
    result = await db.execute(select(CompanyNotification).where(CompanyNotification.id == broadcast_id))
    return result.scalar_one_or_none()


async def get_by_idempotency_key(db: AsyncSession, idempotency_key: str) -> Optional[CompanyNotification]:
    result = await db.execute(
        select(CompanyNotification).where(CompanyNotification.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()
