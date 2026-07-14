import math
import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.attendance as attendance_repo
import repositories.audit_logs as audit_logs_repo
import repositories.companies as companies_repo
import repositories.users as users_repo
import services.holidays as holidays_service
from services.admin import parse_uuid
from services.qr import generate_qr_code

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
    }


async def ensure_qr_token(db: AsyncSession, company) -> str:
    """Self-heal on read: a company whose printed QR predates this feature
    gets an opaque random token and a regenerated QR image the first time
    its QR is needed. The QR encodes only this token, never company data."""
    if not company.qr_token:
        token = uuid.uuid4().hex
        company.qr_token = token
        company.qr_code = generate_qr_code(token)
        await db.flush()
    return company.qr_token


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


async def validate_qr_attendance(db: AsyncSession, current_user: dict, qr_code, latitude, longitude, accuracy):
    """Shared server-side gate for check-in AND check-out. Returns
    (company, settings, distance_from_company_meters); raises a clear 400
    on any failure. A valid QR alone is never sufficient - GPS is always
    required, and the radius is enforced as soon as a company location
    exists (unless the owner disabled QR attendance entirely)."""
    if current_user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Access denied")

    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    settings = attendance_settings_for(company)
    if not settings["qr_enabled"]:
        raise HTTPException(status_code=400, detail="تسجيل الحضور عبر QR معطل حالياً من قبل إدارة الشركة.")

    token = await ensure_qr_token(db, company)
    if qr_code != token:
        raise HTTPException(status_code=400, detail="رمز QR غير صالح. يرجى مسح رمز الشركة الصحيح.")

    if latitude is None or longitude is None:
        raise HTTPException(status_code=400, detail="خدمة الموقع (GPS) مطلوبة لتسجيل الحضور. يرجى السماح بالوصول إلى موقعك والمحاولة مجدداً.")

    rejection = await detect_fake_gps(db, parse_uuid(current_user["id"]), latitude, longitude, accuracy)
    if rejection:
        raise HTTPException(status_code=400, detail=rejection)

    distance = None
    if settings["latitude"] is not None and settings["longitude"] is not None:
        distance = round(haversine_meters(latitude, longitude, settings["latitude"], settings["longitude"]), 1)
        if distance > settings["radius_meters"]:
            raise HTTPException(
                status_code=400,
                detail=f"أنت خارج النطاق المسموح للشركة (المسافة الحالية {int(distance)} متر والمسموح {int(settings['radius_meters'])} متر)."
            )

    return company, settings, distance


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
        "device_info": record.device_info,
        "created_at": _iso(record.created_at),
        "status": record.status,
    }


async def check_in(db: AsyncSession, current_user: dict, data) -> dict:
    company, settings, distance = await validate_qr_attendance(
        db, current_user, data.qr_code, data.latitude, data.longitude, data.accuracy
    )

    now = datetime.now(timezone.utc)
    today = now.date()
    employee_id = parse_uuid(current_user["id"])

    existing = await attendance_repo.get_for_employee_on_date(db, employee_id, today)
    if existing and existing.check_in_time:
        raise HTTPException(status_code=400, detail="Already checked in today")

    attendance_status = "late" if now.hour >= 9 else "present"

    fields = dict(
        employee_department=current_user.get("department"),
        company_id=parse_uuid(current_user["company_id"]),
        date=today,
        check_in_time=now,
        check_in_latitude=data.latitude,
        check_in_longitude=data.longitude,
        check_in_accuracy=data.accuracy,
        distance_from_company_meters=distance,
        device_info=data.device_info,
        status=attendance_status,
    )

    if existing:
        for field, value in fields.items():
            setattr(existing, field, value)
    else:
        await attendance_repo.create(db, employee_id=employee_id, **fields)
    await db.flush()

    return {"message": "Checked in successfully", "status": attendance_status}


async def check_out(db: AsyncSession, current_user: dict, data) -> dict:
    company, settings, distance = await validate_qr_attendance(
        db, current_user, data.qr_code, data.latitude, data.longitude, data.accuracy
    )

    now = datetime.now(timezone.utc)
    today = now.date()
    employee_id = parse_uuid(current_user["id"])

    record = await attendance_repo.get_for_employee_on_date(db, employee_id, today)
    if not record or not record.check_in_time:
        raise HTTPException(status_code=400, detail="Not checked in yet")
    if record.check_out_time:
        raise HTTPException(status_code=400, detail="Already checked out")

    record.check_out_time = now
    record.check_out_latitude = data.latitude
    record.check_out_longitude = data.longitude
    record.check_out_accuracy = data.accuracy
    record.check_out_distance_meters = distance
    record.working_duration_minutes = _duration_minutes(record.check_in_time, now)
    await db.flush()

    return {"message": "Checked out successfully"}


async def get_attendance_history(db: AsyncSession, current_user: dict) -> List[dict]:
    records = await attendance_repo.list_history_for_employee(db, parse_uuid(current_user["id"]))
    return [attendance_response(r, employee_name=current_user["name"]) for r in records]


async def list_attendance_for_owner(db: AsyncSession, company_id, *, date=None, date_from=None, date_to=None,
                                     employee_id=None, department=None, status=None, search=None) -> List[dict]:
    company_id = parse_uuid(company_id)
    date_ = date_type.fromisoformat(date) if date else None
    from_ = date_type.fromisoformat(date_from) if date_from else None
    to_ = date_type.fromisoformat(date_to) if date_to else None
    if from_ is None and to_ is None and date_ is None:
        date_ = datetime.now(timezone.utc).date()

    checked_out = True if status == "checked_out" else None
    status_filter = status if status and status != "checked_out" else None

    records = await attendance_repo.list_by_company(
        db, company_id, date_=date_, date_from=from_, date_to=to_,
        employee_id=parse_uuid(employee_id) if employee_id else None,
        checked_out=checked_out, status=status_filter,
    )

    employees = await users_repo.list_employees_by_company(db, company_id)
    employee_map = {str(e.id): e for e in employees}

    results = []
    for record in records:
        employee = employee_map.get(str(record.employee_id))
        response = attendance_response(record, employee_name=employee.name if employee else None)
        if response["employee_department"] is None and employee:
            response["employee_department"] = employee.department
        if department and response["employee_department"] != department:
            continue
        if search and search.strip().lower() not in (response["employee_name"] or "").lower():
            continue
        results.append(response)

    await holidays_service.annotate_holiday_dates(db, company_id, results)
    return results


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
    expected_attendance = total_employees * day_count
    absence_count = max(0, expected_attendance - len(attended_records))
    attendance_percentage = round(len(attended_records) / expected_attendance * 100, 1) if expected_attendance > 0 else 0
    late_percentage = round(late_count / expected_attendance * 100, 1) if expected_attendance > 0 else 0

    distances = [r.distance_from_company_meters for r in records if r.distance_from_company_meters is not None]
    average_distance_meters = round(sum(distances) / len(distances), 1) if distances else None

    by_date = {}
    for record in records:
        by_date.setdefault(record.date, []).append(record)

    attendance_trend, late_trend, working_hours_trend = [], [], []
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

    per_employee = {
        str(e.id): {
            "employee_id": str(e.id), "name": e.name, "department": e.department,
            "days_attended": 0, "late_count": 0, "total_minutes": 0.0, "duration_days": 0,
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
        "charts": {
            "attendance_trend": attendance_trend,
            "late_trend": late_trend,
            "working_hours_trend": working_hours_trend,
        },
    }


async def edit_attendance(db: AsyncSession, current_user: dict, attendance_id: str, updates) -> dict:
    parsed_id = parse_uuid(attendance_id)
    record = await attendance_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    provided = updates.model_dump(exclude_none=True)
    current_values = {"check_in_time": _iso(record.check_in_time), "check_out_time": _iso(record.check_out_time), "status": record.status}
    changes = {k: v for k, v in provided.items() if k in ATTENDANCE_EDITABLE_FIELDS and current_values.get(k) != v}
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if "status" in changes and changes["status"] not in ("present", "late", "absent"):
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
                "employee_name": names.get(str(record.employee_id)) if record else None,
                "attendance_date": _iso(record.date) if record else None,
            })

    editor_ids = {row.user_id for row in log_rows if row.user_id}
    editor_names = await users_repo.get_names_by_ids(db, editor_ids)
    for entry in entries:
        entry["edited_by_name"] = editor_names.get(entry["edited_by"])

    return entries


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
