from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.users as users_repo
import repositories.work_schedules as work_schedules_repo
import services.notifications as notifications_service
from services.admin import parse_uuid
from services.auth import hash_password, validate_password_strength
from services.storage import decode_and_validate, delete as storage_delete, download_base64, upload as storage_upload

UPDATE_ALLOWED_FIELDS = ["name", "phone", "department", "position", "status", "avatar", "schedule_id"]
PROFILE_ALLOWED_FIELDS = ["name", "phone", "avatar"]

# Employee CV (Part 1): PDF/DOC/DOCX only, matching the feature spec. Checked
# against both the client-supplied MIME type and the filename extension
# (either match is accepted) since browsers report inconsistent MIME types
# for legacy .doc files - a purely MIME-based check would false-reject real
# DOC uploads from some clients.
CV_ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _validate_cv_file(filename: str, mime_type: str) -> None:
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if mime_type not in CV_ALLOWED_MIME_TYPES and ext not in CV_ALLOWED_MIME_TYPES.values():
        raise HTTPException(status_code=400, detail="CV must be a PDF, DOC, or DOCX file")


def _cv_response(user) -> Optional[dict]:
    if not user.cv_storage_path:
        return None
    return {
        "filename": user.cv_original_filename,
        "mime_type": user.cv_mime_type,
        "size_bytes": user.cv_file_size,
        "uploaded_at": _iso(user.cv_uploaded_at),
    }


def user_response(user, *, schedule_name: Optional[str] = None) -> dict:
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
        "schedule_id": str(user.schedule_id) if user.schedule_id else None,
        # Display-only: the *effective* schedule name (the employee's own
        # assignment, or the company default if unassigned) - never the raw
        # schedule_id's name alone, so "no explicit assignment" still shows
        # what the employee is actually calculated against.
        "schedule_name": schedule_name,
        "cv": _cv_response(user),
    }


async def list_employees(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[dict]:
    employees = await users_repo.list_employees_by_company(db, company_id, limit=limit, offset=offset)
    # One query for all of the company's schedules instead of N+1 lookups
    # per employee.
    schedules = await work_schedules_repo.list_by_company(db, company_id)
    schedule_names_by_id = {s.id: s.name for s in schedules}
    default_schedule = next((s for s in schedules if s.is_default), None)

    def _effective_name(employee) -> Optional[str]:
        if employee.schedule_id:
            return schedule_names_by_id.get(employee.schedule_id)
        return default_schedule.name if default_schedule else None

    return [user_response(e, schedule_name=_effective_name(e)) for e in employees]


async def _store_cv(db: AsyncSession, employee, uploaded_by, cv_data) -> None:
    """Shared by create_employee (optional CV at creation) and
    upload_employee_cv (upload/replace from Edit Employee) - one storage
    write path, no duplicated validation/upload logic. Deletes the previous
    file from object storage when replacing, so nothing is orphaned."""
    _validate_cv_file(cv_data.filename, cv_data.mime_type)
    decoded = decode_and_validate(cv_data.data, cv_data.filename)
    old_storage_path = employee.cv_storage_path
    storage_path, _checksum = storage_upload(decoded, prefix=f"employees/{employee.id}/cv")
    employee.cv_storage_path = storage_path
    employee.cv_original_filename = cv_data.filename
    employee.cv_mime_type = cv_data.mime_type
    employee.cv_file_size = len(decoded)
    employee.cv_uploaded_at = datetime.now(timezone.utc)
    employee.cv_uploaded_by = uploaded_by
    await db.flush()
    if old_storage_path:
        storage_delete(old_storage_path)


async def create_employee(db: AsyncSession, company_id, created_by, data) -> dict:
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

    validate_password_strength(data.password)
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

    if getattr(data, "cv", None):
        await _store_cv(db, employee, parse_uuid(created_by), data.cv)

    if company:
        await notifications_service.publish(
            db,
            user_id=company.owner_id,
            company_id=company_id,
            category=notifications_service.CATEGORY_SYSTEM,
            type="employee_created",
            title="موظف جديد",
            message=f"تمت إضافة الموظف {employee.name} إلى الشركة",
            entity_type="user",
            entity_id=employee.id,
            action_url="/company-owner/employees",
        )

    # A freshly created employee has no explicit assignment yet, so the
    # effective schedule is whatever the company's current default is.
    default_schedule = await work_schedules_repo.get_default_for_company(db, company_id)
    return user_response(employee, schedule_name=default_schedule.name if default_schedule else None)


async def update_employee(db: AsyncSession, company_id, employee_id: str, updates: dict) -> dict:
    # updates arrives as a raw dict (unvalidated), same contract as the old
    # implementation - whitelist filtering is the only guard.
    safe_updates = {k: v for k, v in updates.items() if k in UPDATE_ALLOWED_FIELDS}
    if updates.get("password"):
        validate_password_strength(updates["password"])
        safe_updates["password"] = hash_password(updates["password"])
    if not safe_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # schedule_id is a cross-table FK, unlike every other field in
    # UPDATE_ALLOWED_FIELDS (plain scalars with no referential integrity to
    # protect) - it needs its own ownership-scoped resolution before the
    # blind setattr below, otherwise an owner could point an employee at
    # another company's schedule (or a typo'd/deleted id) and only find out
    # at flush time via an opaque FK violation. `null` is allowed through
    # unchanged - it means "fall back to the company default schedule".
    if "schedule_id" in safe_updates and safe_updates["schedule_id"] is not None:
        schedule = await work_schedules_repo.get_by_id_and_company(
            db, parse_uuid(safe_updates["schedule_id"]), company_id,
        )
        if not schedule:
            raise HTTPException(status_code=404, detail="Work schedule not found")
        safe_updates["schedule_id"] = schedule.id

    for field, value in safe_updates.items():
        setattr(employee, field, value)
    await db.flush()
    return {"message": "Employee updated successfully"}


async def upload_employee_cv(db: AsyncSession, company_id, employee_id: str, uploaded_by, cv_data) -> dict:
    """Upload (or Replace, per Edit Employee's spec) - _store_cv already
    deletes whatever CV was previously on file, so this one function covers
    both actions with no branching needed."""
    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    await _store_cv(db, employee, parse_uuid(uploaded_by), cv_data)
    return _cv_response(employee)


async def get_employee_cv(db: AsyncSession, company_id, employee_id: str) -> dict:
    """Preview/Download - lazy-loads the base64 payload only here, same
    lazy-load boundary as message/calendar attachments (list/edit views only
    ever see the lightweight metadata from user_response)."""
    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee or not employee.cv_storage_path:
        raise HTTPException(status_code=404, detail="No CV on file for this employee")
    response = _cv_response(employee)
    response["data"] = f"data:{employee.cv_mime_type};base64,{download_base64(employee.cv_storage_path)}"
    return response


async def delete_employee_cv(db: AsyncSession, company_id, employee_id: str) -> dict:
    parsed_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_id, company_id) if parsed_id else None
    if not employee or not employee.cv_storage_path:
        raise HTTPException(status_code=404, detail="No CV on file for this employee")
    storage_delete(employee.cv_storage_path)
    employee.cv_storage_path = None
    employee.cv_original_filename = None
    employee.cv_mime_type = None
    employee.cv_file_size = None
    employee.cv_uploaded_at = None
    employee.cv_uploaded_by = None
    await db.flush()
    return {"message": "CV deleted successfully"}


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

    company = await companies_repo.get_by_id(db, company_id)
    if company:
        await notifications_service.publish(
            db,
            user_id=company.owner_id,
            company_id=company_id,
            category=notifications_service.CATEGORY_SYSTEM,
            type="employee_deleted",
            title="تم حذف موظف",
            message=f"تم حذف الموظف {employee.name} من الشركة",
            entity_type="user",
            entity_id=employee.id,
            action_url="/company-owner/employees",
        )

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
