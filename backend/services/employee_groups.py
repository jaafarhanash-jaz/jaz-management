from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.employee_groups as employee_groups_repo
import repositories.users as users_repo
import repositories.work_locations as work_locations_repo
import repositories.work_schedules as work_schedules_repo
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# Attendance Groups / Assignments (Owner CRUD) - deliberately independent
# of Department (see the 2026-08-31 architecture decision): an employee's
# org department and their attendance group are different concepts and are
# never conflated here. Role checks happen in the route handlers
# (server.py), matching services/work_schedules.py's own convention - this
# layer trusts it was already checked.
# ---------------------------------------------------------------------------


async def _validate_group_fields(db, company_id, data) -> None:
    if not data.name or not data.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required")
    if data.grace_period_minutes is not None and data.grace_period_minutes < 0:
        raise HTTPException(status_code=400, detail="grace_period_minutes must be zero or greater")
    if data.work_schedule_id:
        schedule = await work_schedules_repo.get_by_id_and_company(db, parse_uuid(data.work_schedule_id), company_id)
        if not schedule:
            raise HTTPException(status_code=400, detail="work_schedule_id does not reference a schedule in this company")


async def _validate_and_resolve_locations(db, company_id, location_ids) -> list:
    ids = [parse_uuid(i) for i in (location_ids or [])]
    if any(i is None for i in ids):
        raise HTTPException(status_code=400, detail="One or more location_ids are not valid ids")
    if not ids:
        return []
    found = await work_locations_repo.get_by_ids_and_company(db, ids, company_id)
    if len(found) != len(set(ids)):
        raise HTTPException(status_code=400, detail="One or more location_ids do not belong to this company")
    return ids


def group_response(group, *, member_count=0, locations=None) -> dict:
    return {
        "id": str(group.id),
        "name": group.name,
        "description": group.description,
        "work_schedule_id": str(group.work_schedule_id) if group.work_schedule_id else None,
        "require_zone": group.require_zone,
        "require_qr": group.require_qr,
        "photo_proof_required": group.photo_proof_required,
        "grace_period_minutes": group.grace_period_minutes,
        "is_active": group.is_active,
        "member_count": member_count,
        "location_ids": [str(loc.id) for loc in locations] if locations is not None else [],
        "locations": [{"id": str(loc.id), "name": loc.name} for loc in locations] if locations is not None else [],
    }


async def list_groups(db: AsyncSession, current_user: dict) -> List[dict]:
    company_id = parse_uuid(current_user["company_id"])
    groups = await employee_groups_repo.list_by_company(db, company_id)
    results = []
    for group in groups:
        member_count = await employee_groups_repo.count_members(db, group.id)
        locations = await employee_groups_repo.list_locations_for_group(db, group.id)
        results.append(group_response(group, member_count=member_count, locations=locations))
    return results


async def create_group(db: AsyncSession, current_user: dict, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    await _validate_group_fields(db, company_id, data)
    location_ids = await _validate_and_resolve_locations(db, company_id, data.location_ids)

    group = await employee_groups_repo.create(
        db, company_id=company_id,
        name=data.name.strip(), description=data.description,
        work_schedule_id=parse_uuid(data.work_schedule_id) if data.work_schedule_id else None,
        require_zone=data.require_zone, require_qr=data.require_qr,
        photo_proof_required=data.photo_proof_required,
        grace_period_minutes=data.grace_period_minutes if data.grace_period_minutes is not None else 180,
        is_active=True, created_by=parse_uuid(current_user["id"]),
    )
    if location_ids:
        await employee_groups_repo.set_locations(db, group.id, location_ids)
    await db.flush()

    locations = await employee_groups_repo.list_locations_for_group(db, group.id)
    return group_response(group, member_count=0, locations=locations)


async def _get_owned_group_or_404(db: AsyncSession, current_user: dict, group_id: str):
    company_id = parse_uuid(current_user["company_id"])
    group = await employee_groups_repo.get_by_id_and_company(db, parse_uuid(group_id), company_id)
    if not group:
        raise HTTPException(status_code=404, detail="Attendance group not found")
    return group


async def update_group(db: AsyncSession, current_user: dict, group_id: str, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    await _validate_group_fields(db, company_id, data)
    group = await _get_owned_group_or_404(db, current_user, group_id)
    location_ids = await _validate_and_resolve_locations(db, company_id, data.location_ids)

    group.name = data.name.strip()
    group.description = data.description
    group.work_schedule_id = parse_uuid(data.work_schedule_id) if data.work_schedule_id else None
    group.require_zone = data.require_zone
    group.require_qr = data.require_qr
    group.photo_proof_required = data.photo_proof_required
    group.grace_period_minutes = data.grace_period_minutes if data.grace_period_minutes is not None else 180
    await employee_groups_repo.set_locations(db, group.id, location_ids)
    await db.flush()

    member_count = await employee_groups_repo.count_members(db, group.id)
    locations = await employee_groups_repo.list_locations_for_group(db, group.id)
    return group_response(group, member_count=member_count, locations=locations)


async def deactivate_group(db: AsyncSession, current_user: dict, group_id: str) -> dict:
    group = await _get_owned_group_or_404(db, current_user, group_id)
    member_count = await employee_groups_repo.count_members(db, group.id)
    if member_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate a group that still has assigned employees - reassign them first.",
        )
    group.is_active = False
    await db.flush()
    return {"message": "Attendance group deactivated"}


async def reactivate_group(db: AsyncSession, current_user: dict, group_id: str) -> dict:
    group = await _get_owned_group_or_404(db, current_user, group_id)
    group.is_active = True
    await db.flush()
    return {"message": "Attendance group reactivated"}


async def list_group_members(db: AsyncSession, current_user: dict, group_id: str) -> list:
    group = await _get_owned_group_or_404(db, current_user, group_id)
    members = await employee_groups_repo.list_members(db, group.id)
    return [{"id": str(m.id), "name": m.name, "email": m.email} for m in members]


async def _resolve_employee_and_group(db, current_user, employee_id, group_id):
    """Both re-resolved from THIS company only, so an owner can never
    reach across a tenant boundary even by guessing ids."""
    company_id = parse_uuid(current_user["company_id"])
    employee = await users_repo.get_employee_in_company(db, parse_uuid(employee_id), company_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    group = await employee_groups_repo.get_by_id_and_company(db, parse_uuid(group_id), company_id)
    if not group:
        raise HTTPException(status_code=400, detail="group_id does not reference a group in this company")
    return company_id, employee, group


async def add_group_membership(db: AsyncSession, current_user: dict, employee_id: str, group_id: str) -> dict:
    """Adds the employee to ONE more group - an employee may belong to
    several groups at once (e.g. "Sales Representatives" AND "Al-Bayaa
    Branch" simultaneously, per the approved spec's own example), so this
    is additive, never a replace. A no-op re-add (already an open member
    of this exact group) is rejected rather than silently accepted, so
    the Owner gets clear feedback instead of a confusing non-error."""
    company_id, employee, group = await _resolve_employee_and_group(db, current_user, employee_id, group_id)
    existing = await employee_groups_repo.get_open_assignment(db, employee.id, group.id)
    if existing:
        raise HTTPException(status_code=400, detail="Employee is already a member of this group")

    await employee_groups_repo.add_assignment(
        db, employee_id=employee.id, company_id=company_id, group_id=group.id,
        assigned_by=parse_uuid(current_user["id"]),
    )
    return {"message": "Employee added to group", "group_id": str(group.id)}


async def remove_group_membership(db: AsyncSession, current_user: dict, employee_id: str, group_id: str) -> dict:
    """Ends this ONE membership - any other group the employee currently
    belongs to is completely untouched, and no historical attendance is
    rewritten (Attendance.group_id already snapshots whichever group
    applied at check-in time)."""
    company_id, employee, group = await _resolve_employee_and_group(db, current_user, employee_id, group_id)
    existing = await employee_groups_repo.get_open_assignment(db, employee.id, group.id)
    if not existing:
        raise HTTPException(status_code=400, detail="Employee is not currently a member of this group")

    await employee_groups_repo.close_assignment(db, existing)
    return {"message": "Employee removed from group", "group_id": str(group.id)}


async def list_employee_groups(db: AsyncSession, current_user: dict, employee_id: str) -> list:
    """Every group this employee is CURRENTLY an open member of - zero,
    one, or many."""
    company_id = parse_uuid(current_user["company_id"])
    employee = await users_repo.get_employee_in_company(db, parse_uuid(employee_id), company_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    assignments = await employee_groups_repo.list_open_assignments_for_employee(db, employee.id)
    groups = []
    for assignment in assignments:
        group = await employee_groups_repo.get_by_id_and_company(db, assignment.group_id, company_id)
        if group:
            groups.append(group_response(group))
    return groups


async def get_assignment_history(db: AsyncSession, current_user: dict, employee_id: str) -> list:
    """Full history - note overlapping date ranges across different
    groups are expected whenever the employee belonged to more than one
    group at the same time, not a data quality issue."""
    company_id = parse_uuid(current_user["company_id"])
    employee = await users_repo.get_employee_in_company(db, parse_uuid(employee_id), company_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    rows = await employee_groups_repo.list_assignment_history_for_employee(db, employee.id)
    return [
        {
            "id": str(r.id),
            "group_id": str(r.group_id),
            "effective_from": r.effective_from.isoformat(),
            "effective_to": r.effective_to.isoformat() if r.effective_to else None,
        }
        for r in rows
    ]
