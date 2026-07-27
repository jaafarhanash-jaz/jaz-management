"""Pure unit tests for services/attendance_calc_engine.py.

Unlike the rest of tests/ (live HTTP integration tests against a running
backend), these test the calculation engine's plain functions directly -
no server, no DB, no fixtures beyond conftest's autouse session seed (which
still applies to every test collected under tests/, per its scope, but
these tests themselves make no network calls).
"""
from datetime import datetime, timezone

from services.attendance_calc_engine import (
    ScheduleSnapshot,
    aggregate,
    calculate_check_in,
    calculate_check_out,
    get_live_worked_minutes,
)

SCHEDULE = ScheduleSnapshot(
    schedule_id="s1", start_time="09:00", end_time="17:00", break_minutes=60, required_minutes=8 * 60
)


def _dt(hour, minute):
    return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc)


class TestCalculateCheckIn:
    def test_on_time(self):
        r = calculate_check_in(SCHEDULE, _dt(9, 0))
        assert r == {"status": "present", "late_minutes": None, "early_arrival_minutes": None}

    def test_late(self):
        r = calculate_check_in(SCHEDULE, _dt(9, 15))
        assert r["status"] == "late"
        assert r["late_minutes"] == 15.0
        assert r["early_arrival_minutes"] is None

    def test_early_arrival(self):
        r = calculate_check_in(SCHEDULE, _dt(8, 45))
        assert r["status"] == "present"
        assert r["early_arrival_minutes"] == 15.0
        assert r["late_minutes"] is None

    def test_no_schedule_falls_back_to_present(self):
        r = calculate_check_in(None, _dt(9, 15))
        assert r == {"status": "present", "late_minutes": None, "early_arrival_minutes": None}


class TestCalculateCheckOut:
    def test_exact_shift_reports_missing_hours_for_the_unpaid_break(self):
        # 9:00-17:00 worked = 480min raw, minus a 60min scheduled break = 420min net,
        # against an 8h (480min) requirement -> 60min missing.
        r = calculate_check_out(SCHEDULE, _dt(9, 0), _dt(17, 0))
        assert r["worked_minutes"] == 480.0
        assert r["net_minutes"] == 420.0
        assert r["missing_minutes"] == 60.0
        assert r["overtime_minutes"] is None
        assert r["early_leave_minutes"] is None

    def test_overtime(self):
        r = calculate_check_out(SCHEDULE, _dt(9, 0), _dt(18, 30))
        assert r["overtime_minutes"] == 30.0
        assert r["missing_minutes"] is None

    def test_early_leave(self):
        r = calculate_check_out(SCHEDULE, _dt(9, 0), _dt(16, 30))
        assert r["early_leave_minutes"] == 30.0

    def test_no_schedule_only_worked_minutes_populated(self):
        r = calculate_check_out(None, _dt(9, 0), _dt(17, 0))
        assert r == {
            "worked_minutes": 480.0,
            "net_minutes": None,
            "overtime_minutes": None,
            "missing_minutes": None,
            "early_leave_minutes": None,
        }


class TestGetLiveWorkedMinutes:
    def test_still_checked_in(self):
        assert get_live_worked_minutes(_dt(9, 0), _dt(12, 0)) == 180.0


class TestAggregate:
    def test_counts_and_percentage(self):
        records = [
            {"status": "present", "worked_minutes": 480, "required_minutes": 480},
            {
                "status": "late",
                "worked_minutes": 450,
                "required_minutes": 480,
                "missing_minutes": 30,
                "late_minutes": 15,
            },
            {"status": "absent"},
        ]
        agg = aggregate(records, expected_days=3)
        assert (agg.days_present, agg.days_late, agg.days_absent) == (1, 1, 1)
        assert agg.total_late_minutes == 15.0
        assert agg.attendance_percentage == round(2 / 3 * 100, 2)

    def test_empty_records(self):
        agg = aggregate([])
        assert agg.days_present == 0
        assert agg.attendance_percentage == 0.0

    def test_none_values_contribute_zero(self):
        records = [{"status": "present", "worked_minutes": 480, "overtime_minutes": None, "missing_minutes": None}]
        agg = aggregate(records, expected_days=1)
        assert agg.total_overtime_minutes == 0.0
        assert agg.total_missing_minutes == 0.0
