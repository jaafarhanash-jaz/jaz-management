import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.audit_logs as audit_logs_repo
import repositories.calendar_attachments as calendar_attachments_repo
import repositories.calendar_event_exceptions as calendar_exceptions_repo
import repositories.calendar_event_participants as calendar_participants_repo
import repositories.calendar_event_reminders as calendar_reminders_repo
import repositories.calendar_events as calendar_events_repo
import repositories.companies as companies_repo
import repositories.counters as counters_repo
import repositories.users as users_repo
import services.messages as messages_service
import services.notifications as notifications_service
from services.admin import parse_uuid
from services.calendar_occurrences import combine_event_datetime, count_raw_occurrences_before, expand_event_occurrences
from services.storage import classify_attachment_type, decode_and_validate, download_base64, upload

COMPLETABLE_CATEGORIES = {"meeting", "training", "task_deadline", "maintenance", "business_trip"}

EVENT_CATEGORY_DEFAULT_COLORS = {
    "meeting": "#0033A0", "company_holiday": "#00A36C", "training": "#7C3AED", "task_deadline": "#E11D48",
    "maintenance": "#6B7280", "business_trip": "#0EA5E9", "reminder": "#FFB800", "announcement": "#EC4899",
    "personal": "#A855F7", "other": "#A1A1AA",
}
VALID_CATEGORIES = set(EVENT_CATEGORY_DEFAULT_COLORS.keys())
VALID_PRIORITIES = {"low", "normal", "high", "critical"}
VALID_VISIBILITIES = {"private", "department", "company", "owner_only"}
VALID_RECIPIENT_TYPES = {"owner", "employee", "department", "owner_plus_employees", "company"}
VALID_LOCATION_TYPES = {"office", "client_site", "online", "other", None}

CALENDAR_REMINDER_PRESETS = {
    "5m": {"minutes": 5}, "15m": {"minutes": 15}, "30m": {"minutes": 30}, "1h": {"hours": 1},
    "6h": {"hours": 6}, "1d": {"days": 1}, "3d": {"days": 3}, "1w": {"weeks": 1},
}
CALENDAR_CONFLICT_LOOKAHEAD_DAYS = 90

CALENDAR_NOTIFICATION_COPY = {
    "created": ("موعد جديد", "تمت دعوتك إلى: {title}"),
    "updated": ("تحديث موعد", "تم تحديث: {title}"),
    "time_changed": ("تغيير موعد", "تم تغيير وقت: {title}"),
    "location_changed": ("تغيير مكان", "تم تغيير مكان: {title}"),
    "cancelled": ("إلغاء موعد", "تم إلغاء: {title}"),
    "attachment_added": ("مرفق جديد", "تمت إضافة مرفق إلى: {title}"),
    "reminder": ("تذكير بموعد", "تذكير: {title}"),
    "owner_created": ("موعد جديد بواسطة موظف", "تم إنشاء موعد يتضمنك: {title}"),
    "accepted": ("تم القبول", "قَبِل {actor_name} دعوة: {title}"),
    "declined": ("تم الرفض", "رفض {actor_name} دعوة: {title}"),
    "tentative": ("رد مبدئي", "رد {actor_name} بشكل مبدئي على: {title}"),
    "all_responded": ("اكتمال الردود", "استجاب جميع المدعوين إلى: {title}"),
}


def require_calendar_access(current_user: dict):
    if current_user["role"] not in ("company_owner", "employee"):
        raise HTTPException(status_code=403, detail="Access denied")


def calendar_paginate(page: int, page_size: int) -> tuple:
    return max(page, 1), min(max(page_size, 1), 200)


def validate_event_fields(category, priority, visibility, recipient_type, location_type):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid priority")
    if visibility not in VALID_VISIBILITIES:
        raise HTTPException(status_code=400, detail="Invalid visibility")
    if recipient_type not in VALID_RECIPIENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid recipient type")
    if location_type not in VALID_LOCATION_TYPES:
        raise HTTPException(status_code=400, detail="Invalid location type")


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _date_iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _clean_ids(raw_ids) -> List[uuid.UUID]:
    return [pid for pid in (parse_uuid(rid) for rid in raw_ids) if pid is not None]


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coerce_changes(changes: dict) -> dict:
    coerced = dict(changes)
    for field in ("start_date", "end_date"):
        if field in coerced and coerced[field] is not None:
            coerced[field] = date_type.fromisoformat(coerced[field])
    return coerced


def _event_raw_kwargs(event) -> dict:
    return {
        "reference_number": event.reference_number, "company_id": event.company_id, "series_id": event.series_id,
        "title": event.title, "description": event.description, "category": event.category,
        "default_color": event.default_color, "custom_color": event.custom_color, "priority": event.priority,
        "start_date": event.start_date, "start_time": event.start_time, "end_date": event.end_date,
        "end_time": event.end_time, "all_day": event.all_day, "location_type": event.location_type,
        "location": event.location, "online_link": event.online_link, "visibility": event.visibility,
        "recipient_type": event.recipient_type, "recipient_ids": list(event.recipient_ids or []),
        "recurrence_type": event.recurrence_type, "recurrence_interval": event.recurrence_interval,
        "recurrence_end_type": event.recurrence_end_type, "recurrence_end_value": event.recurrence_end_value,
        "status": event.status, "linked_thread_id": event.linked_thread_id,
        "meeting_notes_summary": event.meeting_notes_summary, "meeting_notes_decisions": event.meeting_notes_decisions,
        "meeting_notes_action_items": event.meeting_notes_action_items,
        "is_active": event.is_active, "created_by": event.created_by, "created_by_name": event.created_by_name,
        "updated_by": event.updated_by, "updated_by_name": event.updated_by_name,
    }


def event_base_dict(event) -> dict:
    return {
        "id": str(event.id), "reference_number": event.reference_number, "company_id": str(event.company_id),
        "series_id": str(event.series_id), "title": event.title, "description": event.description,
        "category": event.category, "default_color": event.default_color, "custom_color": event.custom_color,
        "priority": event.priority, "start_date": _date_iso(event.start_date), "start_time": event.start_time,
        "end_date": _date_iso(event.end_date), "end_time": event.end_time, "all_day": event.all_day,
        "location_type": event.location_type, "location": event.location, "online_link": event.online_link,
        "visibility": event.visibility, "recipient_type": event.recipient_type,
        "recipient_ids": list(event.recipient_ids or []),
        "recurrence_type": event.recurrence_type, "recurrence_interval": event.recurrence_interval,
        "recurrence_end_type": event.recurrence_end_type, "recurrence_end_value": event.recurrence_end_value,
        "status": event.status, "linked_thread_id": str(event.linked_thread_id) if event.linked_thread_id else None,
        "meeting_notes": {
            "summary": event.meeting_notes_summary, "decisions": event.meeting_notes_decisions,
            "action_items": event.meeting_notes_action_items,
        },
        "is_active": event.is_active, "created_by": str(event.created_by), "created_by_name": event.created_by_name,
        "created_at": _iso(event.created_at), "updated_at": _iso(event.updated_at),
        "updated_by": str(event.updated_by) if event.updated_by else None, "updated_by_name": event.updated_by_name,
    }


def participant_response(p) -> dict:
    return {
        "id": str(p.id), "event_id": str(p.event_id), "company_id": str(p.company_id),
        "participant_id": str(p.participant_id), "participant_name": p.participant_name,
        "department": p.department, "attendance_status": p.attendance_status,
        "responded_at": _iso(p.responded_at), "final_attendance": p.final_attendance,
        "attendance_marked_at": _iso(p.attendance_marked_at),
    }


def compute_display_status(status, category, occurrence_start: datetime, occurrence_end: datetime) -> str:
    if status == "cancelled":
        return "cancelled"
    now = datetime.now(timezone.utc)
    if now < occurrence_start:
        return "scheduled"
    if occurrence_start <= now <= occurrence_end:
        return "in_progress"
    return "completed" if category in COMPLETABLE_CATEGORIES else "expired"


# ---- Recipient resolution ----

async def resolve_calendar_recipients(db: AsyncSession, company_id, recipient_type: str, recipient_ids: List[str],
                                       owner_id) -> List[uuid.UUID]:
    if recipient_type == "owner":
        return [owner_id] if owner_id else []
    if recipient_type == "company":
        employees = await users_repo.list_employees_by_company(db, company_id)
        ids = [e.id for e in employees]
        return ids + ([owner_id] if owner_id else [])
    if recipient_type == "department":
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="At least one department is required")
        employees = await users_repo.list_employees_by_company_and_departments(db, company_id, recipient_ids)
        if not employees:
            raise HTTPException(status_code=400, detail="No employees found in the selected department(s)")
        return [e.id for e in employees]
    if recipient_type == "owner_plus_employees":
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="At least one employee is required")
        unique_raw = set(recipient_ids)
        valid_ids = _clean_ids(unique_raw)
        valid_count = await users_repo.count_matching_ids_in_company(db, valid_ids, company_id)
        if valid_count != len(unique_raw):
            raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")
        return valid_ids + ([owner_id] if owner_id else [])
    # employee
    if not recipient_ids:
        raise HTTPException(status_code=400, detail="At least one participant is required")
    unique_raw = set(recipient_ids)
    valid_ids = _clean_ids(unique_raw)
    valid_count = await users_repo.count_matching_ids_in_company(db, valid_ids, company_id)
    if valid_count != len(unique_raw):
        raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")
    return valid_ids


async def _create_participants(db: AsyncSession, event_id, company_id, participant_ids: List[uuid.UUID]) -> None:
    user_map = await users_repo.get_by_ids(db, participant_ids)
    for pid in participant_ids:
        u = user_map.get(str(pid))
        await calendar_participants_repo.create(
            db, event_id=event_id, company_id=company_id, participant_id=pid,
            participant_name=u.name if u else None, department=u.department if u else None,
            attendance_status="no_response", responded_at=None, final_attendance=None, attendance_marked_at=None,
        )


# ---- Conflict / holiday detection ----

async def check_calendar_conflicts(db: AsyncSession, company_id, participant_ids: List[uuid.UUID], start_dt: datetime,
                                    end_dt: datetime, all_day: bool, exclude_event_id=None) -> List[dict]:
    window_end = min(end_dt, start_dt + timedelta(days=CALENDAR_CONFLICT_LOOKAHEAD_DAYS))
    candidates = await calendar_events_repo.list_conflict_candidates(
        db, company_id, window_end.date(), start_dt.date(), exclude_event_id=exclude_event_id, all_day_only=all_day,
    )
    if not candidates:
        return []

    candidate_ids = [c.id for c in candidates]
    participant_rows = await calendar_participants_repo.list_for_events(db, candidate_ids)
    participant_id_set = set(participant_ids)
    rows_by_event = {}
    for r in participant_rows:
        if r.participant_id in participant_id_set:
            rows_by_event.setdefault(r.event_id, []).append(r)

    conflicts = []
    for candidate in candidates:
        involved_rows = rows_by_event.get(candidate.id, [])
        if involved_rows:
            names = [p.participant_name for p in involved_rows if p.participant_name]
        elif candidate.created_by in participant_id_set:
            names = [candidate.created_by_name] if candidate.created_by_name else []
        else:
            continue
        for occ in await expand_event_occurrences(db, candidate, start_dt, window_end):
            occ_start = datetime.fromisoformat(occ["occurrence_start"])
            occ_end = datetime.fromisoformat(occ["occurrence_end"])
            if occ_start < end_dt and occ_end > start_dt:
                conflicts.append({
                    "event_id": str(candidate.id), "title": candidate.title,
                    "occurrence_date": occ["occurrence_date"], "participants": names,
                })
                break
    return conflicts


async def check_holiday_conflict(db: AsyncSession, company_id, start_dt: datetime, end_dt: datetime) -> Optional[dict]:
    holidays = await calendar_events_repo.list_holiday_conflict_candidates(db, company_id, start_dt.date(), end_dt.date())
    for holiday in holidays:
        for occ in await expand_event_occurrences(db, holiday, start_dt, end_dt):
            occ_start = datetime.fromisoformat(occ["occurrence_start"])
            occ_end = datetime.fromisoformat(occ["occurrence_end"])
            if occ_start < end_dt and occ_end > start_dt:
                return {"event_id": str(holiday.id), "title": holiday.title, "occurrence_date": occ["occurrence_date"]}
    return None


# ---- Activity log / notifications ----

async def write_calendar_activity(db: AsyncSession, company_id, event_id, actor: dict, verb: str, event_title: str):
    await audit_logs_repo.create(
        db, company_id=company_id, user_id=parse_uuid(actor["id"]) if actor.get("id") else None,
        entity_type="calendar_event", entity_id=event_id, action=verb, new_values={"event_title": event_title},
    )


async def notify_for_calendar_event(db: AsyncSession, event, verb: str, participant_ids: List[uuid.UUID],
                                     actor: Optional[dict] = None, notify_owner_too: bool = False):
    title_template, message_template = CALENDAR_NOTIFICATION_COPY.get(verb, ("تحديث موعد", "{title}"))
    recipients = {pid for pid in participant_ids if pid}
    if notify_owner_too:
        company = await companies_repo.get_by_id(db, event.company_id)
        if company:
            recipients.add(company.owner_id)
    actor_id = parse_uuid(actor["id"]) if actor and actor.get("id") else None
    recipients.discard(actor_id)

    for user_id in recipients:
        await notifications_service.publish(
            db, user_id=user_id, company_id=event.company_id,
            category=notifications_service.CATEGORY_CALENDAR, type=f"calendar_{verb}",
            title=title_template, message=message_template.format(title=event.title or "", actor_name=(actor or {}).get("name", "")),
            entity_type="calendar_event", entity_id=event.id, action_url="/company-owner/calendar",
            sender_id=actor_id, sender_name=(actor or {}).get("name"),
        )


async def _reconstruct_calendar_activity(db: AsyncSession, rows) -> List[dict]:
    actor_ids = {r.user_id for r in rows if r.user_id}
    names = await users_repo.get_names_by_ids(db, actor_ids)
    return [{
        "id": str(r.id), "company_id": str(r.company_id), "event_id": str(r.entity_id),
        "actor_id": str(r.user_id) if r.user_id else None, "actor_name": names.get(str(r.user_id)) if r.user_id else None,
        "verb": r.action, "event_title": (r.new_values or {}).get("event_title"),
        "created_at": _iso(r.created_at),
    } for r in rows]


# ---- Access checks ----

async def get_accessible_calendar_event(db: AsyncSession, event_id: str, current_user: dict):
    parsed_id = parse_uuid(event_id)
    event = await calendar_events_repo.get_by_id(db, parsed_id) if parsed_id else None
    if not event or str(event.company_id) != current_user.get("company_id"):
        raise HTTPException(status_code=404, detail="Event not found")

    if current_user["role"] == "company_owner":
        return event
    if str(event.created_by) == current_user["id"]:
        return event
    participant = await calendar_participants_repo.get_participant(db, event.id, parse_uuid(current_user["id"]))
    if participant:
        return event

    visibility = event.visibility or "company"
    if visibility == "company":
        return event
    if visibility == "department":
        depts = await calendar_participants_repo.departments_for_event_matching(db, event.id)
        if current_user.get("department") in depts:
            return event

    raise HTTPException(status_code=404, detail="Event not found")


async def get_event_for_edit(db: AsyncSession, event_id: str, current_user: dict):
    parsed_id = parse_uuid(event_id)
    event = await calendar_events_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not event or (str(event.created_by) != current_user["id"] and current_user["role"] != "company_owner"):
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# ---- Response building ----

async def build_event_detail(db: AsyncSession, event, current_user: dict) -> dict:
    participants = await calendar_participants_repo.list_for_event(db, event.id)
    attachment_count = await calendar_attachments_repo.count_for_event(db, event.id)
    occ_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
    occ_end = combine_event_datetime(event.end_date, event.end_time, event.all_day, True)
    mine = next((p for p in participants if str(p.participant_id) == current_user["id"]), None)
    responded = [p for p in participants if p.attendance_status != "no_response"]

    base = event_base_dict(event)
    base.update({
        "display_status": compute_display_status(event.status, event.category, occ_start, occ_end),
        "color": event.custom_color or event.default_color,
        "participants": [participant_response(p) for p in participants],
        "response_progress": {"total": len(participants), "responded": len(responded)},
        "attachment_count": attachment_count,
        "my_attendance_status": mine.attendance_status if mine else None,
        "is_participant": mine is not None,
    })
    return base


# ---- Compose / CRUD ----

async def create_calendar_event(db: AsyncSession, current_user: dict, data) -> dict:
    require_calendar_access(current_user)
    validate_event_fields(data.category, data.priority, data.visibility, data.recipient_type, data.location_type)

    company_id = parse_uuid(current_user["company_id"])
    start_dt = combine_event_datetime(data.start_date, data.start_time, data.all_day, False)
    end_dt = combine_event_datetime(data.end_date, data.end_time, data.all_day, True)
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="End must be on or after start")

    company = await companies_repo.get_by_id(db, company_id)
    owner_id = company.owner_id if company else None
    participant_ids = await resolve_calendar_recipients(db, company_id, data.recipient_type, data.recipient_ids, owner_id)

    conflicts = await check_calendar_conflicts(db, company_id, participant_ids, start_dt, end_dt, data.all_day)
    holiday_conflict = None
    if data.category != "company_holiday":
        holiday_conflict = await check_holiday_conflict(db, company_id, start_dt, end_dt)
    if (conflicts or holiday_conflict) and not data.override_conflicts:
        raise HTTPException(status_code=409, detail={"message": "Conflict detected", "conflicts": conflicts, "holiday_conflict": holiday_conflict})

    event_id = uuid.uuid4()
    reference_number = f"CAL-{await counters_repo.increment_and_get(db, f'calendar_seq:{company_id}'):06d}"
    current_user_id = parse_uuid(current_user["id"])
    event = await calendar_events_repo.create(
        db, id=event_id, reference_number=reference_number, company_id=company_id, series_id=event_id,
        title=data.title, description=data.description, category=data.category,
        default_color=EVENT_CATEGORY_DEFAULT_COLORS.get(data.category, "#A1A1AA"), custom_color=data.custom_color,
        priority=data.priority, start_date=date_type.fromisoformat(data.start_date), start_time=data.start_time,
        end_date=date_type.fromisoformat(data.end_date), end_time=data.end_time, all_day=data.all_day,
        location_type=data.location_type, location=data.location, online_link=data.online_link,
        visibility=data.visibility, recipient_type=data.recipient_type, recipient_ids=data.recipient_ids,
        recurrence_type=data.recurrence_type, recurrence_interval=data.recurrence_interval,
        recurrence_end_type=data.recurrence_end_type, recurrence_end_value=data.recurrence_end_value,
        status=None, linked_thread_id=None,
        meeting_notes_summary="", meeting_notes_decisions="", meeting_notes_action_items="",
        is_active=True, created_by=current_user_id, created_by_name=current_user.get("name"),
        updated_by=current_user_id, updated_by_name=current_user.get("name"),
    )

    await _create_participants(db, event_id, company_id, participant_ids)

    await write_calendar_activity(db, company_id, event_id, current_user, "created", data.title)
    owner_should_be_notified = current_user["role"] == "employee" and owner_id in participant_ids
    await notify_for_calendar_event(
        db, event, "owner_created" if owner_should_be_notified else "created", participant_ids, actor=current_user,
    )

    return await build_event_detail(db, event, current_user)


async def update_calendar_event(db: AsyncSession, current_user: dict, event_id: str, updates, scope: str,
                                 occurrence_date: Optional[str]) -> dict:
    require_calendar_access(current_user)
    event = await get_event_for_edit(db, event_id, current_user)

    changes = updates.model_dump(exclude_none=True, exclude={"override_conflicts"})
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    for field, valid_set in (("category", VALID_CATEGORIES), ("priority", VALID_PRIORITIES),
                              ("visibility", VALID_VISIBILITIES), ("location_type", VALID_LOCATION_TYPES)):
        if field in changes and changes[field] not in valid_set:
            raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if "category" in changes:
        changes["default_color"] = EVENT_CATEGORY_DEFAULT_COLORS.get(changes["category"], "#A1A1AA")

    time_fields_changed = bool({"start_date", "start_time", "end_date", "end_time", "all_day"} & changes.keys())
    location_changed = bool({"location", "location_type", "online_link"} & changes.keys())

    merged_preview = {
        "start_date": changes.get("start_date", event.start_date.isoformat()),
        "start_time": changes.get("start_time", event.start_time),
        "end_date": changes.get("end_date", event.end_date.isoformat()),
        "end_time": changes.get("end_time", event.end_time),
        "all_day": changes.get("all_day", event.all_day),
        "category": changes.get("category", event.category),
    }
    if scope in ("this_event_only", "this_and_future") and occurrence_date and "start_date" not in changes:
        merged_preview["start_date"] = occurrence_date
        if "end_date" not in changes:
            span_days = (event.end_date - event.start_date).days
            merged_preview["end_date"] = (date_type.fromisoformat(occurrence_date) + timedelta(days=span_days)).isoformat()

    if time_fields_changed:
        start_dt = combine_event_datetime(merged_preview["start_date"], merged_preview["start_time"], merged_preview["all_day"], False)
        end_dt = combine_event_datetime(merged_preview["end_date"], merged_preview["end_time"], merged_preview["all_day"], True)
        if end_dt < start_dt:
            raise HTTPException(status_code=400, detail="End must be on or after start")
        participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
        conflicts = await check_calendar_conflicts(
            db, event.company_id, participant_ids, start_dt, end_dt, merged_preview["all_day"], exclude_event_id=event.id,
        )
        holiday_conflict = None
        if merged_preview["category"] != "company_holiday":
            holiday_conflict = await check_holiday_conflict(db, event.company_id, start_dt, end_dt)
        if (conflicts or holiday_conflict) and not updates.override_conflicts:
            raise HTTPException(status_code=409, detail={"message": "Conflict detected", "conflicts": conflicts, "holiday_conflict": holiday_conflict})

    now = datetime.now(timezone.utc)

    if scope == "this_event_only":
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required for this_event_only edits")
        occ_date = date_type.fromisoformat(occurrence_date)
        existing = await calendar_exceptions_repo.get_for_event_and_date(db, event.id, occ_date)
        if existing:
            existing.override_fields = changes
            existing.is_cancelled = False
        else:
            await calendar_exceptions_repo.create(db, event_id=event.id, occurrence_date=occ_date, is_cancelled=False, override_fields=changes)
        target_event = event

    elif scope == "this_and_future":
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required for this_and_future edits")
        original_recurrence_end_type = event.recurrence_end_type
        original_recurrence_end_value = event.recurrence_end_value
        span_days = (event.end_date - event.start_date).days

        day_before = (date_type.fromisoformat(occurrence_date) - timedelta(days=1)).isoformat()
        event.recurrence_end_type = "on_date"
        event.recurrence_end_value = day_before
        event.updated_at = now

        new_start = changes.get("start_date", occurrence_date)
        new_end = changes.get("end_date", (date_type.fromisoformat(new_start) + timedelta(days=span_days)).isoformat())

        new_recurrence_end_type = original_recurrence_end_type or "never"
        new_recurrence_end_value = original_recurrence_end_value
        if new_recurrence_end_type == "after_count" and new_recurrence_end_value:
            cutoff = datetime.fromisoformat(f"{occurrence_date}T00:00:00+00:00")
            occurrences_before = count_raw_occurrences_before(event, cutoff)
            remaining = max(int(new_recurrence_end_value) - occurrences_before, 1)
            new_recurrence_end_value = str(remaining)

        new_event_id = uuid.uuid4()
        new_reference_number = f"CAL-{await counters_repo.increment_and_get(db, f'calendar_seq:{event.company_id}'):06d}"
        new_kwargs = _event_raw_kwargs(event)
        new_kwargs.update(_coerce_changes(changes))
        new_kwargs.update({
            "id": new_event_id, "reference_number": new_reference_number, "series_id": event.series_id or event.id,
            "start_date": date_type.fromisoformat(new_start), "end_date": date_type.fromisoformat(new_end),
            "recurrence_end_type": new_recurrence_end_type, "recurrence_end_value": new_recurrence_end_value,
            "status": None, "linked_thread_id": None,
            "meeting_notes_summary": "", "meeting_notes_decisions": "", "meeting_notes_action_items": "",
            "updated_by": parse_uuid(current_user["id"]), "updated_by_name": current_user.get("name"),
        })
        new_event = await calendar_events_repo.create(db, **new_kwargs)

        old_participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
        await _create_participants(db, new_event_id, event.company_id, old_participant_ids)
        target_event = new_event

    else:  # entire_series
        for field, value in _coerce_changes(changes).items():
            setattr(event, field, value)
        event.updated_at = now
        event.updated_by = parse_uuid(current_user["id"])
        event.updated_by_name = current_user.get("name")
        target_event = event

    verb = "time_changed" if time_fields_changed else "location_changed" if location_changed else "updated"
    await write_calendar_activity(db, event.company_id, event.id, current_user, verb, event.title)
    participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
    if participant_ids:
        await notify_for_calendar_event(db, target_event, verb, participant_ids, actor=current_user)

    return await build_event_detail(db, target_event, current_user)


async def cancel_calendar_event(db: AsyncSession, current_user: dict, event_id: str, scope: str,
                                 occurrence_date: Optional[str]) -> dict:
    require_calendar_access(current_user)
    if scope not in ("this_event_only", "entire_series"):
        raise HTTPException(status_code=400, detail="scope must be this_event_only or entire_series")
    event = await get_event_for_edit(db, event_id, current_user)

    if scope == "this_event_only":
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required")
        occ_date = date_type.fromisoformat(occurrence_date)
        existing = await calendar_exceptions_repo.get_for_event_and_date(db, event.id, occ_date)
        if existing:
            existing.is_cancelled = True
        else:
            await calendar_exceptions_repo.create(db, event_id=event.id, occurrence_date=occ_date, is_cancelled=True, override_fields=None)
    else:
        event.status = "cancelled"
        event.updated_at = datetime.now(timezone.utc)

    await write_calendar_activity(db, event.company_id, event.id, current_user, "cancelled", event.title)
    participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
    if participant_ids:
        await notify_for_calendar_event(db, event, "cancelled", participant_ids, actor=current_user)
    return {"message": "Event cancelled"}


# ---- Participants / attendance / notes ----

async def add_calendar_participants(db: AsyncSession, current_user: dict, event_id: str, participant_ids_raw: List[str]) -> dict:
    require_calendar_access(current_user)
    event = await get_event_for_edit(db, event_id, current_user)
    existing_ids = {str(pid) for pid in await calendar_participants_repo.participant_ids_for_event(db, event.id)}
    new_id_strings = {pid for pid in set(participant_ids_raw) if pid not in existing_ids}
    if not new_id_strings:
        raise HTTPException(status_code=400, detail="No new participants to add")
    new_ids = _clean_ids(new_id_strings)
    valid = await users_repo.count_matching_ids_in_company(db, new_ids, event.company_id)
    if valid != len(new_id_strings):
        raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")

    await _create_participants(db, event.id, event.company_id, new_ids)
    await write_calendar_activity(db, event.company_id, event.id, current_user, "participant_added", event.title)
    await notify_for_calendar_event(db, event, "created", new_ids, actor=current_user)
    return {"message": "Participants added", "added": [str(pid) for pid in new_ids]}


async def remove_calendar_participants(db: AsyncSession, current_user: dict, event_id: str, participant_ids_raw: List[str]) -> dict:
    require_calendar_access(current_user)
    event = await get_event_for_edit(db, event_id, current_user)
    clean_ids = _clean_ids(participant_ids_raw)
    deleted_count = await calendar_participants_repo.delete_participants(db, event.id, clean_ids) if clean_ids else 0
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="No matching participants found")
    await write_calendar_activity(db, event.company_id, event.id, current_user, "participant_removed", event.title)
    await notify_for_calendar_event(db, event, "cancelled", clean_ids, actor=current_user)
    return {"message": "Participants removed"}


async def mark_final_attendance(db: AsyncSession, current_user: dict, event_id: str, participant_id: str, status: str) -> dict:
    require_calendar_access(current_user)
    if status not in ("attended", "absent"):
        raise HTTPException(status_code=400, detail="Invalid attendance status")
    event = await get_event_for_edit(db, event_id, current_user)

    occ_end = combine_event_datetime(event.end_date, event.end_time, event.all_day, True)
    if datetime.now(timezone.utc) < occ_end:
        raise HTTPException(status_code=400, detail="Cannot mark attendance before the meeting has finished")

    parsed_pid = parse_uuid(participant_id)
    participant = await calendar_participants_repo.get_participant(db, event.id, parsed_pid) if parsed_pid else None
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found on this event")

    participant.final_attendance = status
    participant.attendance_marked_at = datetime.now(timezone.utc)
    verb = "marked_attended" if status == "attended" else "marked_absent"
    await write_calendar_activity(db, event.company_id, event.id, current_user, verb, event.title)
    return {"message": "Attendance recorded", "status": status}


async def update_meeting_notes(db: AsyncSession, current_user: dict, event_id: str, data) -> dict:
    require_calendar_access(current_user)
    event = await get_event_for_edit(db, event_id, current_user)
    event.meeting_notes_summary = data.summary
    event.meeting_notes_decisions = data.decisions
    event.meeting_notes_action_items = data.action_items
    event.updated_at = datetime.now(timezone.utc)
    await write_calendar_activity(db, event.company_id, event.id, current_user, "notes_updated", event.title)
    return {"message": "Meeting notes updated", "meeting_notes": {
        "summary": data.summary, "decisions": data.decisions, "action_items": data.action_items,
    }}


async def open_conversation(db: AsyncSession, current_user: dict, event_id: str) -> dict:
    require_calendar_access(current_user)
    event = await get_accessible_calendar_event(db, event_id, current_user)
    if event.linked_thread_id:
        return {"thread_id": str(event.linked_thread_id), "created": False}

    participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
    other_ids = [str(pid) for pid in participant_ids if str(pid) != current_user["id"]]
    if not other_ids:
        raise HTTPException(status_code=400, detail="No other participants to start a conversation with")

    message_data = SimpleNamespace(
        subject=f"{event.title} ({event.reference_number})",
        body=f"محادثة بخصوص الموعد: {event.title} - {event.start_date.isoformat()}",
        priority="normal", confidentiality="internal", tags=[], recipient_type="employee", recipient_ids=other_ids,
        requires_acknowledgement=False, completion_required=False, expires_at=None, is_draft=False,
    )
    created = await messages_service.create_message(db, current_user, message_data)
    thread_id = created["thread_id"]

    event.linked_thread_id = parse_uuid(thread_id)
    await write_calendar_activity(db, event.company_id, event.id, current_user, "conversation_started", event.title)
    return {"thread_id": thread_id, "created": True}


async def check_conflicts_endpoint(db: AsyncSession, current_user: dict, data) -> dict:
    require_calendar_access(current_user)
    company_id = parse_uuid(current_user["company_id"])
    company = await companies_repo.get_by_id(db, company_id)
    owner_id = company.owner_id if company else None
    participant_ids = await resolve_calendar_recipients(db, company_id, data.recipient_type, data.recipient_ids, owner_id)

    start_dt = combine_event_datetime(data.start_date, data.start_time, data.all_day, False)
    end_dt = combine_event_datetime(data.end_date, data.end_time, data.all_day, True)
    exclude_id = parse_uuid(data.exclude_event_id) if data.exclude_event_id else None
    conflicts = await check_calendar_conflicts(db, company_id, participant_ids, start_dt, end_dt, data.all_day, exclude_event_id=exclude_id)
    holiday_conflict = await check_holiday_conflict(db, company_id, start_dt, end_dt)
    return {"conflicts": conflicts, "holiday_conflict": holiday_conflict, "has_conflict": bool(conflicts or holiday_conflict)}


async def respond_to_calendar_event(db: AsyncSession, current_user: dict, event_id: str, status: str) -> dict:
    require_calendar_access(current_user)
    if status not in ("accepted", "declined", "tentative"):
        raise HTTPException(status_code=400, detail="Invalid response status")
    event = await get_accessible_calendar_event(db, event_id, current_user)
    participant = await calendar_participants_repo.get_participant(db, event.id, parse_uuid(current_user["id"]))
    if not participant:
        raise HTTPException(status_code=404, detail="You are not a participant of this event")

    participant.attendance_status = status
    participant.responded_at = datetime.now(timezone.utc)
    await write_calendar_activity(db, event.company_id, event.id, current_user, status, event.title)
    await notify_for_calendar_event(db, event, status, [], actor=current_user, notify_owner_too=True)

    all_participants = await calendar_participants_repo.list_for_event(db, event.id)
    if all_participants and all(p.attendance_status != "no_response" for p in all_participants):
        await notify_for_calendar_event(db, event, "all_responded", [], actor=current_user, notify_owner_too=True)

    return {"message": "Response recorded", "status": status}


# ---- Reminders ----

async def create_calendar_reminder(db: AsyncSession, current_user: dict, event_id: str, data) -> dict:
    require_calendar_access(current_user)
    event = await get_accessible_calendar_event(db, event_id, current_user)

    if data.preset:
        if data.preset not in CALENDAR_REMINDER_PRESETS:
            raise HTTPException(status_code=400, detail="Invalid reminder preset")
        event_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
        remind_at = event_start - timedelta(**CALENDAR_REMINDER_PRESETS[data.preset])
    elif data.remind_at:
        remind_at = _parse_dt(data.remind_at)
    else:
        raise HTTPException(status_code=400, detail="Provide a preset or an explicit remind_at")

    reminder = await calendar_reminders_repo.create(
        db, event_id=event.id, user_id=parse_uuid(current_user["id"]), company_id=event.company_id, remind_at=remind_at,
    )
    return {"id": str(reminder.id), "remind_at": _iso(reminder.remind_at)}


# ---- Attachments ----

async def upload_calendar_attachment(db: AsyncSession, current_user: dict, event_id: str, data) -> dict:
    require_calendar_access(current_user)
    event = await get_event_for_edit(db, event_id, current_user)

    decoded = decode_and_validate(data.data, data.filename, data.mime_type)
    storage_path, checksum = await upload(decoded, prefix=f"calendar/{event.id}")

    attachment = await calendar_attachments_repo.create(
        db, event_id=event.id, company_id=event.company_id, storage_path=storage_path,
        original_filename=data.filename, mime_type=data.mime_type,
        attachment_type=classify_attachment_type(data.mime_type), file_size=len(decoded), checksum=checksum,
        uploaded_by=parse_uuid(current_user["id"]),
    )
    await write_calendar_activity(db, event.company_id, event.id, current_user, "attachment_added", event.title)
    participant_ids = await calendar_participants_repo.participant_ids_for_event(db, event.id)
    if participant_ids:
        await notify_for_calendar_event(db, event, "attachment_added", participant_ids, actor=current_user)
    return {
        "id": str(attachment.id), "filename": attachment.original_filename,
        "attachment_type": attachment.attachment_type, "size_bytes": attachment.file_size,
    }


async def get_calendar_attachment(db: AsyncSession, current_user: dict, attachment_id: str) -> dict:
    require_calendar_access(current_user)
    parsed_id = parse_uuid(attachment_id)
    attachment = await calendar_attachments_repo.get_by_id(db, parsed_id) if parsed_id else None
    if not attachment or str(attachment.company_id) != current_user["company_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await get_accessible_calendar_event(db, str(attachment.event_id), current_user)
    return {
        "id": str(attachment.id), "event_id": str(attachment.event_id), "company_id": str(attachment.company_id),
        "filename": attachment.original_filename, "mime_type": attachment.mime_type,
        "attachment_type": attachment.attachment_type, "size_bytes": attachment.file_size,
        "uploaded_at": _iso(attachment.uploaded_at),
        "data": f"data:{attachment.mime_type};base64,{await download_base64(attachment.storage_path)}",
    }


# ---- Working hours ----

async def get_company_working_hours(db: AsyncSession, current_user: dict) -> dict:
    if current_user["role"] not in ("company_owner", "employee"):
        raise HTTPException(status_code=403, detail="Access denied")
    return await companies_repo.get_working_hours(db, parse_uuid(current_user["company_id"]))


async def update_company_working_hours(db: AsyncSession, current_user: dict, data) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    await companies_repo.update_working_hours(
        db, parse_uuid(current_user["company_id"]), data.working_days, data.start_time, data.end_time,
    )
    return {"message": "Working hours updated", "working_hours": data.model_dump()}


# ---- Owner monitor / activity ----

async def calendar_owner_monitor(db: AsyncSession, current_user: dict, page: int, page_size: int) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    page, page_size = calendar_paginate(page, page_size)
    company_id = parse_uuid(current_user["company_id"])
    total = await calendar_events_repo.count_by_company(db, company_id)
    events = await calendar_events_repo.list_by_company_paginated(db, company_id, page, page_size)

    event_ids = [e.id for e in events]
    participants = await calendar_participants_repo.list_for_events(db, event_ids)
    by_event = {}
    for p in participants:
        by_event.setdefault(p.event_id, []).append(p)

    items = []
    for event in events:
        occ_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
        occ_end = combine_event_datetime(event.end_date, event.end_time, event.all_day, True)
        base = event_base_dict(event)
        base["display_status"] = compute_display_status(event.status, event.category, occ_start, occ_end)
        base["participants"] = [participant_response(p) for p in by_event.get(event.id, [])]
        items.append(base)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_calendar_event_activity(db: AsyncSession, current_user: dict, event_id: str) -> List[dict]:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    parsed_id = parse_uuid(event_id)
    event = await calendar_events_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    rows = await audit_logs_repo.list_for_entity_desc(db, "calendar_event", event.id)
    return await _reconstruct_calendar_activity(db, rows)


# ---- Search / list ----

async def search_calendar_events(db: AsyncSession, current_user: dict, *, page, page_size, reference_number, title,
                                  participant, department, category, priority, created_by, status, date_from, date_to) -> dict:
    require_calendar_access(current_user)
    page, page_size = calendar_paginate(page, page_size)
    today = datetime.now(timezone.utc).date()
    range_from = date_type.fromisoformat(date_from) if date_from else today - timedelta(days=30)
    range_to = date_type.fromisoformat(date_to) if date_to else today + timedelta(days=90)
    company_id = parse_uuid(current_user["company_id"])

    event_ids = None
    if participant or department:
        event_ids = await calendar_participants_repo.event_ids_matching(
            db, company_id, participant_name=participant, department=department,
        )

    candidates = await calendar_events_repo.search_candidates(
        db, company_id, range_from, range_to, reference_number=reference_number, title=title,
        category=category, priority=priority, created_by_name=created_by, event_ids=event_ids,
    )

    is_owner = current_user["role"] == "company_owner"
    current_user_id_uuid = parse_uuid(current_user["id"])
    results = []
    for event in candidates:
        if not is_owner and str(event.created_by) != current_user["id"]:
            accessible = await calendar_participants_repo.get_participant(db, event.id, current_user_id_uuid)
            visibility = event.visibility or "company"
            if not accessible and visibility != "company":
                if visibility == "department":
                    depts = await calendar_participants_repo.departments_for_event_matching(db, event.id)
                    if current_user.get("department") not in depts:
                        continue
                else:
                    continue

        occ_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
        occ_end = combine_event_datetime(event.end_date, event.end_time, event.all_day, True)
        display_status = compute_display_status(event.status, event.category, occ_start, occ_end)
        if status and status != display_status:
            continue
        base = event_base_dict(event)
        base["display_status"] = display_status
        base["color"] = event.custom_color or event.default_color
        results.append(base)

    results.sort(key=lambda e: e["start_date"], reverse=True)
    total = len(results)
    start_idx = (page - 1) * page_size
    return {"items": results[start_idx:start_idx + page_size], "total": total, "page": page, "page_size": page_size}


async def list_calendar_events(db: AsyncSession, current_user: dict, view_start: Optional[str], view_end: Optional[str],
                                page: int, page_size: int) -> dict:
    require_calendar_access(current_user)
    if not view_start or not view_end:
        raise HTTPException(status_code=400, detail="view_start and view_end are required")
    page, page_size = calendar_paginate(page, page_size)
    range_start = datetime.fromisoformat(f"{view_start}T00:00:00+00:00")
    range_end = datetime.fromisoformat(f"{view_end}T23:59:59+00:00")
    company_id = parse_uuid(current_user["company_id"])

    candidates = await calendar_events_repo.list_range_candidates(
        db, company_id, date_type.fromisoformat(view_start), date_type.fromisoformat(view_end),
    )

    is_owner = current_user["role"] == "company_owner"
    current_user_id_uuid = parse_uuid(current_user["id"])
    accessible = []
    if candidates:
        candidate_ids = [c.id for c in candidates]
        my_event_ids = await calendar_participants_repo.event_ids_for_participant(db, candidate_ids, current_user_id_uuid)
        my_department = current_user.get("department")
        dept_event_ids = await calendar_participants_repo.event_ids_for_department(db, candidate_ids, my_department) if my_department else set()

        for c in candidates:
            if is_owner or str(c.created_by) == current_user["id"] or c.id in my_event_ids:
                accessible.append(c)
                continue
            visibility = c.visibility or "company"
            if visibility == "company":
                accessible.append(c)
            elif visibility == "department" and c.id in dept_event_ids:
                accessible.append(c)

    all_occurrences = []
    for event in accessible:
        for occ in await expand_event_occurrences(db, event, range_start, range_end):
            item = {k: v for k, v in occ.items() if k != "recipient_ids"}
            item["id"] = str(item["id"])
            item["company_id"] = str(item["company_id"])
            item["series_id"] = str(item["series_id"])
            item["created_by"] = str(item["created_by"])
            item["linked_thread_id"] = str(item["linked_thread_id"]) if item.get("linked_thread_id") else None
            item["color"] = occ.get("custom_color") or occ.get("default_color")
            item["display_status"] = compute_display_status(
                occ["status"], occ["category"],
                datetime.fromisoformat(occ["occurrence_start"]), datetime.fromisoformat(occ["occurrence_end"]),
            )
            all_occurrences.append(item)
    all_occurrences.sort(key=lambda o: o["occurrence_start"])

    total = len(all_occurrences)
    start_idx = (page - 1) * page_size
    page_items = all_occurrences[start_idx:start_idx + page_size]
    working_hours = await companies_repo.get_working_hours(db, company_id)
    return {"items": page_items, "total": total, "page": page, "page_size": page_size, "working_hours": working_hours}


# ---- Dashboard widgets ----

async def compute_calendar_dashboard_widgets(db: AsyncSession, current_user: dict, is_owner: bool) -> dict:
    company_id = parse_uuid(current_user["company_id"])
    current_user_id_uuid = parse_uuid(current_user["id"])
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today_start + timedelta(days=7)

    if is_owner:
        candidates = await calendar_events_repo.list_upcoming_window(
            db, company_id, today_start.date(), week_end.date(),
        )
    else:
        my_event_ids = await calendar_participants_repo.all_event_ids_for_participant_in_company(db, company_id, current_user_id_uuid)
        candidates = await calendar_events_repo.list_upcoming_window(
            db, company_id, today_start.date(), week_end.date(), event_ids=my_event_ids,
        ) if my_event_ids else []

    # One query for every candidate event's exceptions instead of
    # expand_event_occurrences issuing its own per-event query in the loop
    # below - this runs on every dashboard load (owner and employee), so an
    # upcoming-week window with N events previously cost N round trips here
    # alone.
    exceptions_by_event: dict = {}
    if candidates:
        all_exceptions = await calendar_exceptions_repo.list_for_events(db, [e.id for e in candidates])
        for exc in all_exceptions:
            exceptions_by_event.setdefault(exc.event_id, []).append(exc)

    occurrences = []
    for event in candidates:
        event_exceptions = exceptions_by_event.get(event.id, [])
        for occ in await expand_event_occurrences(db, event, today_start, week_end, exceptions=event_exceptions):
            occurrences.append({**occ, "start": datetime.fromisoformat(occ["occurrence_start"])})
    occurrences.sort(key=lambda o: o["start"])

    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = today_start + timedelta(days=2)
    today_events = [o for o in occurrences if today_start <= o["start"] < tomorrow_start]
    tomorrow_events = [o for o in occurrences if tomorrow_start <= o["start"] < tomorrow_end]
    future_occurrences = [o for o in occurrences if o["start"] >= now]
    next_event = future_occurrences[0] if future_occurrences else None
    meetings_starting_soon = [o for o in future_occurrences if o["category"] == "meeting" and o["start"] <= now + timedelta(hours=1)]
    upcoming_meetings = [o for o in future_occurrences if o["category"] == "meeting"][:5]
    upcoming_holidays = [o for o in occurrences if o["category"] == "company_holiday"][:5]
    high_priority = [o for o in future_occurrences if o["priority"] in ("high", "critical")][:5]

    widgets = {
        "today_events": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"], "category": o["category"], "priority": o["priority"]} for o in today_events],
        "tomorrow_events": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"]} for o in tomorrow_events],
        "next_event": {"id": str(next_event["id"]), "title": next_event["title"], "start": next_event["occurrence_start"],
                        "seconds_remaining": max(int((next_event["start"] - now).total_seconds()), 0)} if next_event else None,
        "upcoming_meetings": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"]} for o in upcoming_meetings],
        "upcoming_holidays": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"]} for o in upcoming_holidays],
    }
    if is_owner:
        waiting_ids = [o["id"] for o in future_occurrences]
        waiting_event_ids = set()
        if waiting_ids:
            rows = await calendar_participants_repo.list_for_events(db, waiting_ids)
            waiting_event_ids = {r.event_id for r in rows if r.attendance_status == "no_response"}
        widgets.update({
            "meetings_starting_soon": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"]} for o in meetings_starting_soon],
            "events_requiring_response": len(waiting_event_ids),
            "high_priority_events": [{"id": str(o["id"]), "title": o["title"], "start": o["occurrence_start"], "priority": o["priority"]} for o in high_priority],
        })
    else:
        my_pending = 0
        future_ids = [o["id"] for o in future_occurrences]
        if future_ids:
            rows = await calendar_participants_repo.list_for_events(db, future_ids)
            my_pending = sum(1 for r in rows if r.participant_id == current_user_id_uuid and r.attendance_status == "no_response")
        widgets["unanswered_invitations"] = my_pending
    return widgets


# ---- Company Holiday Management ----

async def list_company_holidays(db: AsyncSession, current_user: dict) -> List[dict]:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    holidays = await calendar_events_repo.list_holidays(db, parse_uuid(current_user["company_id"]))
    return [event_base_dict(h) for h in holidays]


async def deactivate_company_holiday(db: AsyncSession, current_user: dict, event_id: str) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    parsed_id = parse_uuid(event_id)
    event = await calendar_events_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.category != "company_holiday":
        raise HTTPException(status_code=400, detail="Only company holidays can be deactivated")
    event.is_active = False
    event.updated_at = datetime.now(timezone.utc)
    event.updated_by = parse_uuid(current_user["id"])
    event.updated_by_name = current_user.get("name")
    await write_calendar_activity(db, event.company_id, event.id, current_user, "holiday_deactivated", event.title)
    return {"message": "Holiday deactivated", "is_active": False}


async def reactivate_company_holiday(db: AsyncSession, current_user: dict, event_id: str) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    parsed_id = parse_uuid(event_id)
    event = await calendar_events_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.category != "company_holiday":
        raise HTTPException(status_code=400, detail="Only company holidays can be reactivated")
    event.is_active = True
    event.updated_at = datetime.now(timezone.utc)
    event.updated_by = parse_uuid(current_user["id"])
    event.updated_by_name = current_user.get("name")
    await write_calendar_activity(db, event.company_id, event.id, current_user, "holiday_reactivated", event.title)
    return {"message": "Holiday reactivated", "is_active": True}


def next_date_for_weekday(target_weekday_sunday0: int, from_date=None) -> str:
    anchor = from_date or datetime.now(timezone.utc).date()
    anchor_sunday0 = (anchor.weekday() + 1) % 7
    days_ahead = (target_weekday_sunday0 - anchor_sunday0) % 7
    return (anchor + timedelta(days=days_ahead)).isoformat()


async def create_weekly_holiday_pattern(db: AsyncSession, current_user: dict, data) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    weekdays = sorted(set(data.weekdays))
    if not weekdays or any(wd < 0 or wd > 6 for wd in weekdays):
        raise HTTPException(status_code=400, detail="weekdays must be 0-6 (0=Sunday..6=Saturday)")

    created = []
    for wd in weekdays:
        start_date = next_date_for_weekday(wd)
        event_data = SimpleNamespace(
            title=data.title, description=data.description, category="company_holiday", custom_color=None,
            priority="normal", start_date=start_date, start_time=None, end_date=start_date, end_time=None,
            all_day=True, location_type=None, location=None, online_link=None, visibility="company",
            recipient_type="company", recipient_ids=[], recurrence_type="weekly", recurrence_interval=1,
            recurrence_end_type="never", recurrence_end_value=None, override_conflicts=True,
        )
        created.append(await create_calendar_event(db, current_user, event_data))
    return {"message": "Weekly holiday pattern created", "created": created}
