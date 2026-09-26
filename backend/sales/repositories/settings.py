"""The Sales workspace settings row (sales_settings: exactly one row, id = 1, created by migration a3f8c2d7e915)."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sales.models import SalesSettings

SETTINGS_ID = 1


async def get(db: AsyncSession, *, for_update: bool = False) -> Optional[SalesSettings]:
    """The settings row. for_update=True takes the row lock that serialises every change of it - and every automatic
    assignment, which moves the round-robin cursor - and re-reads what the database holds now (populate_existing)."""
    stmt = select(SalesSettings).where(SalesSettings.id == SETTINGS_ID)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


async def get_for_update(db: AsyncSession) -> SalesSettings:
    """The row, locked. The migration creates it; should it ever be missing, it is recreated with the defaults (automatic
    distribution OFF) rather than failing the request - ON CONFLICT makes that race-free."""
    row = await get(db, for_update=True)
    if row is None:
        await db.execute(insert(SalesSettings).values(id=SETTINGS_ID).on_conflict_do_nothing(index_elements=["id"]))
        row = await get(db, for_update=True)
    return row
