from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IdempotencyKey


async def get(db: AsyncSession, key: str) -> Optional[IdempotencyKey]:
    result = await db.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, *, key: str, company_id, created_by, endpoint: str, response_body: dict) -> IdempotencyKey:
    record = IdempotencyKey(
        key=key,
        company_id=company_id,
        created_by=created_by,
        endpoint=endpoint,
        response_body=response_body,
    )
    db.add(record)
    await db.flush()
    return record
