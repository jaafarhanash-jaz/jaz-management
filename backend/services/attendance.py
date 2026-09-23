import math
import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.attendance as attendance_repo
import repositories.attendance_events as attendance_events_repo
import repositories.audit_logs as audit_logs_repo
import repositories.companies as companies_repo
import repositories.employee_groups as employee_groups_repo
import repositories.users as users_repo
import services.attendance_calc_engine as calc_engine
import services.group_resolution as group_resolution
import services.holidays as holidays_service
import services.leave_attendance as leave_attendance_service
import services.notifications as notifications_service
import services.schedule_resolution as schedule_resolution
from services.admin import parse_uuid
from services.qr import generate_qr_code
from services.attendance_calc_engine import ScheduleSnapshot

DEFAULT_ATTENDANCE_RADIUS_METERS = 50.0
# Above this implied speed between two consecutive attendance points the
# movement is physically impossible for a commuting employee. Generous
# enough that normal GPS jitter or a fast highway drive never triggers it.
IMPOSSIBLE_TRAVEL_SPEED_KMH = 200.0
# Real GPS fixes essentially never report sub-1-meter accuracy; spoofing
# apps commonly report 0 or 1. Only ever used combined with the repeated-
# identical-coordinates signal - never rejects on its own.
SUSPICIOUS_GPS_ACCURACY_METERS = 1.0
REPEATED_COORDS_MIN_DAYS = 3
ATTENDANCE_EDITABLE_FIELDS = ["check_in_time", "check_out_time", "status"]


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((rlat2 - rlat1) / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def attendance_settings_for(company) -> dict:
    """Attendance settings with backward-compatible defaults: QR attendance
    stays enabled and a missing location means radius validation is
    skipped, so companies created before this feature keep working
    unchanged until their owner configures a location."""
    return {
        "latitude": company.attendance_latitude,
        "longitude": company.attendance_longitude,
        "radius_meters": company.attendance_radius_meters if company.attendance_radius_meters is not None else DEFAULT_ATTENDANCE_RADIUS_METERS,
        "qr_enabled": company.attendance_qr_enabled if company.attendance_qr_enabled is not None else True,
        "updated_at": _iso(company.attendance_settings_updated_at),
        "updated_by": str(company.attendance_settings_updated_by) if company.attendance_settings_updated_by else None,
        "updated_by_name": company.attendance_settings_updated_by_name,
        "qr_generated_at": _iso(company.qr_generated_at),
    }


async def ensure_qr_token(db: AsyncSession, company) -> str:
    """Self-heal on read: a company whose printed QR predates this feature
    gets an opaque random token and a regenerated QR image the first time
    its QR is needed. The QR encodes only this token, never company data."""
    if not company.qr_token:
        token = uuid.uuid4().hex
        company.qr_token = token
        company.qr_code = generate_qr_code(token)
        company.qr_generated_at = datetime.now(timezone.utc)
        await db.flush()
    return company.qr_token


async def regenerate_qr_token(db: AsyncSession, current_user: dict) -> dict:
    """Owner-triggered regeneration. The old token stops validating the
    instant this flushes - `validate_qr_attendance`'s exact-match check
    against the (now different) `qr_token` is the only "invalidation"
    needed, there is nothing else referencing the old value."""
    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    token = uuid.uuid4().hex
    company.qr_token = token
    company.qr_code = generate_qr_code(token)
    company.qr_generated_at = datetime.now(timezone.utc)
    await db.flush()

    return {
        "message": "QR code regenerated",
        "qr_code": company.qr_code,
        "qr_generated_at": _iso(company.qr_generated_at),
    }


async def detect_fake_gps(db: AsyncSession, employee_id, latitude: float, longitude: float, accuracy: Optional[float]) -> Optional[str]:
    """Best-effort server-side spoofing heuristics: an impossible travel
    speed against the employee's own previous attendance point is decisive
    alone; suspiciously perfect accuracy and pin-identical coordinates
    across several days must BOTH be present."""
    now = datetime.now(timezone.utc)
    records = await attendance_repo.list_recent_for_employee(db, employee_id, limit=10)

    events = []
    for record in records:
        for time_val, lat, lon in (
            (record.check_in_time, record.check_in_latitude, record.check_in_longitude),
            (record.check_out_time, record.check_out_latitude, record.check_out_longitude),
        ):
            if time_val and lat is not None:
                events.append((time_val, lat, lon))
    if events:
        last_time, last_lat, last_lon = max(events, key=lambda e: e[0])
        elapsed_hours = (now - last_time).total_seconds() / 3600
        if elapsed_hours > 0:
            distance_km = haversine_meters(latitude, longitude, last_lat, last_lon) / 1000
            # Sub-500m jumps are ignored entirely: GPS jitter can never look
            # like teleporting at that scale, whatever the time delta.
            if distance_km > 0.5 and (distance_km / elapsed_hours) > IMPOSSIBLE_TRAVEL_SPEED_KMH:
                return "تم رفض تسجيل الحضور: تم اكتشاف موقع GPS غير موثوق (سرعة انتقال غير ممكنة)."

    if accuracy is not None and accuracy <= SUSPICIOUS_GPS_ACCURACY_METERS:
        identical_days = {
            r.date for r in records
            if r.check_in_latitude == latitude and r.check_in_longitude == longitude
        }
        if len(identical_days) >= REPEATED_COORDS_MIN_DAYS:
            return "تم رفض تسجيل الحضور: تم اكتشاف موقع GPS غير موثوق (إحداثيات متطابقة بدقة مشبوهة)."

    return None


def _tag(exc: HTTPException, *, qr_valid: Optional[bool] = None, gps_valid: Optional[bool] = None) -> HTTPException:
    """Attaches unofficial attributes to a raised HTTPException so the
    check_in/check_out wrapper can log an accurate Attendance Event without
    string-matching `detail` (fragile - breaks the moment a message is
    edited). Attributes left as None (the default) mean "not applicable /
    not a QR-or-GPS-specific failure" (e.g. company-not-found), and the
    wrapper's own getattr default (True) only kicks in for exceptions
    raised entirely outside this function, past the point QR+GPS already
    passed."""
    exc.qr_valid = qr_valid
    exc.gps_valid = gps_valid
    return exc


def _normalize_platform(device_info: Optional[str]) -> Optional[str]:
    """The Flutter client sends `defaultTargetPlatform.name` verbatim as
    `device_info` ("android" / "iOS") - a substring match is enough and
    stays robust to that casing, without over-parsing a field that's
    otherwise free-text."""
    if not device_info:
        return None
    lowered = device_info.lower()
    if "android" in lowered:
        return "android"
    if "ios" in lowered:
        return "ios"
    return "other"


def match_group_location(locations, qr_code, latitude, longitude, *, require_qr: bool, require_zone: bool):
    """Picks which of the employee's group's linked WorkLocations this
    check-in/out satisfies, per the group's own require_qr/require_zone
    toggles (Rule 9 - fully independent of each other):
    - Both required: a single location must satisfy BOTH (QR match AND
      within that same location's radius) - prevents a QR photographed at
      one site from being used while physically standing in a different
      site's zone.
    - Only QR required: any location whose token matches wins, no radius
      check at all.
    - Only zone required: any location whose radius contains the point
      wins (nearest, if several overlap) - QR is not even inspected.
    - Neither required: no location gate at all; if a QR was scanned
      anyway, record which location it belongs to purely for
      traceability.
    Returns (location_id_or_None, distance_meters_or_None)."""
    if not locations:
        return None, None

    if require_qr and require_zone:
        for loc in locations:
            if qr_code and qr_code == loc.qr_token and latitude is not None:
                distance = round(haversine_meters(latitude, longitude, loc.latitude, loc.longitude), 1)
                if distance <= loc.radius_meters:
                    return loc.id, distance
        return None, None

    if require_qr:
        for loc in locations:
            if qr_code and qr_code == loc.qr_token:
                return loc.id, None
        return None, None

    if require_zone:
        best, best_distance = None, None
        for loc in locations:
            if latitude is None:
                continue
            distance = round(haversine_meters(latitude, longitude, loc.latitude, loc.longitude), 1)
            if distance <= loc.radius_meters and (best_distance is None or distance < best_distance):
                best, best_distance = loc.id, distance
        return best, best_distance

    if qr_code:
        for loc in locations:
            if qr_code == loc.qr_token:
                return loc.id, None
    return None, None


class AmbiguousGroupError(HTTPException):
    """Raised when an employee belongs to multiple active Attendance
    Groups and neither an explicit selection nor the presented QR/GPS
    lets the server determine which one deterministically - the system
    must NEVER silently guess. Distinct type so callers/tests can assert
    on it specifically, not just "some 400"."""

    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


async def _disambiguate_active_groups(db, groups: list, qr_code, latitude, longitude):
    """Deterministic resolution when an employee has 2+ active group
    memberships (Rule: never silently choose a random one) and made no
    explicit selection. Tries each candidate group's OWN policy against
    the presented QR/GPS; a group is a "match" if it has no location
    requirement at all (require_qr and require_zone both false - nothing
    to disambiguate by) or if match_group_location actually finds one of
    its locations. Exactly one match -> unambiguous, use it. Zero or
    multiple matches -> genuinely ambiguous, reject and ask the client to
    resend with an explicit group_id (e.g. after the user picks from a
    list of their own assignment names)."""
    candidates = []
    for group in groups:
        policy = await group_resolution.to_policy(db, group)
        if not policy.require_qr and not policy.require_zone:
            candidates.append((policy, None, None))
            continue
        matched_location_id, distance = match_group_location(
            policy.locations, qr_code, latitude, longitude,
            require_qr=policy.require_qr, require_zone=policy.require_zone,
        )
        if matched_location_id is not None:
            candidates.append((policy, matched_location_id, distance))

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) == 0:
        raise AmbiguousGroupError(
            "أنت عضو في أكثر من مجموعة حضور، ولا يتطابق رمز QR أو موقعك الحالي مع أي منها. "
            "يرجى التأكد من الموقع/الرمز الصحيح أو تحديد المجموعة يدوياً."
        )
    raise AmbiguousGroupError(
        "أنت عضو في أكثر من مجموعة حضور، ويتطابق رمز QR/موقعك الحالي مع أكثر من مجموعة واحدة. "
        "يرجى تحديد المجموعة المطلوبة يدوياً."
    )


async def _resolve_policy_for_check_in(db, *, employee_id, company_id, qr_code, latitude, longitude, selected_group_id=None):
    """Returns (policy_or_None, matched_location_id, distance). None
    policy means "no active group memberships - use the legacy path,"
    same contract as before this employee ever had any groups."""
    groups = await group_resolution.list_active_groups_for_employee(db, employee_id=employee_id, company_id=company_id)
    if not groups:
        return None, None, None

    if selected_group_id:
        chosen = next((g for g in groups if str(g.id) == selected_group_id), None)
        if chosen is None:
            raise HTTPException(status_code=400, detail="group_id is not one of your current attendance group memberships")
        policy = await group_resolution.to_policy(db, chosen)
        matched_location_id, distance = match_group_location(
            policy.locations, qr_code, latitude, longitude,
            require_qr=policy.require_qr, require_zone=policy.require_zone,
        )
        return policy, matched_location_id, distance

    if len(groups) == 1:
        policy = await group_resolution.to_policy(db, groups[0])
        matched_location_id, distance = match_group_location(
            policy.locations, qr_code, latitude, longitude,
            require_qr=policy.require_qr, require_zone=policy.require_zone,
        )
        return policy, matched_location_id, distance

    return await _disambiguate_active_groups(db, groups, qr_code, latitude, longitude)


async def _resolve_policy_for_check_out(db, *, known_group_id, company_id):
    """Check-out never disambiguates - the session being closed already
    recorded which group applied at check-in time (Attendance.group_id).
    QR/zone validation itself stays LIVE here (re-evaluated against that
    group's CURRENT policy, same as this codebase's pre-existing
    behavior for the legacy path) - only WHICH group applies is fixed,
    not whether its rules have since changed. Falls back to the legacy
    path if the group can no longer be found (e.g. hard-deleted, an
    edge case normal deactivation doesn't cause)."""
    if not known_group_id:
        return None
    group = await employee_groups_repo.get_by_id_and_company(db, known_group_id, company_id)
    if group is None:
        return None
    return await group_resolution.to_policy(db, group)


async def validate_qr_attendance(
    db: AsyncSession, current_user: dict, qr_code, latitude, longitude, accuracy,
    *, selected_group_id=None, known_group_id=None,
):
    """Shared server-side gate for check-in AND check-out. Returns
    (company, settings, distance_from_company_meters, group_policy,
    location_id); raises a clear 400 (or 409 AmbiguousGroupError) on any
    failure.

    Resolves the employee's active Attendance Group(s) first (services/
    group_resolution.py) - an employee may belong to several at once, so
    which one applies to THIS action is resolved by, in order: an
    explicit caller-provided group (selected_group_id for check-in,
    known_group_id for check-out - see the two resolver functions above
    for how each decides), the single group if there's only one, or
    deterministic QR/location disambiguation:
    - No active group memberships -> the EXACT legacy single-location
      behavior, unchanged byte-for-byte: QR always required, GPS always
      required, the company's single flattened location/QR.
      `group_policy` is None in the return.
    - Resolved to one group -> require_zone/require_qr are enforced
      INDEPENDENTLY per that group's own toggles (Rule 9's four
      combinations), matched against its linked WorkLocations via
      match_group_location. Photo-proof is validated by the caller
      (check_in/check_out), not here - it's orthogonal to QR/GPS and
      never gates location resolution (Rule 9: "Photo proof must NOT
      automatically enable GPS or QR")."""
    if current_user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Access denied")

    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    settings = attendance_settings_for(company)

    if known_group_id:
        policy = await _resolve_policy_for_check_out(db, known_group_id=known_group_id, company_id=company.id)
        matched_location_id, distance = None, None
        if policy is not None:
            matched_location_id, distance = match_group_location(
                policy.locations, qr_code, latitude, longitude,
                require_qr=policy.require_qr, require_zone=policy.require_zone,
            )
    else:
        policy, matched_location_id, distance = await _resolve_policy_for_check_in(
            db, employee_id=parse_uuid(current_user["id"]), company_id=company.id,
            qr_code=qr_code, latitude=latitude, longitude=longitude, selected_group_id=selected_group_id,
        )

    if policy is None:
        # ---- Legacy single-location path, unchanged from before this
        # feature existed - a company/employee that never adopts the new
        # group hierarchy keeps behaving exactly as today. ----
        if not settings["qr_enabled"]:
            raise _tag(HTTPException(status_code=400, detail="تسجيل الحضور عبر QR معطل حالياً من قبل إدارة الشركة."), qr_valid=False)

        token = await ensure_qr_token(db, company)
        if qr_code != token:
            raise _tag(HTTPException(status_code=400, detail="رمز QR غير صالح. يرجى مسح رمز الشركة الصحيح."), qr_valid=False)

        if latitude is None or longitude is None:
            raise _tag(
                HTTPException(status_code=400, detail="خدمة الموقع (GPS) مطلوبة لتسجيل الحضور. يرجى السماح بالوصول إلى موقعك والمحاولة مجدداً."),
                qr_valid=True, gps_valid=False,
            )

        rejection = await detect_fake_gps(db, parse_uuid(current_user["id"]), latitude, longitude, accuracy)
        if rejection:
            raise _tag(HTTPException(status_code=400, detail=rejection), qr_valid=True, gps_valid=False)

        distance = None
        if settings["latitude"] is not None and settings["longitude"] is not None:
            distance = round(haversine_meters(latitude, longitude, settings["latitude"], settings["longitude"]), 1)
            if distance > settings["radius_meters"]:
                raise _tag(
                    HTTPException(
                        status_code=400,
                        detail=f"أنت خارج النطاق المسموح للشركة (المسافة الحالية {int(distance)} متر والمسموح {int(settings['radius_meters'])} متر)."
                    ),
                    qr_valid=True, gps_valid=False,
                )

        return company, settings, distance, None, None

    # ---- Group-based path - `policy` (and matched_location_id/distance)
    # already resolved above, from exactly one group (see
    # _resolve_policy_for_check_in/_out) - what follows explains WHY a
    # resolved-but-unsatisfied match failed, it does not re-resolve
    # which group applies. ----
    if policy.require_qr and not policy.locations:
        raise _tag(
            HTTPException(status_code=400, detail="لا توجد مواقع مهيأة لمجموعتك. يرجى التواصل مع إدارة الشركة."),
            qr_valid=False,
        )
    if policy.require_zone and (latitude is None or longitude is None):
        raise _tag(
            HTTPException(status_code=400, detail="خدمة الموقع (GPS) مطلوبة لتسجيل الحضور. يرجى السماح بالوصول إلى موقعك والمحاولة مجدداً."),
            gps_valid=False,
        )
    if latitude is not None and longitude is not None:
        rejection = await detect_fake_gps(db, parse_uuid(current_user["id"]), latitude, longitude, accuracy)
        if rejection:
            raise _tag(HTTPException(status_code=400, detail=rejection), gps_valid=False)

    if (policy.require_qr or policy.require_zone) and matched_location_id is None:
        if policy.require_qr and policy.require_zone:
            detail = "رمز QR غير صحيح، أو أنك خارج النطاق المسموح لجميع المواقع المخصصة لك."
        elif policy.require_qr:
            detail = "رمز QR غير صحيح لأي من المواقع المخصصة لك."
        else:
            detail = "أنت خارج النطاق المسموح لجميع المواقع المخصصة لك."
        raise _tag(
            HTTPException(status_code=400, detail=detail),
            qr_valid=(False if policy.require_qr else None), gps_valid=(False if policy.require_zone else None),
        )

    return company, settings, distance, policy, matched_location_id


def _duration_minutes(start, end) -> Optional[float]:
    if not start or not end:
        return None
    delta = (end - start).total_seconds() / 60
    return delta if delta >= 0 else None


def _location_dict(lat, lon, accuracy) -> Optional[dict]:
    if lat is None:
        return None
    d = {"latitude": lat, "longitude": lon}
    if accuracy is not None:
        d["accuracy"] = accuracy
    return d


def attendance_response(record, *, employee_name=None) -> dict:
    return {
        "id": str(record.id),
        "employee_id": str(record.employee_id),
        "employee_name": employee_name,
        "employee_department": record.employee_department,
        "employee_position": record.employee_position,
        "company_id": str(record.company_id),
        "date": _iso(record.date),
        "check_in_time": _iso(record.check_in_time),
        "check_out_time": _iso(record.check_out_time),
        "check_in_location": _location_dict(record.check_in_latitude, record.check_in_longitude, record.check_in_accuracy),
        "check_out_location": _location_dict(record.check_out_latitude, record.check_out_longitude, record.check_out_accuracy),
        "distance_from_company_meters": record.distance_from_company_meters,
        "check_out_distance_meters": record.check_out_distance_meters,
        "working_duration_minutes": record.working_duration_minutes if record.working_duration_minutes is not None
        else _duration_minutes(record.check_in_time, record.check_out_time),
        # Work Schedule calculation fields (Phase 7) - all None on records
        # from before this feature shipped, or for a company that hasn't
        # set up Work Schedules yet. required/break/net/overtime/missing
        # are the schedule-derived figures snapshotted or computed by
        # services/attendance_calc_engine.py; late/early_arrival are set at
        # check-in, overtime/missing/early_leave/net only once checked out.
        "required_minutes": record.required_minutes,
        "scheduled_break_minutes": record.scheduled_break_minutes,
        # Exposed alongside the other schedule-snapshot fields above so
        # services/leave_attendance.py's annotate_leave_dates can recompute
        # the shift's scheduled window (never the actual check-in/check-out
        # clock times) without a second, separate lookup.
        "scheduled_start_time": record.scheduled_start_time,
        "scheduled_end_time": record.scheduled_end_time,
        "net_minutes": record.net_minutes,
        "overtime_minutes": record.overtime_minutes,
        "missing_minutes": record.missing_minutes,
        "late_minutes": record.late_minutes,
        "early_arrival_minutes": record.early_arrival_minutes,
        "early_leave_minutes": record.early_leave_minutes,
        "device_info": record.device_info,
        "created_at": _iso(record.created_at),
        "status": record.status,
        # Group/location traceability (Part 4-9) - null on every record
        # from before this feature, or when the legacy single-location
        # company-wide path resolved the check-in (no group assigned).
        "group_id": str(record.group_id) if record.group_id else None,
        "location_id": str(record.location_id) if record.location_id else None,
    }


def _forgotten_checkout_deadline(record) -> datetime:
    """The deadline THIS record's own snapshotted schedule/grace values
    resolve to - never a fresh group/schedule lookup (see the model
    comment on Attendance.grace_period_minutes). Falls back to a 24h
    safety cap plus grace when no schedule was resolvable at check-in at
    all (a company that hasn't configured any WorkSchedule yet) - a
    session still must not stay open forever even then. See
    calc_engine.compute_forgotten_checkout_deadline for the check-in-time
    floor guarantee."""
    grace = record.grace_period_minutes if record.grace_period_minutes is not None else group_resolution.DEFAULT_GRACE_PERIOD_MINUTES
    if record.scheduled_start_time and record.scheduled_end_time:
        return calc_engine.compute_forgotten_checkout_deadline(
            record.date, record.scheduled_start_time, record.scheduled_end_time, grace,
            check_in_time=record.check_in_time,
        )
    return record.check_in_time + timedelta(hours=24, minutes=grace)


def _is_past_forgotten_checkout_deadline(record) -> bool:
    return datetime.now(timezone.utc) >= _forgotten_checkout_deadline(record)


def self_heal_open_record(record) -> bool:
    """If this open record's forgotten-checkout deadline has passed,
    resolves it as 'did_not_check_out' in place (caller is responsible for
    flushing). Returns True if it was (or already had been) closed this
    way. Shared by check_in (before allowing a new session), check_out
    (when the found session turns out to already be past deadline), and
    read paths (history/list) so the Owner-facing view reflects the
    correct status without requiring a write to trigger it. Never fires
    merely because the calendar date changed (Part 7/Rule 12) - only
    wall-clock time past shift-end-plus-grace."""
    if record.check_out_time is not None or record.status == "did_not_check_out":
        return False
    if _is_past_forgotten_checkout_deadline(record):
        record.status = "did_not_check_out"
        return True
    return False


async def _log_attendance_event(
    db: AsyncSession, *, employee_id, company_id, action_type: str,
    attendance_record_id, qr_valid: bool, gps_valid: bool,
    failure_reason: Optional[str], latitude, longitude, device_platform: Optional[str],
) -> None:
    await attendance_events_repo.create(
        db,
        employee_id=employee_id,
        company_id=company_id,
        action_type=action_type,
        attendance_record_id=attendance_record_id,
        event_date=datetime.now(timezone.utc).date(),
        latitude=latitude,
        longitude=longitude,
        qr_valid=qr_valid,
        gps_valid=gps_valid,
        failure_reason=failure_reason,
        device_platform=device_platform,
    )
    await db.flush()


async def check_in(db: AsyncSession, current_user: dict, data) -> dict:
    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])
    platform = _normalize_platform(data.device_info)
    # Set once the day's row (if any) is fetched below, so a later
    # "already checked in" failure can still link its event to the real
    # record instead of leaving attendance_record_id null.
    known_record_id = None

    try:
        company, settings, distance, policy, location_id = await validate_qr_attendance(
            db, current_user, data.qr_code, data.latitude, data.longitude, data.accuracy,
            selected_group_id=getattr(data, "group_id", None),
        )

        now = datetime.now(timezone.utc)
        today = now.date()

        # Self-heal any stale OPEN session before allowing a new check-in -
        # THE fix for Part 7/Rule 13: this looks for "my current open
        # session" (any date), never "today's row," so a night shift that
        # started yesterday is found here exactly like one that started an
        # hour ago. If it's still within its own grace period, a second,
        # overlapping session must not begin - the employee has to check
        # out of the first one. If its grace period has already elapsed,
        # it's resolved as 'did_not_check_out' in place and this new
        # check-in is then free to proceed, per Rule 13's explicit "allow
        # the next legitimate shift to begin independently."
        open_record = await attendance_repo.get_latest_open_for_employee(db, employee_id)
        if open_record:
            known_record_id = open_record.id
            if self_heal_open_record(open_record):
                await db.flush()
            else:
                raise HTTPException(
                    status_code=400,
                    detail="لديك جلسة حضور سابقة لم يتم تسجيل الخروج منها بعد. يرجى تسجيل الخروج أولاً قبل بدء جلسة جديدة.",
                )

        existing = await attendance_repo.get_for_employee_on_date(db, employee_id, today)
        if existing and existing.status == "did_not_check_out":
            # Already resolved/closed (Part 8: "preserve it") - never
            # reused as a same-day placeholder and never mistaken for
            # "already checked in today." A new check-in on the same
            # calendar date creates its own independent row below,
            # leaving this one exactly as self-healed.
            existing = None
        if existing:
            known_record_id = existing.id
        if existing and existing.check_in_time:
            raise HTTPException(status_code=400, detail="You are already checked in.")

        # Resolved and snapshotted here, once, at check-in time - see the
        # denormalization comment on the Attendance model. Every later read
        # (history, reports, analytics) uses these stored columns, never a
        # fresh lookup, so editing or deleting a schedule afterwards can never
        # rewrite this record's calculated history. When a group is
        # assigned, its already-resolved schedule snapshot (from
        # group_resolution.to_policy, which follows the group's own
        # work_schedule_id -> company-default fall-through) is reused
        # directly rather than re-resolving.
        if policy:
            snapshot = policy.schedule
        else:
            schedule = await schedule_resolution.get_effective_schedule_for_employee(
                db, schedule_id=parse_uuid(current_user.get("schedule_id")), company_id=company_id,
            )
            snapshot = schedule_resolution.to_snapshot(schedule)
        calc = calc_engine.calculate_check_in(snapshot, now)
        attendance_status = calc["status"]
        # Group policy's grace period when assigned; the global default
        # (services/group_resolution.py's DEFAULT_GRACE_PERIOD_MINUTES,
        # currently 180) for every ungrouped/legacy employee - the
        # forgotten-checkout FIX itself applies universally (it's a
        # correctness fix, not an opt-in feature), only the multi-
        # location/QR-independence pieces are opt-in via group setup.
        grace_period_minutes = policy.grace_period_minutes if policy else group_resolution.DEFAULT_GRACE_PERIOD_MINUTES

        fields = dict(
            employee_department=current_user.get("department"),
            employee_position=current_user.get("position"),
            company_id=company_id,
            date=today,
            check_in_time=now,
            check_in_latitude=data.latitude,
            check_in_longitude=data.longitude,
            check_in_accuracy=data.accuracy,
            distance_from_company_meters=distance,
            device_info=data.device_info,
            status=attendance_status,
            schedule_id=parse_uuid(snapshot.schedule_id) if snapshot else None,
            scheduled_start_time=snapshot.start_time if snapshot else None,
            scheduled_end_time=snapshot.end_time if snapshot else None,
            scheduled_break_minutes=snapshot.break_minutes if snapshot else None,
            required_minutes=snapshot.required_minutes if snapshot else None,
            late_minutes=calc["late_minutes"],
            early_arrival_minutes=calc["early_arrival_minutes"],
            group_id=parse_uuid(policy.group_id) if policy else None,
            location_id=parse_uuid(location_id) if location_id else None,
            grace_period_minutes=grace_period_minutes,
        )

        if existing:
            for field, value in fields.items():
                setattr(existing, field, value)
            record = existing
        else:
            record = await attendance_repo.create(db, employee_id=employee_id, **fields)
        await db.flush()
    except HTTPException as exc:
        # get_db()'s request-scoped session rolls back on any exception
        # leaving the route handler, which would silently discard this
        # event along with everything else - commit it here, before
        # re-raising, so the audit trail survives the failure it's
        # recording. If a stale open session was self-healed to
        # 'did_not_check_out' above before some LATER, unrelated failure
        # occurred, that resolution is committed too - it's an
        # independently-correct fix that must not be rolled back just
        # because this particular check-in attempt failed for some other
        # reason.
        await _log_attendance_event(
            db, employee_id=employee_id, company_id=company_id, action_type="check_in",
            attendance_record_id=known_record_id,
            qr_valid=getattr(exc, "qr_valid", True), gps_valid=getattr(exc, "gps_valid", True),
            failure_reason=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            latitude=data.latitude, longitude=data.longitude, device_platform=platform,
        )
        await db.commit()
        raise

    await _log_attendance_event(
        db, employee_id=employee_id, company_id=company_id, action_type="check_in",
        attendance_record_id=record.id, qr_valid=True, gps_valid=True, failure_reason=None,
        latitude=data.latitude, longitude=data.longitude, device_platform=platform,
    )

    if attendance_status == "late":
        if company:
            await notifications_service.publish(
                db,
                user_id=company.owner_id,
                company_id=company.id,
                category=notifications_service.CATEGORY_ATTENDANCE,
                type="late_check_in",
                title="تسجيل حضور متأخر",
                message=f"سجّل الموظف {current_user['name']} حضوره متأخراً اليوم",
                entity_type="user",
                entity_id=employee_id,
                action_url="/company-owner/attendance",
                sender_id=employee_id,
                sender_name=current_user["name"],
            )

    return {"message": "Checked in successfully", "status": attendance_status}


async def check_out(db: AsyncSession, current_user: dict, data) -> dict:
    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])
    platform = _normalize_platform(data.device_info)
    known_record_id = None

    try:
        now = datetime.now(timezone.utc)

        # THE fix for midnight-crossing shifts (Part 7): resolve "my
        # current open session" (any date), never "today's row." A shift
        # that started yesterday at 5PM and is being checked out of at
        # 1:30AM today is found here exactly the same as a same-day shift.
        # Resolved BEFORE group/QR/zone validation below - closing a
        # specific session needs to validate against THAT session's own
        # already-snapshotted group (Attendance.group_id), never a fresh
        # multi-group disambiguation (the employee isn't starting a new
        # session, there's nothing to disambiguate).
        record = await attendance_repo.get_latest_open_for_employee(db, employee_id)
        if record:
            known_record_id = record.id
        if not record:
            # No open session anywhere - distinguish "already checked out
            # today" (a clearer, more actionable message) from "never
            # checked in at all today" by falling back to today's row,
            # which get_latest_open_for_employee deliberately excludes
            # once it's closed.
            todays_record = await attendance_repo.get_for_employee_on_date(db, employee_id, now.date())
            if todays_record:
                known_record_id = todays_record.id
            if todays_record and todays_record.check_out_time:
                raise HTTPException(status_code=400, detail="Already checked out")
            raise HTTPException(status_code=400, detail="No check-in record found.")

        # If this session's forgotten-checkout deadline already passed
        # (shift end + grace, per Rule 12 - never merely "the calendar
        # date changed"), it gets resolved as 'did_not_check_out' right
        # here rather than accepting a very-late checkout for it. A
        # legitimate late arrival still needs an Owner's manual
        # correction (Part 8), not a silent late accept.
        if self_heal_open_record(record):
            await db.flush()
            raise HTTPException(
                status_code=400,
                detail="انتهت مهلة السماح لتسجيل الخروج من هذه الجلسة، وتم اعتبارها (لم يسجل خروج). "
                       "يرجى التواصل مع إدارة الشركة لتصحيح السجل.",
            )

        # Note: group/QR/zone validation still runs (an employee must
        # still be somewhere valid to check out, per that group's current
        # policy) - known_group_id pins it to the session's own group,
        # skipping disambiguation entirely.
        company, settings, distance, policy, location_id = await validate_qr_attendance(
            db, current_user, data.qr_code, data.latitude, data.longitude, data.accuracy,
            known_group_id=record.group_id,
        )

        record.check_out_time = now
        record.check_out_latitude = data.latitude
        record.check_out_longitude = data.longitude
        record.check_out_accuracy = data.accuracy
        record.check_out_distance_meters = distance
        record.working_duration_minutes = _duration_minutes(record.check_in_time, now)

        # Reconstructed from the columns snapshotted at check-in - never a
        # fresh schedule lookup here, so a schedule edited/deleted between
        # check-in and check-out still calculates against what was actually
        # in effect this morning.
        snapshot = (
            ScheduleSnapshot(
                schedule_id=str(record.schedule_id) if record.schedule_id else None,
                start_time=record.scheduled_start_time,
                end_time=record.scheduled_end_time,
                break_minutes=record.scheduled_break_minutes,
                required_minutes=record.required_minutes,
            )
            if record.schedule_id
            else None
        )
        calc = calc_engine.calculate_check_out(snapshot, record.check_in_time, now)
        record.overtime_minutes = calc["overtime_minutes"]
        record.missing_minutes = calc["missing_minutes"]
        record.early_leave_minutes = calc["early_leave_minutes"]
        record.net_minutes = calc["net_minutes"]
        await db.flush()
    except HTTPException as exc:
        # See the matching comment in check_in() - the outer session rolls
        # back on any exception, so this event must be committed here or it
        # never survives to be read back.
        await _log_attendance_event(
            db, employee_id=employee_id, company_id=company_id, action_type="check_out",
            attendance_record_id=known_record_id,
            qr_valid=getattr(exc, "qr_valid", True), gps_valid=getattr(exc, "gps_valid", True),
            failure_reason=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            latitude=data.latitude, longitude=data.longitude, device_platform=platform,
        )
        await db.commit()
        raise

    await _log_attendance_event(
        db, employee_id=employee_id, company_id=company_id, action_type="check_out",
        attendance_record_id=record.id, qr_valid=True, gps_valid=True, failure_reason=None,
        latitude=data.latitude, longitude=data.longitude, device_platform=platform,
    )

    return {"message": "Checked out successfully"}


async def _self_heal_and_flush(db: AsyncSession, records) -> None:
    """Applies self_heal_open_record to every record with an open session
    (check_out_time NULL) and flushes if anything actually changed - the
    read-path half of Part 7/Rule 13's "read/history/dashboard paths
    should also safely resolve stale sessions." No-op for the common case
    (no open records in the page being read), so this is cheap on every
    call that isn't actively touching a stale session."""
    healed = any([self_heal_open_record(r) for r in records if r.check_out_time is None])
    if healed:
        await db.flush()


async def get_attendance_history(db: AsyncSession, current_user: dict) -> List[dict]:
    records = await attendance_repo.list_history_for_employee(db, parse_uuid(current_user["id"]))
    await _self_heal_and_flush(db, records)
    results = [attendance_response(r, employee_name=current_user["name"]) for r in records]
    await leave_attendance_service.annotate_leave_dates(db, results)
    return results


async def list_attendance_for_owner(db: AsyncSession, company_id, *, date=None, date_from=None, date_to=None,
                                     employee_id=None, department=None, position=None, status=None,
                                     search=None, schedule_id=None, late=None, overtime=None,
                                     missing_hours=None, range_=None) -> List[dict]:
    company_id = parse_uuid(company_id)
    date_ = date_type.fromisoformat(date) if date else None
    from_ = date_type.fromisoformat(date_from) if date_from else None
    to_ = date_type.fromisoformat(date_to) if date_to else None

    # week/month/year quick-range - reuses the same range resolver as the
    # per-employee report (Phase 6) rather than duplicating the Sunday-start
    # week math. Only applied when the caller hasn't already pinned an exact
    # date or range explicitly.
    if range_ and date_ is None and from_ is None and to_ is None:
        from_, to_ = _resolve_stats_range(range_, None, None)

    if from_ is None and to_ is None and date_ is None:
        date_ = datetime.now(timezone.utc).date()

    checked_out = True if status == "checked_out" else None
    status_filter = status if status and status != "checked_out" else None

    records = await attendance_repo.list_by_company(
        db, company_id, date_=date_, date_from=from_, date_to=to_,
        employee_id=parse_uuid(employee_id) if employee_id else None,
        checked_out=checked_out, status=status_filter, department=department,
        schedule_id=parse_uuid(schedule_id) if schedule_id else None,
        late=late, overtime=overtime, missing_hours=missing_hours,
    )

    await _self_heal_and_flush(db, records)

    employees = await users_repo.list_employees_by_company(db, company_id)
    employee_map = {str(e.id): e for e in employees}

    results = []
    for record in records:
        employee = employee_map.get(str(record.employee_id))
        response = attendance_response(record, employee_name=employee.name if employee else None)
        if response["employee_department"] is None and employee:
            response["employee_department"] = employee.department
        if response["employee_position"] is None and employee:
            response["employee_position"] = employee.position
        # Unlike department (filtered in SQL above), position stays a
        # post-fetch check: the column is brand new and null on every
        # pre-existing row, so a SQL filter would silently exclude history
        # that only resolves via the live-employee fallback just above.
        if position and response["employee_position"] != position:
            continue
        if search and search.strip().lower() not in (response["employee_name"] or "").lower():
            continue
        results.append(response)

    await holidays_service.annotate_holiday_dates(db, company_id, results)
    await leave_attendance_service.annotate_leave_dates(db, results)
    return results


def _resolve_stats_range(range_: str, date_from: Optional[str], date_to: Optional[str]) -> tuple:
    """Shared by the owner per-employee report (Phase 6) - one place that
    turns "today/week/month/year/custom" into an inclusive [start, end]
    date pair, so every consumer of get_employee_attendance_stats agrees
    on exactly what "this month" etc. means."""
    today = datetime.now(timezone.utc).date()
    if range_ == "today":
        return today, today
    if range_ == "week":
        # Sunday-start week, matching this codebase's own 0=Sunday..6=Saturday
        # convention (Company.working_days / WorkSchedule.working_days) -
        # not Python's default Monday-start ISO week.
        days_since_sunday = (today.weekday() + 1) % 7
        return today - timedelta(days=days_since_sunday), today
    if range_ == "month":
        return today.replace(day=1), today
    if range_ == "year":
        return today.replace(month=1, day=1), today
    if range_ == "custom":
        if not date_from or not date_to:
            raise HTTPException(status_code=400, detail="date_from and date_to are required for a custom range")
        return date_type.fromisoformat(date_from), date_type.fromisoformat(date_to)
    raise HTTPException(status_code=400, detail="range must be one of: today, week, month, year, custom")


async def get_employee_attendance_stats(
    db: AsyncSession, company_id, employee_id: str, range_: str,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
) -> dict:
    """Phases 5 & 6: the single reusable rollup behind the owner's
    per-employee report AND (via the same aggregate() call the dashboards
    use for "today") daily/weekly/monthly/yearly accumulation - no bespoke
    calculation duplicated per time window, per Phase 9's requirement."""
    company_id = parse_uuid(company_id)
    parsed_employee_id = parse_uuid(employee_id)
    employee = await users_repo.get_employee_in_company(db, parsed_employee_id, company_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    start, end = _resolve_stats_range(range_, date_from, date_to)
    if start > end:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    records = await attendance_repo.list_by_company(
        db, company_id, employee_id=parsed_employee_id, date_from=start, date_to=end,
    )
    responses = [attendance_response(r, employee_name=employee.name) for r in records]
    expected_days = (end - start).days + 1
    stats = calc_engine.aggregate(responses, expected_days=expected_days)

    return {
        "employee_id": str(employee.id),
        "employee_name": employee.name,
        "range": range_,
        "date_from": _iso(start),
        "date_to": _iso(end),
        "days_present": stats.days_present,
        "days_late": stats.days_late,
        "days_absent": stats.days_absent,
        "total_worked_minutes": stats.total_worked_minutes,
        "total_required_minutes": stats.total_required_minutes,
        "total_overtime_minutes": stats.total_overtime_minutes,
        "total_missing_minutes": stats.total_missing_minutes,
        "total_late_minutes": stats.total_late_minutes,
        "total_early_leave_minutes": stats.total_early_leave_minutes,
        "late_count": stats.late_count,
        "early_leave_count": stats.early_leave_count,
        "average_daily_worked_minutes": stats.average_daily_worked_minutes,
        "attendance_percentage": stats.attendance_percentage,
    }


async def get_attendance_settings(db: AsyncSession, company) -> dict:
    await ensure_qr_token(db, company)
    settings = attendance_settings_for(company)
    return {
        "settings": settings,
        "location_configured": settings["latitude"] is not None and settings["longitude"] is not None,
        "qr_code": company.qr_code,
        "qr_token": company.qr_token,
        "company_name": company.name,
    }


async def update_attendance_settings(db: AsyncSession, current_user: dict, updates) -> dict:
    provided = updates.model_dump(exclude_none=True)
    if not provided:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if ("latitude" in provided) != ("longitude" in provided):
        raise HTTPException(status_code=400, detail="يجب تحديد خط العرض وخط الطول معاً.")

    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    field_map = {"latitude": "attendance_latitude", "longitude": "attendance_longitude",
                 "radius_meters": "attendance_radius_meters", "qr_enabled": "attendance_qr_enabled"}
    for key, value in provided.items():
        setattr(company, field_map[key], value)
    company.attendance_settings_updated_at = datetime.now(timezone.utc)
    company.attendance_settings_updated_by = parse_uuid(current_user["id"])
    company.attendance_settings_updated_by_name = current_user.get("name")
    await db.flush()

    settings = attendance_settings_for(company)
    return {
        "message": "Attendance settings updated",
        "settings": settings,
        "location_configured": settings["latitude"] is not None and settings["longitude"] is not None,
    }


async def get_attendance_analytics(db: AsyncSession, company_id, date_from: Optional[str], date_to: Optional[str]) -> dict:
    company_id = parse_uuid(company_id)
    today = datetime.now(timezone.utc).date()
    end_date = min(date_type.fromisoformat(date_to), today) if date_to else today
    start_date = date_type.fromisoformat(date_from) if date_from else (today - timedelta(days=29))

    day_count = (end_date - start_date).days + 1
    if day_count < 1:
        raise HTTPException(status_code=400, detail="Invalid date range")
    day_count = min(day_count, 366)

    records = await attendance_repo.list_by_company_date_range(db, company_id, start_date, end_date)
    employees = await users_repo.list_employees_by_company(db, company_id)
    total_employees = len(employees)

    def time_of_day_minutes(dt) -> Optional[float]:
        if not dt:
            return None
        return dt.hour * 60 + dt.minute + dt.second / 60

    def format_clock(minutes_list: list) -> Optional[str]:
        if not minutes_list:
            return None
        avg = sum(minutes_list) / len(minutes_list)
        return f"{int(avg // 60):02d}:{int(avg % 60):02d}"

    check_in_minutes, check_out_minutes, durations = [], [], []
    for record in records:
        m_in = time_of_day_minutes(record.check_in_time)
        if m_in is not None:
            check_in_minutes.append(m_in)
        m_out = time_of_day_minutes(record.check_out_time)
        if m_out is not None:
            check_out_minutes.append(m_out)
        duration = record.working_duration_minutes
        if duration is None:
            duration = _duration_minutes(record.check_in_time, record.check_out_time)
        if duration is not None:
            durations.append(duration)

    attended_records = [r for r in records if r.check_in_time]
    late_count = sum(1 for r in records if r.status == "late")
    # Unified expected-attendance exclusion (approved decision): this
    # denominator previously counted every calendar day for every employee
    # with no holiday/weekly-off or Leave adjustment at all - a pre-existing
    # gap for holidays, now fixed here at the same time Leave is wired in,
    # rather than leaving Leave correct and Holiday still blind in this one
    # function. compute_non_working_employee_days accounts for both without
    # double-counting a given employee-day.
    non_working_employee_days = await leave_attendance_service.compute_non_working_employee_days(
        db, company_id, [e.id for e in employees], start_date, end_date,
    )
    expected_attendance = max(0, total_employees * day_count - non_working_employee_days)
    absence_count = max(0, expected_attendance - len(attended_records))
    attendance_percentage = round(len(attended_records) / expected_attendance * 100, 1) if expected_attendance > 0 else 0
    late_percentage = round(late_count / expected_attendance * 100, 1) if expected_attendance > 0 else 0

    distances = [r.distance_from_company_meters for r in records if r.distance_from_company_meters is not None]
    average_distance_meters = round(sum(distances) / len(distances), 1) if distances else None

    by_date = {}
    for record in records:
        by_date.setdefault(record.date, []).append(record)

    attendance_trend, late_trend, working_hours_trend = [], [], []
    overtime_trend, missing_hours_trend = [], []
    month_buckets = {}
    for offset in range(day_count):
        day = start_date + timedelta(days=offset)
        day_records = by_date.get(day, [])
        day_attended = sum(1 for r in day_records if r.check_in_time)
        day_late = sum(1 for r in day_records if r.status == "late")
        day_durations = []
        for r in day_records:
            d = r.working_duration_minutes
            if d is None:
                d = _duration_minutes(r.check_in_time, r.check_out_time)
            if d is not None:
                day_durations.append(d)
        rate = round(day_attended / total_employees * 100, 1) if total_employees > 0 else 0
        attendance_trend.append({"date": day.isoformat(), "rate": rate})
        late_trend.append({"date": day.isoformat(), "count": day_late})
        working_hours_trend.append({
            "date": day.isoformat(),
            "hours": round(sum(day_durations) / len(day_durations) / 60, 2) if day_durations else 0,
        })
        # Overtime/missing-hours trend (Phase 8): only populated for records
        # with a resolved schedule (calc engine leaves these None otherwise),
        # which contribute 0 - same "no schedule = no penalty" fallback as
        # the calculation engine itself.
        overtime_trend.append({
            "date": day.isoformat(),
            "hours": round(sum(r.overtime_minutes or 0 for r in day_records) / 60, 2),
        })
        missing_hours_trend.append({
            "date": day.isoformat(),
            "hours": round(sum(r.missing_minutes or 0 for r in day_records) / 60, 2),
        })

        month_key = day.strftime("%Y-%m")
        bucket = month_buckets.setdefault(month_key, {"days": 0, "attended": 0})
        bucket["days"] += 1
        bucket["attended"] += day_attended

    monthly_attendance_trend = [
        {
            "month": month,
            "rate": round(bucket["attended"] / (bucket["days"] * total_employees) * 100, 1)
            if total_employees > 0 and bucket["days"] > 0 else 0,
        }
        for month, bucket in sorted(month_buckets.items())
    ]

    per_employee = {
        str(e.id): {
            "employee_id": str(e.id), "name": e.name, "department": e.department,
            "days_attended": 0, "late_count": 0, "total_minutes": 0.0, "duration_days": 0,
            "total_overtime_minutes": 0.0, "total_late_minutes": 0.0,
        }
        for e in employees
    }
    for record in records:
        stats = per_employee.get(str(record.employee_id))
        if not stats:
            continue
        if record.check_in_time:
            stats["days_attended"] += 1
        if record.status == "late":
            stats["late_count"] += 1
        duration = record.working_duration_minutes
        if duration is None:
            duration = _duration_minutes(record.check_in_time, record.check_out_time)
        if duration is not None:
            stats["total_minutes"] += duration
            stats["duration_days"] += 1
        if record.overtime_minutes:
            stats["total_overtime_minutes"] += record.overtime_minutes
        if record.late_minutes:
            stats["total_late_minutes"] += record.late_minutes

    department_totals = {}
    for stats in per_employee.values():
        department = stats["department"]
        if not department:
            continue
        bucket = department_totals.setdefault(department, {"attended": 0, "employee_count": 0})
        bucket["attended"] += stats["days_attended"]
        bucket["employee_count"] += 1
    department_attendance = [
        {
            "department": department,
            "attendance_rate": round(bucket["attended"] / (bucket["employee_count"] * day_count) * 100, 1)
            if bucket["employee_count"] > 0 else 0,
        }
        for department, bucket in sorted(department_totals.items())
    ]

    ranking = []
    for stats in per_employee.values():
        attendance_rate = stats["days_attended"] / day_count * 100
        on_time_rate = ((stats["days_attended"] - stats["late_count"]) / stats["days_attended"] * 100) if stats["days_attended"] > 0 else 0
        score = round(attendance_rate * 0.7 + on_time_rate * 0.3, 1)
        ranking.append({
            "employee_id": stats["employee_id"], "name": stats["name"], "department": stats["department"],
            "days_attended": stats["days_attended"], "late_count": stats["late_count"],
            "avg_working_hours": round(stats["total_minutes"] / stats["duration_days"] / 60, 1) if stats["duration_days"] > 0 else 0,
            "attendance_rate": round(attendance_rate, 1), "score": score,
        })
    ranking.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(ranking):
        item["rank"] = index + 1

    # Most punctual: fewest late days, tie-broken by lowest average late
    # minutes. Only employees who actually attended are eligible - an
    # employee with zero attendance isn't "punctual", they're absent.
    most_punctual_employees = [
        {
            "employee_id": s["employee_id"],
            "name": s["name"],
            "late_count": s["late_count"],
            "avg_late_minutes": round(s["total_late_minutes"] / s["days_attended"], 1) if s["days_attended"] else 0,
        }
        for s in sorted(
            (s for s in per_employee.values() if s["days_attended"] > 0),
            key=lambda s: (s["late_count"], s["total_late_minutes"]),
        )[:5]
    ]

    most_overtime_employees = [
        {
            "employee_id": s["employee_id"],
            "name": s["name"],
            "total_overtime_hours": round(s["total_overtime_minutes"] / 60, 1),
        }
        for s in sorted(per_employee.values(), key=lambda s: s["total_overtime_minutes"], reverse=True)
        if s["total_overtime_minutes"] > 0
    ][:5]

    return {
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "total_employees": total_employees,
        "average_check_in_time": format_clock(check_in_minutes),
        "average_check_out_time": format_clock(check_out_minutes),
        "average_working_hours": round(sum(durations) / len(durations) / 60, 1) if durations else 0,
        "attendance_percentage": attendance_percentage,
        "late_count": late_count,
        "late_percentage": late_percentage,
        "absence_count": absence_count,
        "average_distance_meters": average_distance_meters,
        "department_attendance": department_attendance,
        "employee_ranking": ranking,
        "most_punctual_employees": most_punctual_employees,
        "most_overtime_employees": most_overtime_employees,
        "charts": {
            "attendance_trend": attendance_trend,
            "late_trend": late_trend,
            "working_hours_trend": working_hours_trend,
            "monthly_attendance_trend": monthly_attendance_trend,
            "overtime_trend": overtime_trend,
            "missing_hours_trend": missing_hours_trend,
        },
    }


async def edit_attendance(db: AsyncSession, current_user: dict, attendance_id: str, updates) -> dict:
    parsed_id = parse_uuid(attendance_id)
    record = await attendance_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    reason = (updates.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A reason is required for every manual attendance correction")

    provided = updates.model_dump(exclude_none=True)
    current_values = {"check_in_time": _iso(record.check_in_time), "check_out_time": _iso(record.check_out_time), "status": record.status}
    changes = {k: v for k, v in provided.items() if k in ATTENDANCE_EDITABLE_FIELDS and current_values.get(k) != v}
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if "status" in changes and changes["status"] not in ("present", "late", "absent", "did_not_check_out"):
        raise HTTPException(status_code=400, detail="Invalid status value")

    old_values = {k: current_values.get(k) for k in changes}

    if "check_in_time" in changes:
        record.check_in_time = datetime.fromisoformat(changes["check_in_time"])
    if "check_out_time" in changes:
        record.check_out_time = datetime.fromisoformat(changes["check_out_time"])
    if "status" in changes:
        record.status = changes["status"]
    record.working_duration_minutes = _duration_minutes(record.check_in_time, record.check_out_time)
    await db.flush()

    # Unified audit_logs table (per the approved schema): one row per edit
    # action, old_values/new_values as a JSON diff - the read endpoint
    # reconstructs the old per-field response shape from this.
    await audit_logs_repo.create(
        db,
        company_id=parse_uuid(current_user["company_id"]),
        user_id=parse_uuid(current_user["id"]),
        entity_type="attendance",
        entity_id=record.id,
        action="manual_edit",
        old_values=old_values,
        new_values=changes,
        reason=reason,
    )

    return {"message": "Attendance record updated", "audit_entries": len(changes)}


async def get_attendance_audit_log(db: AsyncSession, company_id) -> List[dict]:
    """Reconstructs the old attendance_audit_log's one-row-per-changed-
    field response shape from the unified audit_logs table's one-row-per-
    action storage - see the migration plan's refinement #3 reconciliation."""
    log_rows = await audit_logs_repo.list_for_company(db, company_id, "attendance", limit=200)

    attendance_ids = {row.entity_id for row in log_rows}
    records = await attendance_repo.get_by_ids(db, attendance_ids)
    record_map = {r.id: r for r in records}
    employee_ids = {r.employee_id for r in records}
    names = await users_repo.get_names_by_ids(db, employee_ids)

    entries = []
    for row in log_rows:
        record = record_map.get(row.entity_id)
        for field, new_value in (row.new_values or {}).items():
            entries.append({
                "id": f"{row.id}-{field}",
                "attendance_id": str(row.entity_id),
                "company_id": str(row.company_id),
                "edited_by": str(row.user_id) if row.user_id else None,
                "edited_by_name": None,  # filled below via a batched name lookup
                "edited_at": _iso(row.created_at),
                "field": field,
                "old_value": (row.old_values or {}).get(field),
                "new_value": new_value,
                "reason": row.reason,
                "employee_name": names.get(str(record.employee_id)) if record else None,
                "attendance_date": _iso(record.date) if record else None,
            })

    editor_ids = {row.user_id for row in log_rows if row.user_id}
    editor_names = await users_repo.get_names_by_ids(db, editor_ids)
    for entry in entries:
        entry["edited_by_name"] = editor_names.get(entry["edited_by"])

    return entries


def _attendance_event_response(event) -> dict:
    return {
        "id": str(event.id),
        "attendance_record_id": str(event.attendance_record_id) if event.attendance_record_id else None,
        "employee_id": str(event.employee_id),
        "company_id": str(event.company_id),
        "action_type": event.action_type,
        "event_date": _iso(event.event_date),
        "event_time": _iso(event.event_time),
        "latitude": event.latitude,
        "longitude": event.longitude,
        "qr_valid": event.qr_valid,
        "gps_valid": event.gps_valid,
        "failure_reason": event.failure_reason,
        "device_platform": event.device_platform,
        "created_at": _iso(event.created_at),
    }


async def get_attendance_events(db: AsyncSession, company_id, attendance_id: str) -> List[dict]:
    parsed_id = parse_uuid(attendance_id)
    record = await attendance_repo.get_in_company(db, parsed_id, parse_uuid(company_id)) if parsed_id else None
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    events = await attendance_events_repo.list_for_record(db, record.id)
    return [_attendance_event_response(e) for e in events]


async def get_company_qr(db: AsyncSession, current_user: dict, company_id: str) -> dict:
    is_own_company_owner = current_user["role"] == "company_owner" and current_user.get("company_id") == company_id
    if current_user["role"] != "super_admin" and not is_own_company_owner:
        raise HTTPException(status_code=403, detail="Access denied")

    parsed_id = parse_uuid(company_id)
    company = await companies_repo.get_by_id(db, parsed_id) if parsed_id else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    await ensure_qr_token(db, company)
    return {"qr_code": company.qr_code}
