from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.employee_groups as employee_groups_repo
import repositories.surprise_attendance as surprise_repo
import repositories.users as users_repo
import services.attendance as attendance_service
import services.group_resolution as group_resolution
import services.notifications as notifications_service
import services.storage as storage_service
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# Surprise Attendance (Part 9) - an Owner-initiated "prove you're here right
# now" ping, independent of scheduled check-in/out (never touches the
# Attendance table, never requires QR). Role checks happen in the route
# handlers (server.py), matching this codebase's convention everywhere
# else. Recipient resolution and fan-out mirror
# services/company_notifications.py's own pattern (resolve once at
# creation, one Notification per resolved recipient via
# services.notifications.publish()) - scoped to a single company, never
# cross-company.
# ---------------------------------------------------------------------------


async def _resolve_recipients(db: AsyncSession, company_id, target_mode: str, target_ids) -> list:
    if target_mode == "everyone":
        employees = await users_repo.list_employees_by_company(db, company_id)
        return list(employees)

    if target_mode == "selected_employees":
        ids = [parse_uuid(i) for i in (target_ids or [])]
        if not ids:
            raise HTTPException(status_code=400, detail="target_ids is required for target_mode=selected_employees")
        employees = []
        for employee_id in ids:
            employee = await users_repo.get_employee_in_company(db, employee_id, company_id)
            if not employee:
                raise HTTPException(status_code=400, detail="One or more target_ids do not belong to this company")
            employees.append(employee)
        return employees

    if target_mode == "selected_groups":
        ids = [parse_uuid(i) for i in (target_ids or [])]
        if not ids:
            raise HTTPException(status_code=400, detail="target_ids is required for target_mode=selected_groups")
        seen_ids = set()
        employees = []
        for group_id in ids:
            group = await employee_groups_repo.get_by_id_and_company(db, group_id, company_id)
            if not group:
                raise HTTPException(status_code=400, detail="One or more target_ids do not reference a group in this company")
            for member in await employee_groups_repo.list_members(db, group.id):
                if member.id not in seen_ids:
                    seen_ids.add(member.id)
                    employees.append(member)
        return employees

    raise HTTPException(status_code=400, detail="target_mode must be one of: everyone, selected_employees, selected_groups")


def _response_summary(response, employee_name=None) -> dict:
    return {
        "id": str(response.id),
        "employee_id": str(response.employee_id),
        "employee_name": employee_name,
        "status": response.status,
        "responded_at": response.responded_at.isoformat() if response.responded_at else None,
        "latitude": response.latitude,
        "longitude": response.longitude,
        "zone_valid": response.zone_valid,
        "has_photo": response.photo_storage_path is not None,
    }


def _request_summary(request, *, requested_count=0, responded_count=0) -> dict:
    return {
        "id": str(request.id),
        "requested_by": str(request.requested_by),
        "target_mode": request.target_mode,
        "target_ids": [str(t) for t in (request.target_ids or [])],
        "require_photo": request.require_photo,
        "status": request.status,
        "created_at": request.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "requested_count": requested_count,
        "responded_count": responded_count,
    }


async def create_request(db: AsyncSession, current_user: dict, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    recipients = await _resolve_recipients(db, company_id, data.target_mode, data.target_ids)
    if not recipients:
        raise HTTPException(status_code=400, detail="No employees match the selected target")

    request = await surprise_repo.create_request(
        db, company_id=company_id, requested_by=parse_uuid(current_user["id"]),
        target_mode=data.target_mode,
        target_ids=[parse_uuid(i) for i in data.target_ids] if data.target_ids else None,
        require_photo=data.require_photo, status="pending",
        # Default response window - configurable later if needed; not
        # specified as an exact duration anywhere in the approved spec, so
        # a conservative same-day default is used rather than inventing a
        # precise business rule.
        expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0),
    )

    for employee in recipients:
        await surprise_repo.create_response_placeholder(db, request_id=request.id, employee_id=employee.id)
    await db.flush()

    for employee in recipients:
        await notifications_service.publish(
            db, user_id=employee.id, company_id=company_id,
            category=notifications_service.CATEGORY_ATTENDANCE, type="surprise_attendance_request",
            title="طلب حضور مفاجئ" if employee.language == "ar" else "Surprise Attendance Request",
            message=(
                "يرجى تسجيل حضورك الآن." if employee.language == "ar"
                else "Please confirm your attendance now."
            ),
            entity_type="surprise_attendance_request", entity_id=request.id,
            action_url="/employee/surprise-attendance",
            sender_id=parse_uuid(current_user["id"]), sender_name=current_user["name"],
        )

    return _request_summary(request, requested_count=len(recipients), responded_count=0)


async def list_requests(db: AsyncSession, current_user: dict) -> List[dict]:
    company_id = parse_uuid(current_user["company_id"])
    requests = await surprise_repo.list_requests_by_company(db, company_id)
    results = []
    for request in requests:
        responses = await surprise_repo.list_responses_for_request(db, request.id)
        responded = sum(1 for r in responses if r.status == "completed")
        results.append(_request_summary(request, requested_count=len(responses), responded_count=responded))
    return results


async def get_request_detail(db: AsyncSession, current_user: dict, request_id: str) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    request = await surprise_repo.get_request_and_company(db, parse_uuid(request_id), company_id)
    if not request:
        raise HTTPException(status_code=404, detail="Surprise attendance request not found")

    responses = await surprise_repo.list_responses_for_request(db, request.id)
    names = await users_repo.get_names_by_ids(db, [r.employee_id for r in responses])
    responded = sum(1 for r in responses if r.status == "completed")

    summary = _request_summary(request, requested_count=len(responses), responded_count=responded)
    summary["responses"] = [_response_summary(r, employee_name=names.get(str(r.employee_id))) for r in responses]
    return summary


async def list_pending_for_employee(db: AsyncSession, current_user: dict) -> List[dict]:
    employee_id = parse_uuid(current_user["id"])
    requests = await surprise_repo.list_pending_requests_for_employee(db, employee_id)
    return [_request_summary(r) for r in requests]


async def respond_to_request(db: AsyncSession, current_user: dict, request_id: str, data) -> dict:
    """Employee-side response. QR is never checked here (Rule 10 - it's a
    proof-of-presence ping, not a scheduled check-in). Zone is enforced
    only if the employee's own group currently requires one; photo is
    enforced only if THIS request requires one - the two are fully
    independent, matching Rule 9/10's design. Role check happens in the
    route handler (server.py), matching this codebase's convention -
    this layer trusts it was already checked."""
    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])
    request = await surprise_repo.get_request_and_company(db, parse_uuid(request_id), company_id)
    if not request:
        raise HTTPException(status_code=404, detail="Surprise attendance request not found")

    response = await surprise_repo.get_pending_response_for_employee(db, request.id, employee_id)
    if not response:
        raise HTTPException(status_code=404, detail="You were not targeted by this request")
    if response.status == "completed":
        raise HTTPException(status_code=400, detail="You have already responded to this request")

    # An employee may belong to several active groups at once. Zone is
    # required for this response if ANY of them requires one (the safer,
    # enforcement-erring default for a surprise/spot-check ping) - and
    # the employee is accepted at ANY location belonging to ANY of their
    # zone-requiring groups (their zone-requiring assignments' locations
    # are pooled, not picked apart by which specific group each belongs
    # to - unlike check-in, this is a presence ping, not opening a new
    # tracked session that itself needs to be attributed to one group).
    groups = await group_resolution.list_active_groups_for_employee(db, employee_id=employee_id, company_id=company_id)
    policies = [await group_resolution.to_policy(db, g) for g in groups]
    zone_requiring_locations = [loc for p in policies if p.require_zone for loc in p.locations]
    require_zone = any(p.require_zone for p in policies)

    zone_valid = None
    if require_zone:
        if data.latitude is None or data.longitude is None:
            raise HTTPException(status_code=400, detail="خدمة الموقع (GPS) مطلوبة للرد على هذا الطلب.")
        matched_location_id, _ = attendance_service.match_group_location(
            zone_requiring_locations, None, data.latitude, data.longitude, require_qr=False, require_zone=True,
        )
        zone_valid = matched_location_id is not None
        if not zone_valid:
            raise HTTPException(status_code=400, detail="أنت خارج النطاق المسموح لمجموعتك.")

    if request.require_photo:
        if not data.photo_data or not data.photo_filename:
            raise HTTPException(status_code=400, detail="مطلوب إثبات صورة للرد على هذا الطلب.")
        decoded = storage_service.decode_and_validate(data.photo_data, data.photo_filename, data.photo_mime_type)
        storage_path, _ = await storage_service.upload(decoded, prefix="surprise_attendance_photos")
        response.photo_storage_path = storage_path

    response.status = "completed"
    response.responded_at = datetime.now(timezone.utc)
    response.latitude = data.latitude
    response.longitude = data.longitude
    response.zone_valid = zone_valid
    await db.flush()

    remaining_pending = await surprise_repo.count_pending_responses(db, request.id)
    if remaining_pending == 0:
        request.status = "completed"
        await db.flush()

    return _response_summary(response)
