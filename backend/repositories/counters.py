from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Counter


async def increment_and_get(db: AsyncSession, key: str) -> int:
    """Atomic upsert-and-increment, equivalent to Mongo's
    find_one_and_update(upsert=True, $inc). INSERT ... ON CONFLICT DO
    UPDATE is race-free under Postgres's row-level locking, same guarantee
    the old find_one_and_update pattern relied on."""
    stmt = insert(Counter).values(id=key, value=1)
    stmt = stmt.on_conflict_do_update(index_elements=[Counter.id], set_={"value": Counter.value + 1})
    stmt = stmt.returning(Counter.value)
    result = await db.execute(stmt)
    return result.scalar_one()
