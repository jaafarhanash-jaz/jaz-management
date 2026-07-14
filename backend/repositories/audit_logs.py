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


async def list_for_entities(db: AsyncSession, entity_type: str, entity_ids, limit: int = 500) -> List[AuditLog]:
    """Chronological (oldest first) activity for a specific set of entities
    - e.g. every message id in one thread - matching the old activity-log
    timeline ordering."""
    if not entity_ids:
        return []
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id.in_(list(entity_ids)))
        .order_by(AuditLog.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
