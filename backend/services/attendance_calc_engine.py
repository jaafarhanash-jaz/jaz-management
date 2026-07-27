from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Pure attendance calculation engine - zero DB access, zero HTTP concerns.
# Every function here takes plain values in and returns plain values out, so
# it can be unit-tested directly and reused unmodified by check-in/check-out,
# dashboards, daily/weekly/monthly/yearly rollups, owner reports, and (per
# the "designed for future payroll integration" requirement) whatever
# payroll calculation eventually consumes the same numbers.
#
# Timezone: every comparison here is done in whatever timezone the caller's
# datetimes are already in. This backend has no company-timezone field
# anywhere in its schema, and the attendance check-in path this replaces
# already compared raw UTC hours against a literal cutoff - callers pass
# UTC-aware datetimes today, and this module makes no attempt to convert
# them. Adding real per-company timezone support is a separate, materially
# larger effort and is out of scope here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleSnapshot:
    """The subset of a WorkSchedule needed to calculate one attendance day.

    Captured once (at check-in) and passed around as a plain value from then
    on, rather than re-reading the live WorkSchedule row - see the
    "denormalized snapshot" comment on the Attendance model for why: editing
    or deleting a schedule later must never rewrite the calculated history
    of a past attendance record.
    """

    schedule_id: Optional[str]
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"
    break_minutes: float
    required_minutes: float


def _minutes_since_midnight(value: datetime) -> float:
    return value.hour * 60 + value.minute + value.second / 60


def _parse_hhmm_to_minutes(hhmm: str) -> float:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def calculate_check_in(schedule: Optional[ScheduleSnapshot], check_in_time: datetime) -> dict:
    """Returns {status, late_minutes, early_arrival_minutes}.

    With no resolvable schedule (employee unassigned AND the company has no
    default schedule configured yet), falls back to the pre-existing
    behaviour of this codebase: status "present", no lateness/earliness
    figure - zero behaviour change for a company that hasn't adopted work
    schedules yet.
    """
    if schedule is None:
        return {"status": "present", "late_minutes": None, "early_arrival_minutes": None}

    check_in_minutes = _minutes_since_midnight(check_in_time)
    scheduled_start_minutes = _parse_hhmm_to_minutes(schedule.start_time)
    delta = check_in_minutes - scheduled_start_minutes

    if delta > 0:
        return {"status": "late", "late_minutes": round(delta, 2), "early_arrival_minutes": None}
    if delta < 0:
        return {"status": "present", "late_minutes": None, "early_arrival_minutes": round(-delta, 2)}
    return {"status": "present", "late_minutes": None, "early_arrival_minutes": None}


def get_live_worked_minutes(check_in_time: datetime, now: datetime) -> float:
    """Worked-so-far minutes for a shift still in progress (no check-out yet).

    Used only for live "worked today" display (e.g. an employee dashboard
    widget while still checked in) - never persisted, and never used to
    derive overtime/missing/early-leave, which must only ever be computed
    once a real check-out timestamp exists (see calculate_check_out).
    """
    return round((now - check_in_time).total_seconds() / 60, 2)


def calculate_check_out(
    schedule: Optional[ScheduleSnapshot], check_in_time: datetime, check_out_time: datetime
) -> dict:
    """Returns {worked_minutes, net_minutes, overtime_minutes, missing_minutes, early_leave_minutes}.

    worked_minutes is always computed (raw check-out minus check-in, matching
    this codebase's pre-existing `working_duration_minutes`). Every
    schedule-derived figure stays None when there is no resolvable schedule,
    for the same zero-behaviour-change reason as calculate_check_in.
    """
    worked_minutes = round((check_out_time - check_in_time).total_seconds() / 60, 2)

    if schedule is None:
        return {
            "worked_minutes": worked_minutes,
            "net_minutes": None,
            "overtime_minutes": None,
            "missing_minutes": None,
            "early_leave_minutes": None,
        }

    net_minutes = round(max(worked_minutes - schedule.break_minutes, 0), 2)

    check_out_minutes = _minutes_since_midnight(check_out_time)
    scheduled_end_minutes = _parse_hhmm_to_minutes(schedule.end_time)
    early_leave_minutes = (
        round(scheduled_end_minutes - check_out_minutes, 2) if check_out_minutes < scheduled_end_minutes else None
    )

    surplus = net_minutes - schedule.required_minutes
    overtime_minutes = round(surplus, 2) if surplus > 0 else None
    missing_minutes = round(-surplus, 2) if surplus < 0 else None

    return {
        "worked_minutes": worked_minutes,
        "net_minutes": net_minutes,
        "overtime_minutes": overtime_minutes,
        "missing_minutes": missing_minutes,
        "early_leave_minutes": early_leave_minutes,
    }


@dataclass(frozen=True)
class AggregatedStats:
    """A rollup over any set of attendance records - one day, one week, one
    month, one year, or an arbitrary custom range. The same shape powers the
    employee dashboard's "today", the owner dashboard's daily summary, and
    the Phase 5/6 monthly/yearly/report rollups - callers just vary which
    records they pass in.
    """

    days_present: int
    days_late: int
    days_absent: int
    total_worked_minutes: float
    total_required_minutes: float
    total_overtime_minutes: float
    total_missing_minutes: float
    total_late_minutes: float
    total_early_leave_minutes: float
    late_count: int
    early_leave_count: int
    average_daily_worked_minutes: float
    attendance_percentage: float


def aggregate(records: Iterable[dict], *, expected_days: Optional[int] = None) -> AggregatedStats:
    """`records` is an iterable of plain dicts, each shaped like a subset of
    the Attendance row's calculated fields: {status, worked_minutes (or
    working_duration_minutes), required_minutes, overtime_minutes,
    missing_minutes, late_minutes, early_leave_minutes}. Missing/None values
    are treated as zero contributions so pre-migration or no-schedule
    records don't break a rollup - they just contribute nothing to the
    schedule-derived totals.

    `expected_days` (e.g. the number of calendar days in the requested
    range) is used for attendance_percentage; when omitted, it falls back
    to the number of records passed in (i.e. "percentage of days that have
    an attendance record at all were present").
    """
    records = list(records)
    days_present = sum(1 for r in records if r.get("status") == "present")
    days_late = sum(1 for r in records if r.get("status") == "late")
    days_absent = sum(1 for r in records if r.get("status") == "absent")

    def _sum(key: str, fallback_key: Optional[str] = None) -> float:
        total = 0.0
        for r in records:
            value = r.get(key)
            if value is None and fallback_key:
                value = r.get(fallback_key)
            total += value or 0
        return round(total, 2)

    total_worked_minutes = _sum("worked_minutes", "working_duration_minutes")
    total_required_minutes = _sum("required_minutes")
    total_overtime_minutes = _sum("overtime_minutes")
    total_missing_minutes = _sum("missing_minutes")
    total_late_minutes = _sum("late_minutes")
    total_early_leave_minutes = _sum("early_leave_minutes")
    late_count = sum(1 for r in records if r.get("late_minutes"))
    early_leave_count = sum(1 for r in records if r.get("early_leave_minutes"))

    worked_day_count = sum(1 for r in records if (r.get("worked_minutes") or r.get("working_duration_minutes")))
    average_daily_worked_minutes = round(total_worked_minutes / worked_day_count, 2) if worked_day_count else 0.0

    denominator = expected_days if expected_days is not None else len(records)
    attendance_percentage = round((days_present + days_late) / denominator * 100, 2) if denominator else 0.0

    return AggregatedStats(
        days_present=days_present,
        days_late=days_late,
        days_absent=days_absent,
        total_worked_minutes=total_worked_minutes,
        total_required_minutes=total_required_minutes,
        total_overtime_minutes=total_overtime_minutes,
        total_missing_minutes=total_missing_minutes,
        total_late_minutes=total_late_minutes,
        total_early_leave_minutes=total_early_leave_minutes,
        late_count=late_count,
        early_leave_count=early_leave_count,
        average_daily_worked_minutes=average_daily_worked_minutes,
        attendance_percentage=attendance_percentage,
    )
