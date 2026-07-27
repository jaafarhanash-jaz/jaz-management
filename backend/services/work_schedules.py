import re

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.work_schedules as work_schedules_repo
from services.admin import parse_uuid

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# ---------------------------------------------------------------------------
# Work Schedules (Owner CRUD) - the source of truth attendance calculation
# reads from (see services/schedule_resolution.py + services/attendance.py).
# Role checks happen in the route handlers (server.py), matching this
# codebase's convention everywhere else - this layer trusts it was already
# checked. Ownership-scoped repo lookups (never a bare get-by-id) and field
# validation (the Pydantic request models only validate shape) live here.
# ---------------------------------------------------------------------------


def _validate_time(value: str, field: str) -> None:
    if not _TIME_RE.match(value):
        raise HTTPException(status_code=400, detail=f"{field} must be in HH:MM 24-hour format")


def _validate_schedule_fields(data) -> None:
    if not data.working_days:
        raise HTTPException(status_code=400, detail="At least one working day must be selected")
    if any(day < 0 or day > 6 for day in data.working_days):
        raise HTTPException(status_code=400, detail="working_days must be between 0 (Sunday) and 6 (Saturday)")
    if data.required_hours <= 0:
        raise HTTPException(status_code=400, detail="Daily required hours must be greater than zero")

    _validate_time(data.start_time, "start_time")
    _validate_time(data.end_time, "end_time")
    if data.start_time >= data.end_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    if data.break_start_time or data.break_end_time:
        if not (data.break_start_time and data.break_end_time):
            raise HTTPException(status_code=400, detail="break_start_time and break_end_time must be set together")
        _validate_time(data.break_start_time, "break_start_time")
        _validate_time(data.break_end_time, "break_end_time")
        if not (data.start_time <= data.break_start_time < data.break_end_time <= data.end_time):
            raise HTTPException(status_code=400, detail="Break window must fall within start_time and end_time")


def schedule_response(schedule) -> dict:
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "break_start_time": schedule.break_start_time,
        "break_end_time": schedule.break_end_time,
        "required_hours": schedule.required_hours,
        "working_days": list(schedule.working_days),
        "is_default": schedule.is_default,
        "is_active": schedule.is_active,
    }


async def list_schedules(db: AsyncSession, current_user: dict) -> list:
    company_id = parse_uuid(current_user["company_id"])
    schedules = await work_schedules_repo.list_by_company(db, company_id)
    return [schedule_response(s) for s in schedules]


async def create_schedule(db: AsyncSession, current_user: dict, data) -> dict:
    _validate_schedule_fields(data)
    company_id = parse_uuid(current_user["company_id"])

    if data.is_default:
        await work_schedules_repo.unset_current_default(db, company_id)

    schedule = await work_schedules_repo.create(
        db, company_id=company_id,
        name=data.name, start_time=data.start_time, end_time=data.end_time,
        break_start_time=data.break_start_time, break_end_time=data.break_end_time,
        required_hours=data.required_hours, working_days=data.working_days,
        is_default=data.is_default, is_active=True,
    )
    await db.flush()
    return schedule_response(schedule)


async def update_schedule(db: AsyncSession, current_user: dict, schedule_id: str, data) -> dict:
    _validate_schedule_fields(data)
    company_id = parse_uuid(current_user["company_id"])

    schedule = await work_schedules_repo.get_by_id_and_company(db, parse_uuid(schedule_id), company_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Work schedule not found")

    if data.is_default and not schedule.is_default:
        await work_schedules_repo.unset_current_default(db, company_id)

    schedule.name = data.name
    schedule.start_time = data.start_time
    schedule.end_time = data.end_time
    schedule.break_start_time = data.break_start_time
    schedule.break_end_time = data.break_end_time
    schedule.required_hours = data.required_hours
    schedule.working_days = data.working_days
    schedule.is_default = data.is_default
    await db.flush()
    return schedule_response(schedule)


async def _get_owned_schedule_or_404(db: AsyncSession, current_user: dict, schedule_id: str):
    company_id = parse_uuid(current_user["company_id"])
    schedule = await work_schedules_repo.get_by_id_and_company(db, parse_uuid(schedule_id), company_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Work schedule not found")
    return schedule


async def deactivate_schedule(db: AsyncSession, current_user: dict, schedule_id: str) -> dict:
    schedule = await _get_owned_schedule_or_404(db, current_user, schedule_id)

    if schedule.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate the company's default schedule - set a different default first.",
        )
    assigned_count = await work_schedules_repo.count_employees_assigned(db, schedule.id)
    if assigned_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate a schedule that is still assigned to employees - reassign them first.",
        )

    schedule.is_active = False
    await db.flush()
    return {"message": "Work schedule deactivated"}


async def reactivate_schedule(db: AsyncSession, current_user: dict, schedule_id: str) -> dict:
    schedule = await _get_owned_schedule_or_404(db, current_user, schedule_id)
    schedule.is_active = True
    await db.flush()
    return {"message": "Work schedule reactivated"}


async def set_default_schedule(db: AsyncSession, current_user: dict, schedule_id: str) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    schedule = await _get_owned_schedule_or_404(db, current_user, schedule_id)
    if not schedule.is_active:
        raise HTTPException(status_code=400, detail="Cannot set an inactive schedule as the company default")

    await work_schedules_repo.unset_current_default(db, company_id)
    schedule.is_default = True
    await db.flush()
    return {"message": "Default work schedule updated"}
