import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import PaymentTransaction


async def create(db: AsyncSession, **fields) -> PaymentTransaction:
    fields.setdefault("id", uuid.uuid4())
    tx = PaymentTransaction(**fields)
    db.add(tx)
    await db.flush()
    return tx


async def get_by_session_id(db: AsyncSession, session_id: str) -> Optional[PaymentTransaction]:
    result = await db.execute(select(PaymentTransaction).where(PaymentTransaction.session_id == session_id))
    return result.scalar_one_or_none()
