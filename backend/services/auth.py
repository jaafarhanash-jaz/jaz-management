import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import anyio
from fastapi import HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.refresh_tokens as refresh_tokens_repo
import repositories.users as users_repo
from services.storage import decode_and_validate, delete as storage_delete, download_base64, upload as storage_upload

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable must be set - refusing to start "
        "with an insecure default signing key."
    )
ALGORITHM = "HS256"
# Short-lived on purpose now that refresh tokens exist to keep sessions
# alive silently - a leaked access token is only useful for an hour,
# vs. the old 24h. Both web and mobile clients must be deployed with
# their refresh-on-401 interceptor before this ships, or users get
# logged out every hour instead of silently refreshed.
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30

SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_EXPIRED = "expired"
SUBSCRIPTION_SUSPENDED = "suspended"

# Profile photo (User Profile & Account Settings) - image-only, tighter cap
# than the generic 10MB attachment ceiling in services/storage.py since a
# profile photo is always small and displayed inline, never downloaded.
PHOTO_ALLOWED_MIME_TYPES = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
}
MAX_PHOTO_BYTES = 5 * 1024 * 1024

# No formal password policy exists anywhere in this codebase today (Pydantic
# models accept any non-empty string) - this is the one new rule introduced
# for password changes, chosen to not be stricter than the shortest existing
# seeded demo password ("emp123", 6 chars).
MIN_PASSWORD_LENGTH = 6


def validate_password_strength(password: str) -> None:
    """Applied everywhere a password is ever set (self-service change,
    employee creation/update, company/owner creation, admin resets) - the
    minimum was previously only enforced on the self-service change-
    password path, letting an owner or super_admin set an account up
    with an empty or single-character password with no guardrail at all."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


async def hash_password(password: str) -> str:
    # bcrypt is deliberately CPU-slow (100ms+ per call) - calling it directly
    # would block the single event loop for that whole duration, stalling
    # every other in-flight request (dashboard loads, polls, everything) on
    # this worker, not just the one doing the hashing. to_thread runs it on
    # a worker thread instead so the loop stays free.
    return await anyio.to_thread.run_sync(pwd_context.hash, password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return await anyio.to_thread.run_sync(pwd_context.verify, plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _hash_token(raw_token: str) -> str:
    """Only the hash is ever persisted - a DB leak alone can't be replayed
    as a live session, same principle as password hashing."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def _issue_refresh_token(db: AsyncSession, user_id: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await refresh_tokens_repo.create(db, user_id, _hash_token(raw_token), expires_at)
    return raw_token


async def rotate_refresh_token(db: AsyncSession, raw_token: str) -> dict:
    """Verifies a refresh token, rotates it (old one revoked, a fresh one
    issued), and returns a new access+refresh token pair - same response
    shape as `login()`. Presenting an already-revoked token is treated as
    reuse/theft: every active token for that user is revoked defensively,
    forcing a full re-login everywhere."""
    token_hash = _hash_token(raw_token)
    stored = await refresh_tokens_repo.get_by_hash(db, token_hash)
    if not stored:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if stored.revoked_at is not None:
        # Commit before raising - `get_db` rolls back the session on any
        # exception, which would otherwise silently discard this
        # defensive revoke-everything the moment it's needed most.
        await refresh_tokens_repo.revoke_all_for_user(db, stored.user_id)
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token already used")

    if stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user = await users_repo.get_by_id(db, str(stored.user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    user_dict = user_to_dict(user)
    await enforce_company_access(db, user_dict)

    new_raw_token = secrets.token_urlsafe(32)
    new_expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    new_row = await refresh_tokens_repo.create(db, stored.user_id, _hash_token(new_raw_token), new_expires_at)
    await refresh_tokens_repo.revoke(db, stored, replaced_by_id=new_row.id)

    access_token = create_access_token({"sub": user_dict["id"], "role": user_dict["role"]})
    public_user = {k: v for k, v in user_dict.items() if k != "password"}
    return {"token": access_token, "refresh_token": new_raw_token, "user": public_user, "role": user_dict["role"]}


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    """Logout - idempotent no-op if the token is already gone/unknown, so
    the client can always call this safely without checking state first."""
    stored = await refresh_tokens_repo.get_by_hash(db, _hash_token(raw_token))
    if stored and stored.revoked_at is None:
        await refresh_tokens_repo.revoke(db, stored)


def _photo_response(user) -> Optional[dict]:
    if not user.avatar_storage_path:
        return None
    return {"mime_type": user.avatar_mime_type}


def user_to_dict(user) -> dict:
    """Mirrors the old Mongo doc shape (str ids, ISO datetime strings) so
    every not-yet-migrated route body that reads current_user as a plain
    dict keeps working unchanged."""
    return {
        "id": str(user.id),
        "email": user.email,
        "phone": user.phone,
        "password": user.password,
        "name": user.name,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
        "department": user.department,
        "position": user.position,
        "schedule_id": str(user.schedule_id) if user.schedule_id else None,
        "status": user.status,
        "avatar": user.avatar,
        "photo": _photo_response(user),
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


async def resolve_subscription_status(db: AsyncSession, company) -> str:
    """Self-heal on read: an active subscription whose end date has passed
    is persisted as expired. Compares DATE portions only, exactly like the
    old implementation's is_past_date - a subscription ending today stays
    active through the whole day and expires tomorrow."""
    if company.subscription_status == SUBSCRIPTION_ACTIVE and company.subscription_end_date and \
            company.subscription_end_date.date() < datetime.now(timezone.utc).date():
        company.subscription_status = SUBSCRIPTION_EXPIRED
        await db.flush()
        return SUBSCRIPTION_EXPIRED
    return company.subscription_status


async def enforce_company_access(db: AsyncSession, user_dict: dict, *, company=None) -> None:
    company_id = user_dict.get("company_id")
    if not company_id:
        return
    if company is None:
        company = await companies_repo.get_by_id(db, company_id)
    if not company:
        return
    effective_status = await resolve_subscription_status(db, company)
    if effective_status in (SUBSCRIPTION_EXPIRED, SUBSCRIPTION_SUSPENDED):
        raise HTTPException(
            status_code=403,
            detail={
                "field": "subscription",
                "status": effective_status,
                "message": f"Company subscription is {effective_status}",
                "subscription_end_date": company.subscription_end_date.isoformat()
                if company.subscription_end_date else None,
            },
        )


async def login(db: AsyncSession, email_or_phone: str, password: str) -> dict:
    user = await users_repo.get_by_email_or_phone(db, email_or_phone)
    if not user or not await verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_dict = user_to_dict(user)
    await enforce_company_access(db, user_dict)

    token = create_access_token({"sub": user_dict["id"], "role": user_dict["role"]})
    refresh_token = await _issue_refresh_token(db, user.id)
    # Never return the password hash to the client - matches the original
    # Mongo implementation's explicit strip before building the response.
    public_user = {k: v for k, v in user_dict.items() if k != "password"}
    return {"token": token, "refresh_token": refresh_token, "user": public_user, "role": user_dict["role"]}


async def get_current_user(db: AsyncSession, token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user, company = await users_repo.get_by_id_with_company(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    user_dict = user_to_dict(user)
    # This dependency runs on every single authenticated request in the
    # app - one combined query here instead of two sequential ones
    # (get_by_id, then companies_repo.get_by_id inside
    # enforce_company_access) halves the fixed per-request DB round-trip
    # cost, which is dominated by network RTT to Supabase, not query
    # complexity.
    await enforce_company_access(db, user_dict, company=company)
    return user_dict


def _validate_photo_file(filename: str, mime_type: str) -> None:
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if mime_type not in PHOTO_ALLOWED_MIME_TYPES and ext not in PHOTO_ALLOWED_MIME_TYPES.values():
        raise HTTPException(status_code=400, detail="Photo must be a JPG, PNG, WEBP, or GIF image")


async def upload_own_photo(db: AsyncSession, user_id: str, photo_data) -> dict:
    """Upload or Replace - deletes whatever photo was previously on file
    (same one-function-covers-both pattern as the employee CV upload), so
    nothing is orphaned in object storage."""
    user = await users_repo.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    _validate_photo_file(photo_data.filename, photo_data.mime_type)
    decoded = decode_and_validate(photo_data.data, photo_data.filename)
    if len(decoded) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail=f"Photo exceeds the {MAX_PHOTO_BYTES // (1024*1024)}MB limit")

    old_storage_path = user.avatar_storage_path
    storage_path, _checksum = await storage_upload(decoded, prefix=f"users/{user.id}/avatar")
    user.avatar_storage_path = storage_path
    user.avatar_mime_type = photo_data.mime_type
    await db.flush()
    if old_storage_path:
        await storage_delete(old_storage_path)
    return _photo_response(user)


async def get_own_photo(db: AsyncSession, user_id: str) -> dict:
    """Preview - lazy-loads the base64 payload only here, same lazy-load
    boundary as every other attachment type in this app."""
    user = await users_repo.get_by_id(db, user_id)
    if not user or not user.avatar_storage_path:
        raise HTTPException(status_code=404, detail="No profile photo on file")
    response = _photo_response(user)
    response["data"] = f"data:{user.avatar_mime_type};base64,{await download_base64(user.avatar_storage_path)}"
    return response


async def delete_own_photo(db: AsyncSession, user_id: str) -> dict:
    user = await users_repo.get_by_id(db, user_id)
    if not user or not user.avatar_storage_path:
        raise HTTPException(status_code=404, detail="No profile photo on file")
    await storage_delete(user.avatar_storage_path)
    user.avatar_storage_path = None
    user.avatar_mime_type = None
    await db.flush()
    return {"message": "Photo deleted successfully"}


async def change_password(db: AsyncSession, user_id: str, current_password: str, new_password: str) -> dict:
    user = await users_repo.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not await verify_password(current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    validate_password_strength(new_password)
    user.password = await hash_password(new_password)
    await db.flush()
    return {"message": "Password changed successfully"}
