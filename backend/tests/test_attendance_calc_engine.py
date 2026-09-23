"""Pure unit tests for services/attendance_calc_engine.py.

Unlike the rest of tests/ (live HTTP integration tests against a running
backend), these test the calculation engine's plain functions directly -
no server, no DB, no fixtures beyond conftest's autouse session seed (which
still applies to every test collected under tests/, per its scope, but
these tests themselves make no network calls).
"""
from datetime import date, datetime, timezone

from services.attendance_calc_engine import (
    ScheduleSnapshot,
    aggregate,
    calculate_check_in,
    calculate_check_out,
    compute_forgotten_checkout_deadline,
    compute_shift_end_datetime,
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


class TestComputeShiftEndDatetime:
    """Part 17's core edge-case matrix: midnight must never automatically
    close a night shift - shift end is always derived from start/end
    time-of-day, never from the calendar date changing."""

    def test_normal_daytime_shift_8am_4pm(self):
        end = compute_shift_end_datetime(date(2026, 8, 31), "08:00", "16:00")
        assert end == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)

    def test_overnight_shift_5pm_2am_rolls_to_next_day(self):
        end = compute_shift_end_datetime(date(2026, 8, 31), "17:00", "02:00")
        assert end == datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)

    def test_overnight_shift_11pm_7am_rolls_to_next_day(self):
        end = compute_shift_end_datetime(date(2026, 8, 31), "23:00", "07:00")
        assert end == datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)

    def test_month_boundary_crossing(self):
        # Aug 31 -> Sep 1, not Aug 32 - exercises real calendar rollover.
        end = compute_shift_end_datetime(date(2026, 8, 31), "22:00", "06:00")
        assert end == datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)

    def test_year_boundary_crossing(self):
        end = compute_shift_end_datetime(date(2026, 12, 31), "22:00", "06:00")
        assert end == datetime(2027, 1, 1, 6, 0, tzinfo=timezone.utc)

    def test_degenerate_equal_start_end_treated_as_24h_overnight(self):
        end = compute_shift_end_datetime(date(2026, 8, 31), "09:00", "09:00")
        assert end == datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


class TestComputeForgottenCheckoutDeadline:
    def test_daytime_shift_deadline_is_end_plus_grace(self):
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "08:00", "16:00", 180)
        assert deadline == datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc)

    def test_overnight_shift_deadline_matches_the_worked_example(self):
        # Master spec's own example: 5PM-2AM shift, 3h grace -> forgotten
        # checkout threshold is 5AM the NEXT day, not 5AM the same day.
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "17:00", "02:00", 180)
        assert deadline == datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)

    def test_zero_grace_period_deadline_equals_shift_end(self):
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "08:00", "16:00", 0)
        assert deadline == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)

    def test_checkout_before_deadline_is_before_grace_expiry(self):
        # 5PM-2AM shift: checkout at 1:30AM (before shift end) is well
        # before the 5AM deadline.
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "17:00", "02:00", 180)
        checkout_at = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)
        assert checkout_at < deadline

    def test_checkout_within_grace_window_is_before_deadline(self):
        # Same shift: checkout at 4:30AM is late but still within the 3h
        # grace window (deadline 5AM) - must remain a valid checkout.
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "17:00", "02:00", 180)
        checkout_at = datetime(2026, 9, 1, 4, 30, tzinfo=timezone.utc)
        assert checkout_at < deadline

    def test_checkout_after_grace_window_is_past_deadline(self):
        # 5:01AM is one minute past the 5AM deadline.
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "17:00", "02:00", 180)
        checkout_at = datetime(2026, 9, 1, 5, 1, tzinfo=timezone.utc)
        assert checkout_at > deadline

    def test_normal_near_shift_start_check_in_is_unaffected_by_the_floor(self):
        # Checking in right at 5PM for a 5PM-2AM shift - the floor
        # (17:00 + 180min = 20:00) is far earlier than the schedule-
        # derived deadline (9-1 05:00), so the schedule deadline wins,
        # exactly matching the spec's own worked example.
        check_in_time = datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc)
        deadline = compute_forgotten_checkout_deadline(
            date(2026, 8, 31), "17:00", "02:00", 180, check_in_time=check_in_time,
        )
        assert deadline == datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)

    def test_very_late_check_in_still_gets_a_full_grace_period_from_check_in(self):
        # Checking in at 8PM against a 9AM-5PM schedule - the schedule's
        # own end-of-day+grace (17:00+180min=20:00) has ALREADY passed by
        # the moment of check-in. The session must not become eligible
        # for 'did_not_check_out' functionally the instant it's created -
        # the floor (check_in_time + grace) must win here instead.
        check_in_time = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)
        deadline = compute_forgotten_checkout_deadline(
            date(2026, 8, 31), "09:00", "17:00", 180, check_in_time=check_in_time,
        )
        assert deadline == datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
        assert deadline > check_in_time

    def test_no_check_in_time_given_uses_schedule_deadline_only(self):
        deadline = compute_forgotten_checkout_deadline(date(2026, 8, 31), "09:00", "17:00", 180)
        assert deadline == datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)


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
