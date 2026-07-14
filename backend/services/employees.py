import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.users as users_repo
from services.admin import parse_uuid
from services.auth import hash_password

UPDATE_ALLOWED_FIELDS = ["name", "phone", "department", "position", "status", "avatar"]
PROFILE_ALLOWED_FIELDS = ["name", "phone", "avatar"]


def user_response(user) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "phone": user.phone,
        "name": user.name,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
        "avatar": user.avatar,
        "status": user.status,
        "department": user.department,
        "position": user.position,
    }


async def list_employees(db: AsyncSession, company_id) -> List[dict]:
    employees = await users_repo.list_employees_by_company(db, company_id)
    return [user_response(e) for e in employees]


async def create_employee(db: AsyncSession, company_id, data) -> dict:
    if await users_repo.email_taken(db, data.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    # The old Mongo implementation never checked phone uniqueness (there was
    # no constraint to violate); Postgres now enforces UNIQUE(phone) among
    # live rows, so this pre-check avoids an unhandled IntegrityError ->
    # 500 in favor of the same clean-400 shape used for email. Required for
    # Postgres correctness, not a behavioral improvement.
    if await users_repo.phone_taken(db, data.phone):
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # Enforce the company's max_employees limit (from its subscription
    # plan). Companies without a max_employees value are unlimited.
    company = await companies_repo.get_by_id(db, company_id)
    max_employees = company.max_employees if company else None
    if max_employees:
        current_count = await users_repo.count_employees_by_company(db, company_id)
        if current_count >= max_employees:
            raise HTTPException(
                status_code=400,
                detail="Employee limit reached. Please upgrade your subscription plan to add more employees.",
            )

    employee = await users_repo.create(
        db,
        email=data.email,
        phone=data.phone,
        password=hash_password(data.password),
        name=data.name,
        role="employee",
        company_id=company_id,
        department=data.department,
        position=data.position,
        status="active",
    )
    await db.flush()
    await db.refresh(employee)
    return user_response(employee)


async def update_employee(db: AsyncSession, company_id, employee_id: str, updates: dict) -> dict:
    # updates arrives as a raw dict (unvalidated), same contract as the old
    # implementation - whitelist filtering is the only guard.
    safe_updates = {k: v for k, v in updates.items() if k in UPDATE_ALLOWED_FIELDS}
    if updates.get("password"):
        safe_updates["password"] = hash_password(updates["password"])
    if not safe_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    for field, value in safe_updates.items():
        setattr(employee, field, value)
    await db.flush()
    return {"message": "Employee updated successfully"}


async def delete_employee(db: AsyncSession, company_id, employee_id: str) -> dict:
    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    # Soft delete per the approved plan (was a hard delete_one in Mongo).
    # Observable behavior is unchanged: the employee vanishes from every
    # listing and can no longer log in (see get_current_user/login's
    # deleted_at IS NULL filter) - see the migration plan's soft-delete
    # section for why this isn't a behavior change.
    employee.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    return {"message": "Employee deleted successfully"}


async def update_own_profile(db: AsyncSession, user_id: str, updates: dict) -> dict:
    filtered = {k: v for k, v in updates.items() if k in PROFILE_ALLOWED_FIELDS}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    user = await users_repo.get_by_id(db, parse_uuid(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in filtered.items():
        setattr(user, field, value)
    await db.flush()
    return {"message": "Profile updated successfully"}
