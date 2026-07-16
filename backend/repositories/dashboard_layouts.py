import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import DashboardLayout


async def get_by_user(db: AsyncSession, user_id) -> Optional[DashboardLayout]:
    result = await db.execute(select(DashboardLayout).where(DashboardLayout.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert(db: AsyncSession, user_id, layout: list) -> DashboardLayout:
    stmt = pg_insert(DashboardLayout).values(id=uuid.uuid4(), user_id=user_id, layout=layout)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DashboardLayout.user_id],
        set_={"layout": layout},
    ).returning(DashboardLayout)
    result = await db.execute(stmt)
    await db.flush()
    return result.scalar_one()
