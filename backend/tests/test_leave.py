"""Live HTTP integration tests for Employee Leave (Feature A) - same style
as tests/test_surprise_attendance.py: real requests against a running
local backend, using the session-scoped admin/owner/employee fixtures
from conftest.py. Dates are chosen far in the future (2027+) and unique
per test to avoid colliding with other tests' attendance/leave state that
shares the same seeded demo accounts.
"""
import random
import uuid
from datetime import date, timedelta

import pytest

from tests.conftest import BASE_URL, auth_headers

# Randomized per test-process run (not per-test - offsets within a run must
# stay stable/unique relative to each other) so repeated runs against the
# same persistent local dev database never collide with a previous run's
# leftover approved leaves on the same fixed date (this suite creates real
# rows and does not delete them - there is no hard-delete endpoint to do so
# through the API, matching this codebase's soft-delete-only conventions
# elsewhere). A fixed anchor previously caused exactly this: a second test
# run's "single day" full_day create legitimately 409'd against the FIRST
# run's still-approved leave on the same literal date.
_BASE_DAY = date(2027, 1, 1) + timedelta(days=random.randint(0, 3000))


def _d(offset: int) -> str:
    """A unique, far-future ISO date per call site (offset), so tests
    never collide with each other's leave intervals."""
    return (_BASE_DAY + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def other_company_owner_headers(api_client, admin_token):
    """A second, distinct company/owner - tenant-isolation tests must never
    reach across this boundary. Same pattern as test_surprise_attendance.py."""
    admin_hdrs = auth_headers(admin_token)
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_LeavePlan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_hdrs,
    ).json()
    owner_email = f"TEST_leaveowner_{unique}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_LeaveOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Leave Owner", "owner_password": "pass1234", "owner_phone": f"057{unique[:7]}",
            "subscription_plan_id": plan["id"],
        },
        headers=admin_hdrs,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


@pytest.fixture(scope="module")
def employee2_headers(api_client, owner_token):
    """A second, disposable same-company employee, created fresh here
    rather than reusing the seeded employee2@demo.com account - that
    account's password predates the current seed script (seed_identity_data
    is idempotent on owner@demo.com already existing, so its employee2
    block - and whatever password it used - never actually re-ran in this
    environment); this avoids depending on that unknown/stale value.

    Module-scoped (built from the module/session-scoped owner_token, not
    the function-scoped owner_headers) and created exactly once for this
    whole file - every extra login this suite performs adds to the shared
    60/minute rate limit on /auth/login (services/auth.py), which the full
    test suite run already sits close to; the two tests that need a second
    employee reuse this one instance instead of each creating their own."""
    unique = uuid.uuid4().hex[:8]
    owner_hdrs = auth_headers(owner_token)
    created = api_client.post(
        f"{BASE_URL}/api/owner/employees",
        json={
            "email": f"TEST_leaveemp2_{unique}@example.com", "phone": f"058{unique[:7]}",
            "password": "pass1234", "name": "Test Second Employee", "role": "employee",
        },
        headers=owner_hdrs,
    )
    assert created.status_code == 200, created.text
    login = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email_or_phone": f"TEST_leaveemp2_{unique}@example.com", "password": "pass1234"},
    )
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


def _create(api_client, headers, **overrides):
    body = {"reason": "test reason"}
    body.update(overrides)
    return api_client.post(f"{BASE_URL}/api/employee/leaves", json=body, headers=headers)


class TestCreateTimeBasedLeave:
    def test_valid_time_based_leave_created_pending(self, api_client, employee_headers):
        d = _d(1)
        r = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="10:00", end_time="13:00")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["origin"] == "employee"
        assert body["duration_type"] == "time_based"
        assert body["start_date"] == body["end_date"] == d
        assert body["start_at"] == f"{d}T10:00:00+00:00"
        assert body["end_at"] == f"{d}T13:00:00+00:00"

    def test_missing_reason_rejected(self, api_client, employee_headers):
        r = api_client.post(
            f"{BASE_URL}/api/employee/leaves",
            json={"duration_type": "time_based", "date": _d(2), "start_time": "10:00", "end_time": "13:00", "reason": "  "},
            headers=employee_headers,
        )
        assert r.status_code == 400

    def test_missing_times_rejected(self, api_client, employee_headers):
        r = _create(api_client, employee_headers, duration_type="time_based", date=_d(3))
        assert r.status_code == 400

    def test_equal_start_end_time_rejected(self, api_client, employee_headers):
        r = _create(api_client, employee_headers, duration_type="time_based", date=_d(4), start_time="10:00", end_time="10:00")
        assert r.status_code == 400

    def test_midnight_crossing_interval_accepted(self, api_client, employee_headers):
        d = _d(5)
        r = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="23:00", end_time="01:00")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["start_date"] == body["end_date"] == d
        assert body["end_at"].startswith((date.fromisoformat(d) + timedelta(days=1)).isoformat())


class TestCreateFullDayLeave:
    def test_single_day(self, api_client, employee_headers):
        d = _d(10)
        r = _create(api_client, employee_headers, duration_type="full_day", start_date=d)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["start_date"] == body["end_date"] == d

    def test_multi_day(self, api_client, employee_headers):
        d1, d2 = _d(15), _d(18)
        r = _create(api_client, employee_headers, duration_type="full_day", start_date=d1, end_date=d2)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["start_date"] == d1
        assert body["end_date"] == d2

    def test_end_before_start_rejected(self, api_client, employee_headers):
        r = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(25), end_date=_d(20))
        assert r.status_code == 400

    def test_missing_start_date_rejected(self, api_client, employee_headers):
        r = _create(api_client, employee_headers, duration_type="full_day")
        assert r.status_code == 400


class TestInvalidDurationType:
    def test_unknown_duration_type_rejected(self, api_client, employee_headers):
        # duration_type is a Pydantic Literal["time_based","full_day",
        # "remaining_of_day"] - FastAPI/Pydantic reject an out-of-set value
        # at the schema layer with 422, before services/leaves.py's own
        # duration_type check (400) ever runs. Same as every other
        # malformed-body case in this codebase.
        r = _create(api_client, employee_headers, duration_type="annual", start_date=_d(30))
        assert r.status_code == 422


class TestOverlapValidation:
    def test_overlapping_approved_leave_rejected_for_new_request(self, api_client, owner_headers, employee_headers):
        d = _d(40)
        first = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="09:00", end_time="12:00").json()
        approved = api_client.post(f"{BASE_URL}/api/owner/leaves/{first['id']}/approve", headers=owner_headers)
        assert approved.status_code == 200, approved.text

        r = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="11:00", end_time="14:00")
        assert r.status_code == 409

    def test_adjacent_non_overlapping_leave_accepted(self, api_client, owner_headers, employee_headers):
        d = _d(41)
        first = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="08:00", end_time="10:00").json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{first['id']}/approve", headers=owner_headers)

        r = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="10:00", end_time="12:00")
        assert r.status_code == 200, r.text

    def test_overlapping_pending_requests_both_allowed(self, api_client, employee_headers):
        d = _d(42)
        r1 = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="09:00", end_time="12:00")
        r2 = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="10:00", end_time="13:00")
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

    def test_duplicate_identical_pending_requests_both_created(self, api_client, employee_headers):
        d = _d(43)
        body = dict(duration_type="time_based", date=d, start_time="09:00", end_time="10:00")
        r1 = _create(api_client, employee_headers, **body)
        r2 = _create(api_client, employee_headers, **body)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["id"] != r2.json()["id"]

    def test_approving_second_overlapping_pending_returns_409(self, api_client, owner_headers, employee_headers):
        d = _d(44)
        r1 = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="09:00", end_time="12:00").json()
        r2 = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="11:00", end_time="14:00").json()
        approve1 = api_client.post(f"{BASE_URL}/api/owner/leaves/{r1['id']}/approve", headers=owner_headers)
        assert approve1.status_code == 200, approve1.text
        approve2 = api_client.post(f"{BASE_URL}/api/owner/leaves/{r2['id']}/approve", headers=owner_headers)
        assert approve2.status_code == 409

    def test_revoked_leave_frees_the_slot_for_new_approval(self, api_client, owner_headers, employee_headers):
        d = _d(45)
        first = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="09:00", end_time="12:00").json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{first['id']}/approve", headers=owner_headers)
        revoke = api_client.post(
            f"{BASE_URL}/api/owner/leaves/{first['id']}/revoke", json={"cancellation_reason": "changed plans"}, headers=owner_headers,
        )
        assert revoke.status_code == 200, revoke.text

        second = _create(api_client, employee_headers, duration_type="time_based", date=d, start_time="09:00", end_time="12:00").json()
        approve_second = api_client.post(f"{BASE_URL}/api/owner/leaves/{second['id']}/approve", headers=owner_headers)
        assert approve_second.status_code == 200, approve_second.text


class TestAuthorizationAndOwnership:
    def test_employee_sees_own_leave(self, api_client, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(50)).json()
        r = api_client.get(f"{BASE_URL}/api/employee/leaves/{created['id']}", headers=employee_headers)
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_employee_lists_own_leaves(self, api_client, employee_headers):
        _create(api_client, employee_headers, duration_type="full_day", start_date=_d(51))
        r = api_client.get(f"{BASE_URL}/api/employee/leaves", headers=employee_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_employee_cannot_see_another_employees_leave(self, api_client, employee_headers, employee2_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(52)).json()
        r = api_client.get(f"{BASE_URL}/api/employee/leaves/{created['id']}", headers=employee2_headers)
        assert r.status_code == 404

    def test_employee_cannot_approve(self, api_client, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(53)).json()
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=employee_headers)
        assert r.status_code == 403

    def test_employee_cannot_reject(self, api_client, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(54)).json()
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/reject", json={}, headers=employee_headers)
        assert r.status_code == 403

    def test_employee_cannot_list_owner_leaves(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/leaves", headers=employee_headers)
        assert r.status_code == 403

    def test_owner_lists_company_leaves(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/leaves", headers=owner_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_owner_filters_by_status(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/leaves?status=pending", headers=owner_headers)
        assert r.status_code == 200
        assert all(item["status"] == "pending" for item in r.json())

    def test_owner_filters_by_employee(self, api_client, owner_headers, employee_headers, employee_user):
        _create(api_client, employee_headers, duration_type="full_day", start_date=_d(55))
        r = api_client.get(f"{BASE_URL}/api/owner/leaves?employee_id={employee_user['id']}", headers=owner_headers)
        assert r.status_code == 200
        assert all(item["employee_id"] == employee_user["id"] for item in r.json())

    def test_cross_company_leave_access_rejected(self, api_client, employee_headers, other_company_owner_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(56)).json()
        r = api_client.get(f"{BASE_URL}/api/owner/leaves/{created['id']}", headers=other_company_owner_headers)
        assert r.status_code == 404

    def test_cross_company_approve_rejected(self, api_client, employee_headers, other_company_owner_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(57)).json()
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=other_company_owner_headers)
        assert r.status_code == 404

    def test_owner_cannot_create_leave_for_employee_in_another_company(self, api_client, other_company_owner_headers, employee_user):
        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves",
            json={"duration_type": "full_day", "start_date": _d(58), "reason": "x", "employee_id": employee_user["id"]},
            headers=other_company_owner_headers,
        )
        assert r.status_code == 400


class TestOwnerCreatedLeave:
    def test_owner_created_leave_is_immediately_approved(self, api_client, owner_headers, employee_user):
        d = _d(60)
        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves",
            json={"duration_type": "full_day", "start_date": d, "reason": "granted by owner", "employee_id": employee_user["id"]},
            headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["origin"] == "owner"
        assert body["approved_by"] is not None
        assert body["approved_at"] is not None

    def test_owner_created_leave_conflicting_with_existing_approved_rejected(self, api_client, owner_headers, employee_user, employee_headers):
        d = _d(61)
        first = _create(api_client, employee_headers, duration_type="full_day", start_date=d).json()
        approve = api_client.post(f"{BASE_URL}/api/owner/leaves/{first['id']}/approve", headers=owner_headers)
        assert approve.status_code == 200

        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves",
            json={"duration_type": "full_day", "start_date": d, "reason": "conflict", "employee_id": employee_user["id"]},
            headers=owner_headers,
        )
        assert r.status_code == 409


class TestApprovalRejectionRevoke:
    def test_owner_rejects_with_reason(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(70)).json()
        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves/{created['id']}/reject",
            json={"rejection_reason": "too many people off that day"}, headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"
        assert r.json()["rejection_reason"] == "too many people off that day"

    def test_reject_without_reason_still_succeeds(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(71)).json()
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/reject", json={}, headers=owner_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_cannot_approve_already_rejected(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(72)).json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/reject", json={}, headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=owner_headers)
        assert r.status_code == 400

    def test_cannot_reject_already_approved(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(73)).json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/reject", json={}, headers=owner_headers)
        assert r.status_code == 400

    def test_owner_revokes_approved_leave_requires_reason(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(74)).json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/revoke", json={"cancellation_reason": ""}, headers=owner_headers)
        assert r.status_code == 400

    def test_owner_revokes_approved_leave(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(75)).json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves/{created['id']}/revoke",
            json={"cancellation_reason": "business need changed"}, headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

    def test_cannot_revoke_pending_leave(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(76)).json()
        r = api_client.post(
            f"{BASE_URL}/api/owner/leaves/{created['id']}/revoke", json={"cancellation_reason": "x"}, headers=owner_headers,
        )
        assert r.status_code == 400


class TestCancellation:
    def test_employee_cancels_own_pending_request(self, api_client, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(80)).json()
        r = api_client.post(f"{BASE_URL}/api/employee/leaves/{created['id']}/cancel", headers=employee_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_employee_cannot_cancel_approved_leave(self, api_client, owner_headers, employee_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(81)).json()
        api_client.post(f"{BASE_URL}/api/owner/leaves/{created['id']}/approve", headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/employee/leaves/{created['id']}/cancel", headers=employee_headers)
        assert r.status_code == 400

    def test_employee_cannot_cancel_another_employees_leave(self, api_client, employee_headers, employee2_headers):
        created = _create(api_client, employee_headers, duration_type="full_day", start_date=_d(82)).json()
        r = api_client.post(f"{BASE_URL}/api/employee/leaves/{created['id']}/cancel", headers=employee2_headers)
        assert r.status_code == 404


class TestAttendanceHistoryLeaveAnnotation:
    def test_owner_history_response_includes_leave_intervals_key(self, api_client, owner_headers):
        """Not asserting specific content (attendance rows for these
        far-future test dates don't exist) - just that the annotation
        wiring doesn't break the existing owner attendance-history
        endpoint and every returned row carries the new key."""
        r = api_client.get(f"{BASE_URL}/api/owner/attendance", headers=owner_headers)
        assert r.status_code == 200
        rows = r.json()
        if rows:
            assert "leave_intervals" in rows[0]
            assert "leave_covered_minutes" in rows[0]

    def test_employee_history_response_includes_leave_intervals_key(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers)
        assert r.status_code == 200
        rows = r.json()
        if rows:
            assert "leave_intervals" in rows[0]
