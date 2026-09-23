from datetime import date as date_type, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.leaves as leaves_repo
import services.holidays as holidays_service
from services.attendance_calc_engine import compute_shift_end_datetime

# ---------------------------------------------------------------------------
# Leave <-> Attendance integration layer. Leave is a fully independent
# domain (models.Leave) - this module NEVER writes to Attendance, and never
# introduces Attendance.status = "leave". Every function here only reads
# approved Leave rows and either (a) mutates a response dict in place
# (mirroring services/holidays.py's annotate_holiday_dates - read-time
# annotation, not a stored-field mutation) or (b) returns a plain count/set
# consumed by services/attendance.py and services/dashboard.py's existing
# expected-attendance/absence calculations.
#
# Only APPROVED leave ever appears here - pending and rejected leave have
# zero effect on any of these functions by construction (every repository
# query below filters status='approved').
# ---------------------------------------------------------------------------


def _parse_date(value) -> date_type:
    return date_type.fromisoformat(value) if isinstance(value, str) else value


def combine_date_and_time(shift_date: date_type, start_time: str) -> datetime:
    hour, minute = (int(p) for p in start_time.split(":"))
    return datetime(shift_date.year, shift_date.month, shift_date.day, hour, minute, tzinfo=timezone.utc)


def _overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds() / 60


def _iter_dates(start: date_type, end: date_type) -> Iterable[date_type]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


async def get_leave_overlap_minutes(
    db: AsyncSession, employee_id, shift_date: date_type, scheduled_start_time: str, scheduled_end_time: str,
) -> float:
    """Sum of overlap (minutes) between this shift's SCHEDULED window
    (never the actual check-in/check-out clock times - see
    annotate_leave_dates) and every APPROVED leave whose shift-owning date
    equals shift_date. Zero when there is no approved leave for that date.
    This is what an employee's mid-shift approved leave actually reduces:
    their scheduled obligation for the day, not their raw worked span -
    Rule/decision: mid-shift leave never requires a second check-in/out,
    it only removes attendance obligation for the covered interval."""
    leaves = await leaves_repo.list_approved_for_employee_on_date(db, employee_id, shift_date)
    if not leaves:
        return 0.0
    shift_start_dt = combine_date_and_time(shift_date, scheduled_start_time)
    shift_end_dt = compute_shift_end_datetime(shift_date, scheduled_start_time, scheduled_end_time)
    return round(sum(_overlap_minutes(shift_start_dt, shift_end_dt, lv.start_at, lv.end_at) for lv in leaves), 2)


async def annotate_leave_dates(db: AsyncSession, records: List[dict]) -> None:
    """Mutates attendance response dicts in place - adds leave_intervals
    (every APPROVED leave overlapping that record's shift date),
    leave_covered_minutes (overlap against the SCHEDULED shift window),
    and, when the record has a resolvable required_minutes,
    effective_required_minutes/effective_missing_minutes/
    effective_overtime_minutes recomputed against the leave-reduced
    requirement. Never touches the underlying stored Attendance row - the
    real missing_minutes/status columns are untouched, exactly like
    holidays.annotate_holiday_dates never touches stored status. One
    leave lookup per distinct (employee_id, date) pair in the batch."""
    keys = {(r["employee_id"], r["date"]) for r in records if r.get("date") and r.get("employee_id")}
    leaves_by_key: Dict[Tuple[str, str], list] = {}
    for employee_id, date_str in keys:
        rows = await leaves_repo.list_approved_for_employee_on_date(db, employee_id, _parse_date(date_str))
        if rows:
            leaves_by_key[(employee_id, date_str)] = rows

    for r in records:
        leaves = leaves_by_key.get((r.get("employee_id"), r.get("date")), [])
        r["leave_intervals"] = [
            {
                "id": str(lv.id),
                "duration_type": lv.duration_type,
                "start_at": lv.start_at.isoformat(),
                "end_at": lv.end_at.isoformat(),
                "reason": lv.reason,
            }
            for lv in leaves
        ]
        if not leaves:
            r["leave_covered_minutes"] = 0.0
            continue

        scheduled_start = r.get("scheduled_start_time")
        scheduled_end = r.get("scheduled_end_time")
        required = r.get("required_minutes")
        if not scheduled_start or not scheduled_end or required is None:
            # No resolvable schedule snapshot on this record (pre-feature
            # data, or an ungrouped/no-schedule company) - nothing to
            # recompute against, same "zero behaviour change" fallback the
            # calc engine itself uses.
            r["leave_covered_minutes"] = 0.0
            continue

        shift_date = _parse_date(r["date"])
        shift_start_dt = combine_date_and_time(shift_date, scheduled_start)
        shift_end_dt = compute_shift_end_datetime(shift_date, scheduled_start, scheduled_end)
        covered = round(sum(_overlap_minutes(shift_start_dt, shift_end_dt, lv.start_at, lv.end_at) for lv in leaves), 2)
        r["leave_covered_minutes"] = covered
        if covered <= 0:
            continue

        effective_required = max(0.0, required - covered)
        r["effective_required_minutes"] = round(effective_required, 2)
        net = r.get("net_minutes")
        if net is not None:
            surplus = net - effective_required
            r["effective_missing_minutes"] = round(-surplus, 2) if surplus < 0 else None
            r["effective_overtime_minutes"] = round(surplus, 2) if surplus > 0 else None


async def get_full_day_leave_employee_date_pairs(
    db: AsyncSession, company_id, employee_ids, date_from: date_type, date_to: date_type,
) -> Set[Tuple[str, str]]:
    """Every (employee_id, date-string) pair in [date_from, date_to]
    covered by an APPROVED full_day/multi-day leave for that specific
    employee. The shared source of truth behind both the attendance-row
    exclusion filter (services/dashboard.py) and the expected-attendance
    denominator fix (compute_non_working_employee_days below) - mirrors
    how holiday exemption is a single shared date set reused across
    dashboard.py's own call sites, just keyed per-employee instead of
    company-wide since leave (unlike a holiday) does not apply to
    everyone equally.

    time_based/remaining_of_day leave deliberately never appears here -
    those types only ever reduce per-shift obligation (see
    get_leave_overlap_minutes/annotate_leave_dates above), they never make
    a whole day exempt from being "expected"."""
    if not employee_ids:
        return set()
    rows = await leaves_repo.list_approved_full_day_in_range(db, company_id, list(employee_ids), date_from, date_to)
    pairs: Set[Tuple[str, str]] = set()
    for leave in rows:
        start = max(_parse_date(leave.start_date), date_from)
        end = min(_parse_date(leave.end_date), date_to)
        for d in _iter_dates(start, end):
            pairs.add((str(leave.employee_id), d.isoformat()))
    return pairs


async def compute_non_working_employee_days(
    db: AsyncSession, company_id, employee_ids, date_from: date_type, date_to: date_type,
    *, working_hours: Optional[dict] = None,
) -> int:
    """Total count of (employee, date) slots in [date_from, date_to] that
    should be excluded from 'expected attendance' - either a company
    holiday/weekly-off (applies to every employee - existing
    services/holidays.py behaviour, reused unchanged) or an APPROVED
    full_day leave specific to that employee. A given employee-day is
    counted at most once even when both apply.

    This is the single calculation layer where Holiday and Leave exclusion
    are unified for expected-attendance purposes, per the approved design:
    previously services/attendance.py's get_attendance_analytics ignored
    both; services/dashboard.py's get_owner_dashboard already handled
    company-wide holidays (by zeroing absent_today entirely) but had no
    concept of per-employee Leave at all. Both call sites now go through
    this one function instead of each growing its own bespoke exclusion
    logic."""
    employee_ids = list(employee_ids)
    if not employee_ids or date_from > date_to:
        return 0

    if working_hours is None:
        working_hours = await companies_repo.get_working_hours(db, company_id)

    holiday_dates: Set[date_type] = set()
    for d in _iter_dates(date_from, date_to):
        if await holidays_service.get_day_off_info(db, company_id, d, working_hours=working_hours):
            holiday_dates.add(d)

    leave_pairs = await get_full_day_leave_employee_date_pairs(db, company_id, employee_ids, date_from, date_to)

    total = len(holiday_dates) * len(employee_ids)
    for _employee_id, date_str in leave_pairs:
        if _parse_date(date_str) not in holiday_dates:
            total += 1
    return total
