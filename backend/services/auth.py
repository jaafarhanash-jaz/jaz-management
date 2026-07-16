import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.users as users_repo
from services.storage import decode_and_validate, delete as storage_delete, download_base64, upload as storage_upload

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

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


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


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


async def enforce_company_access(db: AsyncSession, user_dict: dict) -> None:
    company_id = user_dict.get("company_id")
    if not company_id:
        return
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
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_dict = user_to_dict(user)
    await enforce_company_access(db, user_dict)

    token = create_access_token({"sub": user_dict["id"], "role": user_dict["role"]})
    # Never return the password hash to the client - matches the original
    # Mongo implementation's explicit strip before building the response.
    public_user = {k: v for k, v in user_dict.items() if k != "password"}
    return {"token": token, "user": public_user, "role": user_dict["role"]}


async def get_current_user(db: AsyncSession, token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await users_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    user_dict = user_to_dict(user)
    await enforce_company_access(db, user_dict)
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
    storage_path, _checksum = storage_upload(decoded, prefix=f"users/{user.id}/avatar")
    user.avatar_storage_path = storage_path
    user.avatar_mime_type = photo_data.mime_type
    await db.flush()
    if old_storage_path:
        storage_delete(old_storage_path)
    return _photo_response(user)


async def get_own_photo(db: AsyncSession, user_id: str) -> dict:
    """Preview - lazy-loads the base64 payload only here, same lazy-load
    boundary as every other attachment type in this app."""
    user = await users_repo.get_by_id(db, user_id)
    if not user or not user.avatar_storage_path:
        raise HTTPException(status_code=404, detail="No profile photo on file")
    response = _photo_response(user)
    response["data"] = f"data:{user.avatar_mime_type};base64,{download_base64(user.avatar_storage_path)}"
    return response


async def delete_own_photo(db: AsyncSession, user_id: str) -> dict:
    user = await users_repo.get_by_id(db, user_id)
    if not user or not user.avatar_storage_path:
        raise HTTPException(status_code=404, detail="No profile photo on file")
    storage_delete(user.avatar_storage_path)
    user.avatar_storage_path = None
    user.avatar_mime_type = None
    await db.flush()
    return {"message": "Photo deleted successfully"}


async def change_password(db: AsyncSession, user_id: str, current_password: str, new_password: str) -> dict:
    user = await users_repo.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"New password must be at least {MIN_PASSWORD_LENGTH} characters")
    user.password = hash_password(new_password)
    await db.flush()
    return {"message": "Password changed successfully"}
