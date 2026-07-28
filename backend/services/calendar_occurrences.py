from datetime import date as date_type, datetime, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.calendar_event_exceptions as exceptions_repo

# Safety bound on recurrence expansion regardless of how the rule is shaped -
# never a runaway loop, and consistent with "never load an entire year":
# expansion itself never generates more than this many candidate occurrences.
MAX_OCCURRENCE_ITERATIONS = 3660


def _date_str(value) -> str:
    return value.isoformat() if isinstance(value, date_type) else value


def combine_event_datetime(date_str, time_str, all_day: bool, is_end: bool) -> datetime:
    date_str = _date_str(date_str)
    if all_day or not time_str:
        time_str = "23:59:59" if is_end else "00:00:00"
    if len(time_str) == 5:
        time_str += ":00"
    return datetime.fromisoformat(f"{date_str}T{time_str}+00:00")


def step_occurrence(dt: datetime, recurrence_type: str, interval: int) -> datetime:
    interval = max(interval, 1)
    if recurrence_type in ("daily", "custom"):
        return dt + timedelta(days=interval)
    if recurrence_type == "weekly":
        return dt + timedelta(weeks=interval)
    if recurrence_type == "monthly":
        return dt + relativedelta(months=interval)
    if recurrence_type == "yearly":
        return dt + relativedelta(years=interval)
    return dt


def count_raw_occurrences_before(event, cutoff: datetime) -> int:
    """Counts scheduled recurrence SLOTS before cutoff, ignoring exceptions -
    used only for this_and_future's after_count budget split. A cancelled
    occurrence still consumed one of the original N slots; it must not free
    up budget for an extra occurrence the series was never meant to have."""
    base_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
    if event.recurrence_type == "none":
        return 1 if base_start < cutoff else 0
    cursor = base_start
    count = 0
    iterations = 0
    while cursor < cutoff and iterations < MAX_OCCURRENCE_ITERATIONS:
        iterations += 1
        count += 1
        cursor = step_occurrence(cursor, event.recurrence_type, event.recurrence_interval)
    return count


async def expand_event_occurrences(
    db: AsyncSession, event, range_start: datetime, range_end: datetime, *, exceptions=None,
) -> list:
    """Occurrences are computed on read for the requested window only -
    nothing is pre-materialized per-occurrence in storage. Applies
    calendar_event_exceptions (skip if cancelled, merge if overridden) so
    'This Event Only' edits/cancellations don't require touching the series.
    Returns a list of dicts merging the event's own fields with any
    exception's override_fields, plus occurrence_date/occurrence_start/
    occurrence_end/has_exception.

    `exceptions` lets a caller expanding many events at once (e.g. a
    dashboard widget over a whole week's candidate events) pass in this
    event's slice of a single batched query instead of every call issuing
    its own `list_for_event` round trip. Defaults to the original
    per-event fetch when not provided, so existing single-event callers are
    unaffected."""
    base_start = combine_event_datetime(event.start_date, event.start_time, event.all_day, False)
    base_end = combine_event_datetime(event.end_date, event.end_time, event.all_day, True)
    duration = base_end - base_start

    if exceptions is None:
        exceptions = await exceptions_repo.list_for_event(db, event.id)
    exceptions_by_date = {_date_str(e.occurrence_date): e for e in exceptions}

    occurrences = []
    if event.recurrence_type == "none":
        if base_start <= range_end and base_end >= range_start:
            occurrences.append({"occurrence_date": _date_str(event.start_date), "start": base_start, "end": base_end})
    else:
        cursor = base_start
        end_type = event.recurrence_end_type
        end_value = event.recurrence_end_value
        end_date_limit = datetime.fromisoformat(f"{end_value}T23:59:59+00:00") if end_type == "on_date" and end_value else None
        max_count = int(end_value) if end_type == "after_count" and end_value else None
        count = 0
        iterations = 0
        while cursor <= range_end and iterations < MAX_OCCURRENCE_ITERATIONS:
            iterations += 1
            count += 1
            if end_date_limit and cursor > end_date_limit:
                break
            if max_count and count > max_count:
                break
            occurrence_date = cursor.date().isoformat()
            occurrence_end = cursor + duration
            if cursor <= range_end and occurrence_end >= range_start:
                occurrences.append({"occurrence_date": occurrence_date, "start": cursor, "end": occurrence_end})
            cursor = step_occurrence(cursor, event.recurrence_type, event.recurrence_interval)

    event_fields = {
        "id": event.id, "reference_number": event.reference_number, "company_id": event.company_id,
        "series_id": event.series_id, "title": event.title, "description": event.description,
        "category": event.category, "default_color": event.default_color, "custom_color": event.custom_color,
        "priority": event.priority, "start_date": _date_str(event.start_date), "start_time": event.start_time,
        "end_date": _date_str(event.end_date), "end_time": event.end_time, "all_day": event.all_day,
        "location_type": event.location_type, "location": event.location, "online_link": event.online_link,
        "visibility": event.visibility, "recipient_type": event.recipient_type, "recipient_ids": event.recipient_ids,
        "recurrence_type": event.recurrence_type, "recurrence_interval": event.recurrence_interval,
        "recurrence_end_type": event.recurrence_end_type, "recurrence_end_value": event.recurrence_end_value,
        "status": event.status, "linked_thread_id": event.linked_thread_id,
        "is_active": event.is_active, "created_by": event.created_by, "created_by_name": event.created_by_name,
        "meeting_notes_summary": event.meeting_notes_summary, "meeting_notes_decisions": event.meeting_notes_decisions,
        "meeting_notes_action_items": event.meeting_notes_action_items,
    }

    result = []
    for occ in occurrences:
        exception = exceptions_by_date.get(occ["occurrence_date"])
        if exception and exception.is_cancelled:
            continue
        merged = {**event_fields, **((exception.override_fields or {}) if exception else {})}
        merged["occurrence_date"] = occ["occurrence_date"]
        merged["occurrence_start"] = occ["start"].isoformat()
        merged["occurrence_end"] = occ["end"].isoformat()
        merged["has_exception"] = exception is not None
        result.append(merged)
    return result
