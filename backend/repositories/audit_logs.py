import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog


async def create(db: AsyncSession, **fields) -> AuditLog:
    fields.setdefault("id", uuid.uuid4())
    entry = AuditLog(**fields)
    db.add(entry)
    await db.flush()
    return entry


async def list_for_company(db: AsyncSession, company_id, entity_type: str, limit: int = 200) -> List[AuditLog]:
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.company_id == company_id, AuditLog.entity_type == entity_type)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
