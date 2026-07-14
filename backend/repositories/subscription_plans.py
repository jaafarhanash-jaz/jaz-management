import uuid
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SubscriptionPlan


async def get_by_id(db: AsyncSession, plan_id) -> Optional[SubscriptionPlan]:
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    return result.scalar_one_or_none()


async def get_by_name(db: AsyncSession, name: str) -> Optional[SubscriptionPlan]:
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == name))
    return result.scalar_one_or_none()


async def list_all(db: AsyncSession) -> List[SubscriptionPlan]:
    result = await db.execute(select(SubscriptionPlan))
    return list(result.scalars().all())


async def list_active(db: AsyncSession) -> List[SubscriptionPlan]:
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True)))
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> SubscriptionPlan:
    fields.setdefault("id", uuid.uuid4())
    plan = SubscriptionPlan(**fields)
    db.add(plan)
    await db.flush()
    return plan


async def delete_by_id(db: AsyncSession, plan_id) -> bool:
    result = await db.execute(delete(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    return result.rowcount > 0
