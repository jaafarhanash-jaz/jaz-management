import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SubscriptionPlan


async def get_by_name(db: AsyncSession, name: str) -> Optional[SubscriptionPlan]:
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == name))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> SubscriptionPlan:
    plan = SubscriptionPlan(id=uuid.uuid4(), **fields)
    db.add(plan)
    await db.flush()
    return plan
