import uuid
from typing import Collection, List, Optional, Tuple

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.models import SalesLeadActivity


async def insert_event(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    event_type: str,
    actor_user_id: uuid.UUID,
    before: Optional[dict],
    after: Optional[dict],
    note: Optional[str],
    metadata: dict,
    correlation_id: uuid.UUID,
) -> None:
    await db.execute(
        insert(SalesLeadActivity).values(
            id=uuid.uuid4(),
            lead_id=lead_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            before_data=before,
            after_data=after,
            note=note,
            event_metadata=metadata,
            correlation_id=correlation_id,
        )
    )


async def list_for_lead(
    db: AsyncSession, lead_id: uuid.UUID, limit: int, offset: int, event_types: Optional[Collection[str]] = None
) -> Tuple[List[Tuple[SalesLeadActivity, Optional[str]]], int]:
    """Newest first (seq is monotonic, so events written within one transaction keep their order), with the
    actor's name joined in the same query. `event_types` (when given) is an ALLOW-list: only those event types are
    returned and counted - the caller decides which kinds of event this reader may see (services/activity.py)."""
    conds = [SalesLeadActivity.lead_id == lead_id]
    if event_types is not None:
        conds.append(SalesLeadActivity.event_type.in_(sorted(event_types)))
    total = (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(*conds))).scalar_one()
    result = await db.execute(
        select(SalesLeadActivity, User.name)
        .outerjoin(User, User.id == SalesLeadActivity.actor_user_id)
        .where(*conds)
        .order_by(SalesLeadActivity.seq.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(event, actor_name) for event, actor_name in result.all()], total
