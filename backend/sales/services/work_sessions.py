"""Work sessions: the persisted record behind "work hours" (migration f2b6d8a1c4e9).

The platform has no clock-in, and inventing one would be a new workflow. What every Sales person already leaves behind is a trail of
recorded work actions - each lead-timeline event (a lead entered, edited, assigned, called, decided, set up ...) is written by
services/activity.py with its actor and the database clock. `touch` is called for each of them, in the same transaction:

  * an action less than SESSION_GAP after the actor's latest session ended EXTENDS that session (ended_at, actions + 1);
  * otherwise it OPENS a new session.

Sessions are rows, never recomputed and never deleted, so past work hours stay exactly as they were recorded whatever later happens
to the leads. A session's duration is ended_at - started_at + SESSION_TAIL_CREDIT (the last action itself took time too).
One advisory transaction lock per person keeps two concurrent requests of the same person from opening two sessions at once. It is
TRIED, never waited for: a request that finds another request of the same person holding it skips its own touch (that other request
is recording the very same moment of work), so recording a session can never make a request wait or deadlock.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales.constants import SESSION_GAP

_GAP_SECONDS = int(SESSION_GAP.total_seconds())


async def touch(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Record one work action of `user_id` now (the database clock)."""
    locked = await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"sales_work_session:{user_id}"})
    if not locked.scalar_one():
        return
    extended = await db.execute(
        text(
            """
            UPDATE sales_work_sessions SET ended_at = clock_timestamp(), actions = actions + 1
             WHERE id = (SELECT id FROM sales_work_sessions
                          WHERE user_id = :u AND ended_at >= clock_timestamp() - make_interval(secs => :gap)
                          ORDER BY ended_at DESC LIMIT 1)
            RETURNING id
            """
        ),
        {"u": user_id, "gap": _GAP_SECONDS},
    )
    if extended.first() is None:
        await db.execute(
            text(
                "INSERT INTO sales_work_sessions (id, user_id, started_at, ended_at, actions) "
                "VALUES (:id, :u, clock_timestamp(), clock_timestamp(), 1)"
            ),
            {"id": uuid.uuid4(), "u": user_id},
        )
