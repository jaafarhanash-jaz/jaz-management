from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import RefreshToken


async def create(db: AsyncSession, user_id, token_hash: str, expires_at: datetime) -> RefreshToken:
    row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(row)
    await db.flush()
    return row


async def get_by_hash(db: AsyncSession, token_hash: str) -> Optional[RefreshToken]:
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    return result.scalar_one_or_none()


async def revoke(db: AsyncSession, token: RefreshToken, *, replaced_by_id=None) -> None:
    token.revoked_at = datetime.now(timezone.utc)
    if replaced_by_id is not None:
        token.replaced_by_id = replaced_by_id
    await db.flush()


async def revoke_all_for_user(db: AsyncSession, user_id) -> None:
    """Defensive revocation of every active token for a user - used when a
    revoked token is presented again (reuse => likely theft)."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
