import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.users as users_repo

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_EXPIRED = "expired"
SUBSCRIPTION_SUSPENDED = "suspended"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


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
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


async def resolve_subscription_status(db: AsyncSession, company) -> str:
    """Self-heal on read: an active subscription whose end date has passed
    is persisted as expired. Ported unchanged from the Mongo implementation."""
    if company.subscription_status == SUBSCRIPTION_ACTIVE and company.subscription_end_date and \
            company.subscription_end_date < datetime.now(timezone.utc):
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
