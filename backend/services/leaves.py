from datetime import date as date_type, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.attendance as attendance_repo
import repositories.audit_logs as audit_logs_repo
import repositories.companies as companies_repo
import repositories.leaves as leaves_repo
import repositories.users as users_repo
import services.group_resolution as group_resolution
import services.leave_attendance as leave_attendance
import services.notifications as notifications_service
import services.schedule_resolution as schedule_resolution
from services.admin import parse_uuid
from services.attendance_calc_engine import compute_shift_end_datetime

# ---------------------------------------------------------------------------
# Employee Leave (Feature A) - a fully independent domain, never an
# Attendance row or Attendance.status value (see services/leave_attendance.py
# for the read-time exemption/annotation layer). Role checks happen in the
# route handlers (server.py), matching this codebase's convention
# everywhere else - this layer trusts it was already checked.
#
# Concurrency model for "no two overlapping APPROVED leaves for the same
# employee": every path that can produce a new/updated 'approved' row
# (owner-direct grant, and approve()) goes through
# _validate_and_lock_for_approval, which acquires a transaction-scoped
# Postgres advisory lock keyed on the employee id (serializing concurrent
# approval attempts for that same employee), locks that employee's current
# approved rows with SELECT ... FOR UPDATE, and re-checks overlap against
# the now-guaranteed-fresh set before writing. The leaves table's own
# EXCLUDE constraint (models.py) is the unconditional final backstop
# regardless of whether this path is ever bypassed.
# ---------------------------------------------------------------------------

DURATION_TYPES = ("time_based", "full_day", "remaining_of_day")
CATEGORY_LEAVE = "leave"

_NOTIFICATION_COPY = {
    "leave_approved": ("تمت الموافقة على طلب إجازتك", "Your leave request was approved"),
    "leave_rejected": ("تم رفض طلب إجازتك", "Your leave request was rejected"),
    "leave_granted": ("تم منحك إجازة", "You have been granted leave"),
    "leave_revoked": ("تم إلغاء إجازتك المعتمدة", "Your approved leave has been revoked"),
}


def _response(leave, *, employee_name: Optional[str] = None) -> dict:
    def iso(value):
        return value.isoformat() if value is not None else None

    return {
        "id": str(leave.id),
        "company_id": str(leave.company_id),
        "employee_id": str(leave.employee_id),
        "employee_name": employee_name,
        "duration_type": leave.duration_type,
        "start_date": iso(leave.start_date) if hasattr(leave.start_date, "isoformat") else leave.start_date,
        "end_date": iso(leave.end_date) if hasattr(leave.end_date, "isoformat") else leave.end_date,
        "start_at": iso(leave.start_at),
        "end_at": iso(leave.end_at),
        "reason": leave.reason,
        "status": leave.status,
        "origin": leave.origin,
        "requested_by": str(leave.requested_by),
        "approved_by": str(leave.approved_by) if leave.approved_by else None,
        "approved_at": iso(leave.approved_at),
        "rejected_by": str(leave.rejected_by) if leave.rejected_by else None,
        "rejected_at": iso(leave.rejected_at),
        "rejection_reason": leave.rejection_reason,
        "cancelled_by": str(leave.cancelled_by) if leave.cancelled_by else None,
        "cancelled_at": iso(leave.cancelled_at),
        "cancellation_reason": leave.cancellation_reason,
        "created_at": iso(leave.created_at),
        "updated_at": iso(leave.updated_at),
    }


def _require_reason(raw: Optional[str]) -> str:
    reason = (raw or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    return reason


def _parse_iso_date(value: str, field: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD)")


def _resolve_time_based(data) -> tuple:
    if not data.date:
        raise HTTPException(status_code=400, detail="date is required for time_based leave")
    if not data.start_time or not data.end_time:
        raise HTTPException(status_code=400, detail="start_time and end_time are required for time_based leave")
    if data.start_time == data.end_time:
        raise HTTPException(status_code=400, detail="end_time must differ from start_time")
    shift_date = _parse_iso_date(data.date, "date")
    start_at = leave_attendance.combine_date_and_time(shift_date, data.start_time)
    end_at = compute_shift_end_datetime(shift_date, data.start_time, data.end_time)
    return shift_date, shift_date, start_at, end_at


def _resolve_full_day(data) -> tuple:
    if not data.start_date:
        raise HTTPException(status_code=400, detail="start_date is required for full_day leave")
    start_date = _parse_iso_date(data.start_date, "start_date")
    end_date = _parse_iso_date(data.end_date, "end_date") if data.end_date else start_date
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date cannot be before start_date")
    start_at = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_at = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return start_date, end_date, start_at, end_at


async def _resolve_group_schedule(db: AsyncSession, employee_id, company_id, group_id: Optional[str]):
    """Reuses Rule 1.2's exact deterministic disambiguation policy
    (already established for check-in) to resolve 'today's effective
    schedule' when there is no open Attendance session to anchor
    remaining_of_day leave to: an explicit group_id is honoured if it's
    one of the employee's active memberships; a single active group is
    used automatically; several active groups with the SAME resolved
    schedule are fine; several active groups with DIFFERENT schedules are
    an explicit ambiguity error, never a random pick; zero active groups
    falls back to the employee's own direct schedule / the company
    default, exactly like the legacy (ungrouped) attendance path."""
    groups = await group_resolution.list_active_groups_for_employee(db, employee_id=employee_id, company_id=company_id)

    if group_id:
        match = next((g for g in groups if str(g.id) == str(group_id)), None)
        if not match:
            raise HTTPException(status_code=400, detail="group_id is not one of your active group memberships")
        policy = await group_resolution.to_policy(db, match)
        return policy.schedule

    if not groups:
        user = await users_repo.get_by_id(db, employee_id)
        schedule_id = user.schedule_id if user else None
        return await schedule_resolution.get_effective_schedule_for_employee(
            db, schedule_id=schedule_id, company_id=company_id,
        )

    policies = [await group_resolution.to_policy(db, g) for g in groups]
    distinct_schedules = {
        (p.schedule.start_time, p.schedule.end_time) for p in policies if p.schedule is not None
    }
    if len(distinct_schedules) > 1:
        raise HTTPException(
            status_code=400,
            detail="You belong to multiple active groups with different schedules - specify group_id",
        )
    return policies[0].schedule if policies else None


async def _resolve_remaining_of_day(
    db: AsyncSession, employee_id, company_id, *, group_id: Optional[str] = None,
) -> tuple:
    """START = now(); END = the employee's effective scheduled end for
    today, authoritatively resolved by the backend - never client-
    supplied, never a naive 23:59:59 default. If the employee currently
    has an open Attendance session, that session's OWN snapshotted
    schedule anchors the resolution (it may be a shift that started
    yesterday - an overnight shift still open past midnight resolves
    against ITS shift date, not today's calendar date). Otherwise, today's
    date is resolved against the employee's current group/schedule
    configuration. Resolved once, at creation time, and never
    recomputed later (historical determinism, Rule 2.10)."""
    now = datetime.now(timezone.utc)

    open_session = await attendance_repo.get_latest_open_for_employee(db, employee_id)
    if open_session is not None:
        if not open_session.scheduled_start_time or not open_session.scheduled_end_time:
            raise HTTPException(
                status_code=400, detail="Cannot determine an effective work schedule for your current session",
            )
        shift_date = open_session.date if isinstance(open_session.date, date_type) else date_type.fromisoformat(open_session.date)
        end_at = compute_shift_end_datetime(shift_date, open_session.scheduled_start_time, open_session.scheduled_end_time)
        if now >= end_at:
            raise HTTPException(status_code=400, detail="Your current shift has already ended")
        return shift_date, shift_date, now, end_at

    schedule = await _resolve_group_schedule(db, employee_id, company_id, group_id)
    if schedule is None:
        raise HTTPException(status_code=400, detail="Cannot determine an effective work schedule for today")
    today = now.date()
    end_at = compute_shift_end_datetime(today, schedule.start_time, schedule.end_time)
    if now >= end_at:
        raise HTTPException(status_code=400, detail="Today's effective work schedule has already ended")
    return today, today, now, end_at


async def _validate_and_lock_for_approval(db: AsyncSession, employee_id, start_at, end_at, *, exclude_id=None) -> None:
    """Transactional overlap revalidation - see module docstring. Must be
    called for every path that writes status='approved', including the
    Owner-direct-grant path (which is, functionally, an immediate
    approval)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:eid)::bigint)"), {"eid": str(employee_id)})
    locked = await leaves_repo.lock_approved_for_employee(db, employee_id)
    for other in locked:
        if exclude_id and other.id == exclude_id:
            continue
        if other.start_at < end_at and other.end_at > start_at:
            raise HTTPException(status_code=409, detail="This overlaps an existing approved leave for this employee")


async def _reject_if_overlaps_pending_creation(db: AsyncSession, employee_id, start_at, end_at) -> None:
    """Create-time check for an employee-submitted (pending) request -
    only APPROVED leave can conflict at creation time; other PENDING
    requests are allowed to coexist (only one can ultimately be
    approved - see _validate_and_lock_for_approval, which re-checks at
    approval time)."""
    conflicts = await leaves_repo.list_approved_overlapping(db, employee_id, start_at, end_at)
    if conflicts:
        raise HTTPException(status_code=409, detail="This overlaps an existing approved leave for this employee")


async def _audit(db: AsyncSession, *, company_id, actor_id, leave, action: str, reason: Optional[str], old_status: Optional[str]) -> None:
    await audit_logs_repo.create(
        db, company_id=company_id, user_id=actor_id, entity_type="leave", entity_id=leave.id,
        action=action,
        old_values={"status": old_status} if old_status is not None else None,
        new_values={"status": leave.status},
        reason=reason,
    )


async def _notify_owner_new_request(db: AsyncSession, company_id, leave, employee) -> None:
    company = await companies_repo.get_by_id(db, company_id)
    if not company:
        return
    owner = await users_repo.get_by_id(db, company.owner_id)
    is_ar = bool(owner and owner.language == "ar")
    await notifications_service.publish(
        db, user_id=company.owner_id, company_id=company_id,
        category=CATEGORY_LEAVE, type="leave_requested",
        title="طلب إجازة جديد" if is_ar else "New Leave Request",
        message=(
            f"قدّم الموظف {employee.name} طلب إجازة." if is_ar
            else f"{employee.name} submitted a leave request."
        ),
        entity_type="leave", entity_id=leave.id, action_url="/company-owner/leave",
        sender_id=employee.id, sender_name=employee.name,
    )


async def _notify_employee(db: AsyncSession, leave, notif_type: str, *, actor_name: str) -> None:
    employee = await users_repo.get_by_id(db, leave.employee_id)
    if not employee:
        return
    is_ar = employee.language == "ar"
    title_ar, title_en = _NOTIFICATION_COPY[notif_type]
    await notifications_service.publish(
        db, user_id=employee.id, company_id=leave.company_id,
        category=CATEGORY_LEAVE, type=notif_type,
        title=title_ar if is_ar else title_en,
        message=title_ar if is_ar else title_en,
        entity_type="leave", entity_id=leave.id, action_url="/employee/leave",
        sender_id=None, sender_name=actor_name,
    )


async def _create(
    db: AsyncSession, *, company_id, employee_id, data, origin: str, status: str,
    requested_by, approved_by=None, approved_at=None, group_id: Optional[str] = None,
) -> tuple:
    if data.duration_type not in DURATION_TYPES:
        raise HTTPException(status_code=400, detail=f"duration_type must be one of: {', '.join(DURATION_TYPES)}")
    reason = _require_reason(data.reason)

    if data.duration_type == "time_based":
        start_date, end_date, start_at, end_at = _resolve_time_based(data)
    elif data.duration_type == "full_day":
        start_date, end_date, start_at, end_at = _resolve_full_day(data)
    else:
        start_date, end_date, start_at, end_at = await _resolve_remaining_of_day(
            db, employee_id, company_id, group_id=group_id,
        )

    if status == "approved":
        await _validate_and_lock_for_approval(db, employee_id, start_at, end_at)
    else:
        await _reject_if_overlaps_pending_creation(db, employee_id, start_at, end_at)

    leave = await leaves_repo.create(
        db, company_id=company_id, employee_id=employee_id, duration_type=data.duration_type,
        start_date=start_date, end_date=end_date, start_at=start_at, end_at=end_at,
        reason=reason, status=status, origin=origin, requested_by=requested_by,
        approved_by=approved_by, approved_at=approved_at,
    )
    return leave, reason


async def create_for_employee(db: AsyncSession, current_user: dict, data) -> dict:
    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])
    leave, reason = await _create(
        db, company_id=company_id, employee_id=employee_id, data=data,
        origin="employee", status="pending", requested_by=employee_id,
        group_id=getattr(data, "group_id", None),
    )
    await _audit(db, company_id=company_id, actor_id=employee_id, leave=leave, action="requested", reason=reason, old_status=None)
    employee = await users_repo.get_by_id(db, employee_id)
    if employee:
        await _notify_owner_new_request(db, company_id, leave, employee)
    return _response(leave, employee_name=employee.name if employee else None)


async def create_for_owner(db: AsyncSession, current_user: dict, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    employee_id = parse_uuid(data.employee_id)
    employee = await users_repo.get_employee_in_company(db, employee_id, company_id)
    if not employee:
        raise HTTPException(status_code=400, detail="employee_id does not belong to this company")

    owner_id = parse_uuid(current_user["id"])
    now = datetime.now(timezone.utc)
    leave, reason = await _create(
        db, company_id=company_id, employee_id=employee_id, data=data,
        origin="owner", status="approved", requested_by=owner_id,
        approved_by=owner_id, approved_at=now, group_id=getattr(data, "group_id", None),
    )
    await _audit(db, company_id=company_id, actor_id=owner_id, leave=leave, action="owner_created", reason=reason, old_status=None)
    await _notify_employee(db, leave, "leave_granted", actor_name=current_user["name"])
    return _response(leave, employee_name=employee.name)


async def list_own(db: AsyncSession, current_user: dict, *, status: Optional[str] = None) -> List[dict]:
    employee_id = parse_uuid(current_user["id"])
    leaves = await leaves_repo.list_for_employee(db, employee_id, status=status)
    return [_response(lv) for lv in leaves]


async def get_own_detail(db: AsyncSession, current_user: dict, leave_id: str) -> dict:
    employee_id = parse_uuid(current_user["id"])
    leave = await leaves_repo.get_by_id_and_employee(db, parse_uuid(leave_id), employee_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    return _response(leave)


async def cancel_own(db: AsyncSession, current_user: dict, leave_id: str) -> dict:
    employee_id = parse_uuid(current_user["id"])
    leave = await leaves_repo.get_by_id_and_employee(db, parse_uuid(leave_id), employee_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if leave.status != "pending":
        raise HTTPException(status_code=400, detail="Only a pending request can be cancelled")

    old_status = leave.status
    leave.status = "cancelled"
    leave.cancelled_by = employee_id
    leave.cancelled_at = datetime.now(timezone.utc)
    await db.flush()
    # onupdate=func.now() columns (updated_at) aren't repopulated inline via
    # RETURNING on an UPDATE the way server_default is on INSERT - without
    # this, the ORM leaves the attribute "expired," and a later plain sync
    # access to it (in _response, outside any awaited SQLAlchemy call)
    # raises MissingGreenlet. An explicit, awaited refresh is the fix.
    await db.refresh(leave)

    await _audit(db, company_id=leave.company_id, actor_id=employee_id, leave=leave, action="cancelled", reason=None, old_status=old_status)
    return _response(leave)


async def list_for_owner(
    db: AsyncSession, current_user: dict, *, status: Optional[str] = None, employee_id: Optional[str] = None,
    duration_type: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
) -> List[dict]:
    company_id = parse_uuid(current_user["company_id"])
    leaves = await leaves_repo.list_for_company(
        db, company_id, status=status, employee_id=parse_uuid(employee_id) if employee_id else None,
        duration_type=duration_type,
        date_from=_parse_iso_date(date_from, "date_from") if date_from else None,
        date_to=_parse_iso_date(date_to, "date_to") if date_to else None,
    )
    names = await users_repo.get_names_by_ids(db, [lv.employee_id for lv in leaves])
    return [_response(lv, employee_name=names.get(str(lv.employee_id))) for lv in leaves]


async def get_owner_detail(db: AsyncSession, current_user: dict, leave_id: str) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    leave = await leaves_repo.get_by_id_and_company(db, parse_uuid(leave_id), company_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    names = await users_repo.get_names_by_ids(db, [leave.employee_id])
    return _response(leave, employee_name=names.get(str(leave.employee_id)))


async def approve(db: AsyncSession, current_user: dict, leave_id: str) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    leave = await leaves_repo.get_by_id_and_company(db, parse_uuid(leave_id), company_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if leave.status != "pending":
        raise HTTPException(status_code=400, detail=f"Only a pending request can be approved (current status: {leave.status})")

    await _validate_and_lock_for_approval(db, leave.employee_id, leave.start_at, leave.end_at, exclude_id=leave.id)

    old_status = leave.status
    leave.status = "approved"
    leave.approved_by = parse_uuid(current_user["id"])
    leave.approved_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(leave)  # see cancel_own's comment on why this is required

    await _audit(db, company_id=company_id, actor_id=leave.approved_by, leave=leave, action="approved", reason=None, old_status=old_status)
    await _notify_employee(db, leave, "leave_approved", actor_name=current_user["name"])
    return _response(leave)


async def reject(db: AsyncSession, current_user: dict, leave_id: str, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    leave = await leaves_repo.get_by_id_and_company(db, parse_uuid(leave_id), company_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if leave.status != "pending":
        raise HTTPException(status_code=400, detail=f"Only a pending request can be rejected (current status: {leave.status})")

    rejection_reason = (getattr(data, "rejection_reason", None) or "").strip() or None
    old_status = leave.status
    leave.status = "rejected"
    leave.rejected_by = parse_uuid(current_user["id"])
    leave.rejected_at = datetime.now(timezone.utc)
    leave.rejection_reason = rejection_reason
    await db.flush()
    await db.refresh(leave)  # see cancel_own's comment on why this is required

    await _audit(db, company_id=company_id, actor_id=leave.rejected_by, leave=leave, action="rejected", reason=rejection_reason, old_status=old_status)
    await _notify_employee(db, leave, "leave_rejected", actor_name=current_user["name"])
    return _response(leave)


async def revoke(db: AsyncSession, current_user: dict, leave_id: str, data) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    leave = await leaves_repo.get_by_id_and_company(db, parse_uuid(leave_id), company_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if leave.status != "approved":
        raise HTTPException(status_code=400, detail=f"Only an approved leave can be revoked (current status: {leave.status})")

    cancellation_reason = _require_reason(getattr(data, "cancellation_reason", None))
    old_status = leave.status
    leave.status = "cancelled"
    leave.cancelled_by = parse_uuid(current_user["id"])
    leave.cancelled_at = datetime.now(timezone.utc)
    leave.cancellation_reason = cancellation_reason
    await db.flush()
    await db.refresh(leave)  # see cancel_own's comment on why this is required

    await _audit(db, company_id=company_id, actor_id=leave.cancelled_by, leave=leave, action="revoked", reason=cancellation_reason, old_status=old_status)
    await _notify_employee(db, leave, "leave_revoked", actor_name=current_user["name"])
    return _response(leave)
