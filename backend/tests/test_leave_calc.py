"""Pure unit tests for Leave's calculation logic - zero DB access, zero
HTTP concerns, mirroring test_attendance_calc_engine.py's own style. Fake
request-shaped objects (types.SimpleNamespace) stand in for the real
Pydantic LeaveCreate schema so this file never imports server.py (which
would pull in the whole FastAPI app / DB engine just to construct a
schema instance)."""
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException

import services.leave_attendance as leave_attendance
import services.leaves as leaves_service


def _data(**kwargs):
    defaults = {
        "duration_type": None, "reason": "test", "date": None, "start_time": None, "end_time": None,
        "start_date": None, "end_date": None, "group_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestCombineDateAndTime:
    def test_combines_date_and_hhmm_as_utc(self):
        dt = leave_attendance.combine_date_and_time(date(2026, 9, 10), "10:00")
        assert dt == datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)


class TestOverlapMinutes:
    def test_no_overlap_when_disjoint(self):
        a_start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
        a_end = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        b_start = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        b_end = datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc)
        assert leave_attendance._overlap_minutes(a_start, a_end, b_start, b_end) == 0.0

    def test_zero_overlap_when_touching_boundary(self):
        """Half-open [start, end) interval semantics (Rule/decision): a
        leave ending exactly when another begins does not overlap - this
        is what lets back-to-back leave requests coexist."""
        a_start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
        a_end = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        b_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        b_end = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        assert leave_attendance._overlap_minutes(a_start, a_end, b_start, b_end) == 0.0

    def test_partial_overlap_computed_correctly(self):
        a_start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
        a_end = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        b_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        b_end = datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc)
        assert leave_attendance._overlap_minutes(a_start, a_end, b_start, b_end) == 60.0

    def test_full_containment(self):
        a_start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
        a_end = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)
        b_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        b_end = datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc)
        assert leave_attendance._overlap_minutes(a_start, a_end, b_start, b_end) == 180.0


class TestResolveTimeBased:
    def test_simple_same_day_interval(self):
        start_date, end_date, start_at, end_at = leaves_service._resolve_time_based(
            _data(date="2026-09-10", start_time="10:00", end_time="13:00")
        )
        assert start_date == end_date == date(2026, 9, 10)
        assert start_at == datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        assert end_at == datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc)

    def test_midnight_crossing_interval_rolls_end_to_next_day(self):
        """An overnight-shift employee requesting leave for the last two
        hours of their shift (23:00 -> 01:00) - end_at's calendar date
        rolls forward, but start_date/end_date both stay pinned to the
        single shift date the leave belongs to (Rule 8)."""
        start_date, end_date, start_at, end_at = leaves_service._resolve_time_based(
            _data(date="2026-09-10", start_time="23:00", end_time="01:00")
        )
        assert start_date == end_date == date(2026, 9, 10)
        assert start_at == datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)
        assert end_at == datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)

    def test_missing_date_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._resolve_time_based(_data(start_time="10:00", end_time="13:00"))
        assert exc.value.status_code == 400

    def test_missing_times_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._resolve_time_based(_data(date="2026-09-10"))
        assert exc.value.status_code == 400

    def test_equal_start_and_end_time_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._resolve_time_based(_data(date="2026-09-10", start_time="10:00", end_time="10:00"))
        assert exc.value.status_code == 400


class TestResolveFullDay:
    def test_single_day_defaults_end_to_start(self):
        start_date, end_date, start_at, end_at = leaves_service._resolve_full_day(_data(start_date="2026-09-10"))
        assert start_date == end_date == date(2026, 9, 10)
        assert start_at == datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
        assert end_at == datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)

    def test_multi_day_range(self):
        start_date, end_date, start_at, end_at = leaves_service._resolve_full_day(
            _data(start_date="2026-09-05", end_date="2026-09-07")
        )
        assert start_date == date(2026, 9, 5)
        assert end_date == date(2026, 9, 7)
        assert start_at == datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
        assert end_at == datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)

    def test_end_before_start_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._resolve_full_day(_data(start_date="2026-09-10", end_date="2026-09-05"))
        assert exc.value.status_code == 400

    def test_missing_start_date_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._resolve_full_day(_data())
        assert exc.value.status_code == 400


class TestRequireReason:
    def test_empty_reason_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._require_reason("")
        assert exc.value.status_code == 400

    def test_whitespace_only_reason_rejected(self):
        with pytest.raises(HTTPException) as exc:
            leaves_service._require_reason("   ")
        assert exc.value.status_code == 400

    def test_valid_reason_stripped(self):
        assert leaves_service._require_reason("  need to see a doctor  ") == "need to see a doctor"
