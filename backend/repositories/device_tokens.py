import uuid
from typing import List

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import DeviceToken


async def upsert(db: AsyncSession, *, user_id, token: str, platform: str, app_version) -> DeviceToken:
    """Register or refresh a device token. `token` is the conflict target,
    not `(user_id, token)` - a token identifies one physical app install,
    so a device re-registering under a different account (logout/login as
    someone else on the same phone) reassigns this row's `user_id` rather
    than creating a duplicate. `last_seen_at` is bumped via `func.now()`
    inline so it updates even when nothing else in the row changed."""
    stmt = pg_insert(DeviceToken).values(
        id=uuid.uuid4(), user_id=user_id, token=token, platform=platform,
        app_version=app_version, last_seen_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DeviceToken.token],
        set_={
            "user_id": user_id,
            "platform": platform,
            "app_version": app_version,
            "last_seen_at": func.now(),
        },
    ).returning(DeviceToken)
    result = await db.execute(stmt)
    await db.flush()
    return result.scalar_one()


async def list_by_user(db: AsyncSession, user_id) -> List[DeviceToken]:
    result = await db.execute(
        select(DeviceToken).where(DeviceToken.user_id == user_id).order_by(DeviceToken.last_seen_at.desc())
    )
    return list(result.scalars().all())


async def delete_by_token_and_user(db: AsyncSession, token: str, user_id) -> bool:
    """Scoped to `user_id` so one account can never unregister another
    account's device by guessing/observing its token."""
    result = await db.execute(delete(DeviceToken).where(DeviceToken.token == token, DeviceToken.user_id == user_id))
    return result.rowcount > 0


async def list_tokens_for_user(db: AsyncSession, user_id) -> List[str]:
    """Bare token strings only - used by services/push.py, which never
    needs the full row."""
    result = await db.execute(select(DeviceToken.token).where(DeviceToken.user_id == user_id))
    return list(result.scalars().all())


async def delete_tokens(db: AsyncSession, tokens: List[str]) -> None:
    """Cleanup for tokens FCM reports as permanently invalid (see
    services/push.py) - best-effort, no return value needed."""
    if not tokens:
        return
    await db.execute(delete(DeviceToken).where(DeviceToken.token.in_(tokens)))
