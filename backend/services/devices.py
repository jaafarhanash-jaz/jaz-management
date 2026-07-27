from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.device_tokens as device_tokens_repo
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# Device registration for push notifications (see services/push.py for the
# send side). `user_id` is always taken from the authenticated session,
# never from the request body - a client can only ever register/unregister
# a token against its own account, by construction rather than by a
# separate ownership check.
# ---------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() if value else None


def device_token_response(device) -> dict:
    return {
        "id": str(device.id),
        "user_id": str(device.user_id),
        "platform": device.platform,
        "app_version": device.app_version,
        "last_seen_at": _iso(device.last_seen_at),
        "created_at": _iso(device.created_at),
    }


async def register_device(db: AsyncSession, current_user: dict, data) -> dict:
    device = await device_tokens_repo.upsert(
        db,
        user_id=parse_uuid(current_user["id"]),
        token=data.token,
        platform=data.platform,
        app_version=data.app_version,
    )
    return device_token_response(device)


async def unregister_device(db: AsyncSession, current_user: dict, token: str) -> dict:
    deleted = await device_tokens_repo.delete_by_token_and_user(db, token, parse_uuid(current_user["id"]))
    if not deleted:
        raise HTTPException(status_code=404, detail="Device token not found")
    return {"message": "Device unregistered successfully"}


async def list_own_devices(db: AsyncSession, current_user: dict) -> List[dict]:
    devices = await device_tokens_repo.list_by_user(db, parse_uuid(current_user["id"]))
    return [device_token_response(d) for d in devices]


async def list_devices_for_user(db: AsyncSession, user_id: str) -> List[dict]:
    """Super-admin support/debugging view - route-level `require_super_admin`
    already gates access, so no ownership check is needed here."""
    parsed = parse_uuid(user_id)
    if not parsed:
        raise HTTPException(status_code=404, detail="User not found")
    devices = await device_tokens_repo.list_by_user(db, parsed)
    return [device_token_response(d) for d in devices]
