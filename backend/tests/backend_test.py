"""JAZ Platform backend tests - Phase 2 CRUD, Employee flows, Stripe integration & RBAC."""
import os
import time
import uuid
import requests
from datetime import datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://jaz-management.preview.emergentagent.com").rstrip("/")


# ============ AUTH ============
class TestAuth:
    def test_login_admin(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": "admin@jaz.com", "password": "admin123"})
        assert r.status_code == 200
        data = r.json()
        assert data["role"] == "super_admin"
        assert data["token"]

    def test_login_owner(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": "owner@demo.com", "password": "owner123"})
        assert r.status_code == 200
        assert r.json()["role"] == "company_owner"

    def test_login_employee(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": "employee1@demo.com", "password": "emp123"})
        assert r.status_code == 200
        assert r.json()["role"] == "employee"

    def test_login_invalid(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": "wrong@x.com", "password": "bad"})
        assert r.status_code == 401


# ============ OWNER: EMPLOYEE CRUD ============
class TestOwnerEmployeeCRUD:
    _created_id = None

    def test_create_employee(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        payload = {
            "email": f"TEST_emp_{unique}@example.com",
            "phone": f"055{unique[:7]}",
            "password": "testpass123",
            "name": "TEST_Employee",
            "role": "employee",
            "department": "IT",
            "position": "Developer"
        }
        r = api_client.post(f"{BASE_URL}/api/owner/employees", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["email"] == payload["email"]
        assert data["name"] == "TEST_Employee"
        assert data["role"] == "employee"
        assert "id" in data
        TestOwnerEmployeeCRUD._created_id = data["id"]

    def test_get_employees_persisted(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/employees", headers=owner_headers)
        assert r.status_code == 200
        ids = [e["id"] for e in r.json()]
        assert TestOwnerEmployeeCRUD._created_id in ids

    def test_update_employee(self, api_client, owner_headers):
        emp_id = TestOwnerEmployeeCRUD._created_id
        r = api_client.put(f"{BASE_URL}/api/owner/employees/{emp_id}", json={"position": "Senior Developer"}, headers=owner_headers)
        assert r.status_code == 200
        # verify persistence
        r2 = api_client.get(f"{BASE_URL}/api/owner/employees", headers=owner_headers)
        emp = next((e for e in r2.json() if e["id"] == emp_id), None)
        assert emp is not None
        assert emp["position"] == "Senior Developer"

    def test_delete_employee(self, api_client, owner_headers):
        emp_id = TestOwnerEmployeeCRUD._created_id
        r = api_client.delete(f"{BASE_URL}/api/owner/employees/{emp_id}", headers=owner_headers)
        assert r.status_code == 200
        r2 = api_client.get(f"{BASE_URL}/api/owner/employees", headers=owner_headers)
        ids = [e["id"] for e in r2.json()]
        assert emp_id not in ids


# ============ OWNER: TASK CRUD ============
class TestOwnerTaskCRUD:
    _created_id = None

    def test_create_task(self, api_client, owner_headers, employee_user):
        payload = {
            "title": "TEST_Task",
            "description": "test task description",
            "priority": "high",
            "assigned_to": employee_user["id"],
            "due_date": "2026-12-31",
            "requires_proof": True
        }
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["title"] == "TEST_Task"
        assert data["priority"] == "high"
        assert data["requires_proof"] is True
        assert data["status"] == "new"
        assert data["assigned_to"] == employee_user["id"]
        TestOwnerTaskCRUD._created_id = data["id"]

    def test_update_task(self, api_client, owner_headers):
        tid = TestOwnerTaskCRUD._created_id
        r = api_client.put(f"{BASE_URL}/api/owner/tasks/{tid}", json={"priority": "low"}, headers=owner_headers)
        assert r.status_code == 200
        r2 = api_client.get(f"{BASE_URL}/api/owner/tasks", headers=owner_headers)
        t = next((x for x in r2.json() if x["id"] == tid), None)
        assert t and t["priority"] == "low"

    def test_delete_task(self, api_client, owner_headers):
        tid = TestOwnerTaskCRUD._created_id
        r = api_client.delete(f"{BASE_URL}/api/owner/tasks/{tid}", headers=owner_headers)
        assert r.status_code == 200


# ============ OWNER: DEPARTMENT ============
class TestOwnerDepartment:
    def test_create_department(self, api_client, owner_headers):
        payload = {"name": f"TEST_Dept_{uuid.uuid4().hex[:6]}"}
        r = api_client.post(f"{BASE_URL}/api/owner/departments", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == payload["name"]
        assert "id" in data

    def test_get_departments(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/departments", headers=owner_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ============ EMPLOYEE: TASK STATUS & PROOF ============
class TestEmployeeTasks:
    _tid = None

    def test_setup_task(self, api_client, owner_headers, employee_user):
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json={
            "title": "TEST_EmpTask",
            "description": "for employee test",
            "priority": "medium",
            "assigned_to": employee_user["id"],
            "due_date": "2026-12-31",
            "requires_proof": True
        }, headers=owner_headers)
        assert r.status_code == 200
        TestEmployeeTasks._tid = r.json()["id"]

    def test_update_status(self, api_client, employee_headers):
        tid = TestEmployeeTasks._tid
        r = api_client.put(f"{BASE_URL}/api/employee/tasks/{tid}/status",
                            json={"status": "in_progress"}, headers=employee_headers)
        assert r.status_code == 200
        # verify
        r2 = api_client.get(f"{BASE_URL}/api/employee/tasks", headers=employee_headers)
        t = next((x for x in r2.json() if x["id"] == tid), None)
        assert t and t["status"] == "in_progress"

    def test_upload_proof(self, api_client, employee_headers):
        tid = TestEmployeeTasks._tid
        proof_data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEX///+nxBvIAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        r = api_client.post(f"{BASE_URL}/api/employee/tasks/{tid}/proof",
                             json={"file_url": proof_data}, headers=employee_headers)
        assert r.status_code == 200
        r2 = api_client.get(f"{BASE_URL}/api/employee/tasks", headers=employee_headers)
        t = next((x for x in r2.json() if x["id"] == tid), None)
        assert t and len(t.get("proof_files", [])) >= 1

    def test_cleanup(self, api_client, owner_headers):
        tid = TestEmployeeTasks._tid
        api_client.delete(f"{BASE_URL}/api/owner/tasks/{tid}", headers=owner_headers)


# ============ EMPLOYEE ATTENDANCE ============
class TestAttendance:
    def test_check_in_invalid_qr(self, api_client, employee_headers):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                             json={"qr_code": "wrong-qr", "latitude": 24.7, "longitude": 46.6},
                             headers=employee_headers)
        assert r.status_code == 400

    def test_check_in_valid_qr(self, api_client, owner_headers, employee_headers):
        # The QR now encodes an opaque per-company token (never company data);
        # the owner-scoped settings endpoint exposes it for testing.
        settings = api_client.get(f"{BASE_URL}/api/owner/attendance/settings", headers=owner_headers).json()
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                             json={"qr_code": settings["qr_token"], "latitude": 24.7, "longitude": 46.6},
                             headers=employee_headers)
        # Either succeeded now, or already checked in earlier this session
        assert r.status_code in (200, 400)
        if r.status_code == 200:
            assert "status" in r.json()

    def test_check_out(self, api_client, owner_headers, employee_headers):
        # Check-out now requires scanning the QR too, exactly like check-in.
        settings = api_client.get(f"{BASE_URL}/api/owner/attendance/settings", headers=owner_headers).json()
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                             json={"qr_code": settings["qr_token"], "latitude": 24.7, "longitude": 46.6},
                             headers=employee_headers)
        # Might be 200 (success), or 400 (already checked out) - both acceptable after check-in flow
        assert r.status_code in (200, 400)

    def test_history(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_history_includes_work_schedule_calculation_fields(self, api_client, employee_headers):
        # Confirms the response *shape* is wired end-to-end (schedule
        # resolution -> calc engine -> persisted columns -> API response).
        # The calculation *values* themselves (late/overtime/missing/etc.
        # arithmetic) are covered deterministically by
        # tests/test_attendance_calc_engine.py's pure unit tests - this
        # test can't control wall-clock check-in/out time the way those
        # can, so it only asserts the new fields are present with the
        # right types, not specific numbers.
        r = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers)
        assert r.status_code == 200
        records = r.json()
        if not records:
            return
        record = records[0]
        for field in (
            "required_minutes", "scheduled_break_minutes", "net_minutes",
            "overtime_minutes", "missing_minutes", "late_minutes",
            "early_arrival_minutes", "early_leave_minutes",
        ):
            assert field in record
            assert record[field] is None or isinstance(record[field], (int, float))


# ============ WORK SCHEDULES ============
class TestWorkSchedules:
    _schedule_id = None

    def test_create_schedule(self, api_client, owner_headers):
        payload = {
            "name": f"TEST_Schedule_{uuid.uuid4().hex[:6]}",
            "start_time": "08:00", "end_time": "16:00",
            "break_start_time": "12:00", "break_end_time": "12:30",
            "required_hours": 7.5, "working_days": [0, 1, 2, 3, 4],
            "is_default": False,
        }
        r = api_client.post(f"{BASE_URL}/api/owner/work-schedules", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == payload["name"]
        assert data["is_active"] is True
        TestWorkSchedules._schedule_id = data["id"]

    def test_list_schedules_includes_created(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/work-schedules", headers=owner_headers)
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert TestWorkSchedules._schedule_id in ids

    def test_invalid_time_format_rejected(self, api_client, owner_headers):
        payload = {
            "name": "TEST_Bad", "start_time": "8am", "end_time": "16:00",
            "required_hours": 8, "working_days": [0],
        }
        r = api_client.post(f"{BASE_URL}/api/owner/work-schedules", json=payload, headers=owner_headers)
        assert r.status_code == 400

    def test_empty_working_days_rejected(self, api_client, owner_headers):
        payload = {
            "name": "TEST_Bad2", "start_time": "08:00", "end_time": "16:00",
            "required_hours": 8, "working_days": [],
        }
        r = api_client.post(f"{BASE_URL}/api/owner/work-schedules", json=payload, headers=owner_headers)
        assert r.status_code == 400

    def test_employee_cannot_manage_schedules(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/work-schedules", headers=employee_headers)
        assert r.status_code == 403

    def test_assign_schedule_to_employee(self, api_client, owner_headers, employee_headers):
        me = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        r = api_client.put(
            f"{BASE_URL}/api/owner/employees/{me['id']}",
            json={"schedule_id": TestWorkSchedules._schedule_id}, headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        me_after = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        assert me_after["schedule_id"] == TestWorkSchedules._schedule_id

        # Unassign again so this test is repeatable and doesn't leave the
        # demo employee permanently pinned to a throwaway TEST_ schedule.
        api_client.put(f"{BASE_URL}/api/owner/employees/{me['id']}", json={"schedule_id": None}, headers=owner_headers)

    def test_assign_nonexistent_schedule_rejected(self, api_client, owner_headers, employee_headers):
        me = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        r = api_client.put(
            f"{BASE_URL}/api/owner/employees/{me['id']}",
            json={"schedule_id": "00000000-0000-0000-0000-000000000000"}, headers=owner_headers,
        )
        assert r.status_code == 404

    def test_cannot_deactivate_default_schedule(self, api_client, owner_headers):
        schedules = api_client.get(f"{BASE_URL}/api/owner/work-schedules", headers=owner_headers).json()
        default_schedule = next((s for s in schedules if s["is_default"]), None)
        if not default_schedule:
            return
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-schedules/{default_schedule['id']}/deactivate", headers=owner_headers,
        )
        assert r.status_code == 400

    def test_deactivate_and_reactivate_unassigned_schedule(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-schedules/{TestWorkSchedules._schedule_id}/deactivate",
            headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-schedules/{TestWorkSchedules._schedule_id}/reactivate",
            headers=owner_headers,
        )
        assert r.status_code == 200, r.text

    def test_employee_stats_all_ranges(self, api_client, owner_headers, employee_headers):
        me = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        for range_ in ("today", "week", "month", "year"):
            r = api_client.get(
                f"{BASE_URL}/api/owner/attendance/employees/{me['id']}/stats?range={range_}",
                headers=owner_headers,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["range"] == range_
            assert data["date_from"] <= data["date_to"]
            assert 0 <= data["attendance_percentage"] <= 100

    def test_employee_stats_custom_range_requires_dates(self, api_client, owner_headers, employee_headers):
        me = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        r = api_client.get(
            f"{BASE_URL}/api/owner/attendance/employees/{me['id']}/stats?range=custom", headers=owner_headers,
        )
        assert r.status_code == 400

    def test_employee_stats_invalid_range_rejected(self, api_client, owner_headers, employee_headers):
        me = api_client.get(f"{BASE_URL}/api/auth/me", headers=employee_headers).json()
        r = api_client.get(
            f"{BASE_URL}/api/owner/attendance/employees/{me['id']}/stats?range=bogus", headers=owner_headers,
        )
        assert r.status_code == 400


# ============ EMPLOYEE REPORTS ============
class TestReports:
    def test_create_report(self, api_client, employee_headers):
        b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEX///+nxBvIAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        payload = {
            "title": "TEST_Report",
            "description": "test description",
            "images": [b64],
            "files": [b64]
        }
        r = api_client.post(f"{BASE_URL}/api/employee/reports", json=payload, headers=employee_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["title"] == "TEST_Report"
        assert data["status"] == "pending"
        assert len(data["images"]) == 1
        assert len(data["files"]) == 1

    def test_owner_can_see_report(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/reports", headers=owner_headers)
        assert r.status_code == 200
        titles = [rep["title"] for rep in r.json()]
        assert "TEST_Report" in titles


# ============ EMPLOYEE PERFORMANCE ============
class TestPerformance:
    def test_get_performance(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/employee/performance", headers=employee_headers)
        assert r.status_code == 200
        data = r.json()
        for k in ["completion_rate", "attendance_rate", "performance_score"]:
            assert k in data
        assert isinstance(data["performance_score"], (int, float))


# ============ ADMIN: COMPANIES + PLANS ============
class TestAdmin:
    _cid = None
    _pid = None

    def test_create_subscription_plan(self, api_client, admin_headers):
        payload = {"name": f"TEST_Plan_{uuid.uuid4().hex[:6]}", "max_employees": 25, "price": 199.0, "duration_months": 3, "features": ["Feature A", "Feature B"]}
        r = api_client.post(f"{BASE_URL}/api/admin/subscription-plans", json=payload, headers=admin_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == payload["name"]
        assert data["price"] == 199.0
        assert data["is_active"] is True
        TestAdmin._pid = data["id"]

    def test_create_company(self, api_client, admin_headers):
        unique = uuid.uuid4().hex[:8]
        payload = {
            "name": f"TEST_Company_{unique}",
            "owner_email": f"TEST_own_{unique}@example.com",
            "owner_name": "TEST Owner",
            "owner_password": "pass1234",
            "owner_phone": f"055{unique[:7]}",
            "address": "Test Address",
            # Required since the Subscription Plans feature; the test predated
            # it and was never updated (pre-existing failure, fixed here).
            "subscription_plan_id": TestAdmin._pid
        }
        r = api_client.post(f"{BASE_URL}/api/admin/companies", json=payload, headers=admin_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == payload["name"]
        assert data["qr_code"].startswith("data:image/png;base64,")
        assert data["owner_id"]
        TestAdmin._cid = data["id"]

    def test_get_companies(self, api_client, admin_headers):
        r = api_client.get(f"{BASE_URL}/api/admin/companies", headers=admin_headers)
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()]
        assert TestAdmin._cid in ids

    def test_cleanup_admin(self, api_client, admin_headers):
        if TestAdmin._cid:
            api_client.delete(f"{BASE_URL}/api/admin/companies/{TestAdmin._cid}", headers=admin_headers)


# ============ OWNER SUBSCRIPTION PLANS ============
class TestOwnerSubscription:
    def test_get_plans(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/subscription-plans", headers=owner_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_get_subscription(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/subscription", headers=owner_headers)
        assert r.status_code == 200
        data = r.json()
        assert "subscription_status" in data
        assert "current_plan" in data


# ============ STRIPE PAYMENTS ============
class TestStripe:
    _session_id = None

    def test_create_checkout(self, api_client, owner_headers):
        plans = api_client.get(f"{BASE_URL}/api/owner/subscription-plans", headers=owner_headers).json()
        payload = {"plan_id": plans[0]["id"], "origin_url": "https://jaz-management.preview.emergentagent.com"}
        r = api_client.post(f"{BASE_URL}/api/payments/checkout", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "url" in data and data["url"].startswith("http")
        assert "session_id" in data
        TestStripe._session_id = data["session_id"]

    def test_check_status(self, api_client, owner_headers):
        if not TestStripe._session_id:
            return
        r = api_client.get(f"{BASE_URL}/api/payments/status/{TestStripe._session_id}", headers=owner_headers)
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "payment_status" in data


# ============ RBAC ============
class TestRBAC:
    def test_employee_cannot_access_owner(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/employees", headers=employee_headers)
        assert r.status_code == 403

    def test_employee_cannot_create_task(self, api_client, employee_headers):
        payload = {"title": "x", "description": "y", "priority": "high", "assigned_to": "emp-001", "due_date": "2026-12-31"}
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json=payload, headers=employee_headers)
        assert r.status_code == 403

    def test_owner_cannot_access_admin(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/admin/companies", headers=owner_headers)
        assert r.status_code == 403

    def test_owner_cannot_create_plan(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/admin/subscription-plans", json={"name": "x", "max_employees": 1, "price": 1, "duration_months": 1}, headers=owner_headers)
        assert r.status_code == 403

    def test_admin_cannot_access_owner(self, api_client, admin_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/employees", headers=admin_headers)
        assert r.status_code == 403

    def test_no_token_returns_error(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/owner/employees")
        assert r.status_code in (401, 403)


# ============ SMART QR ATTENDANCE (Feature #5) ============
class TestSmartQRAttendance:
    """Covers the QR-token security model, geofence/GPS enforcement, fake-GPS
    heuristics, settings, regeneration, manual-edit audit, and analytics."""
    _emp_id = None
    _emp_headers = None
    _token = None

    def _settings(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance/settings", headers=owner_headers)
        assert r.status_code == 200, r.text
        return r.json()

    def test_settings_shape_and_token_exists(self, api_client, owner_headers):
        data = self._settings(api_client, owner_headers)
        assert data["qr_code"].startswith("data:image/png;base64,")
        assert data["qr_token"]
        # Token must be opaque - never the legacy company:{id} format
        assert not data["qr_token"].startswith("company:")
        assert data["settings"]["radius_meters"] > 0
        assert data["company_name"]
        # Metadata keys must exist even before any settings update has ever
        # been made (defaulting to None, never a KeyError).
        for key in ("updated_at", "updated_by", "updated_by_name"):
            assert key in data["settings"]
        TestSmartQRAttendance._token = data["qr_token"]

    def test_regenerate_creates_new_token_and_invalidates_old(self, api_client, owner_headers, employee_headers):
        # Part 2 of the QR Attendance System spec requires a working
        # Regenerate button - this endpoint is no longer removed, it's a
        # required feature. Confirms: a fresh token/QR image/timestamp are
        # issued, settings reflect them, and the old token is immediately
        # rejected on the next scan (no separate invalidation step exists -
        # it's just no longer an exact match).
        old_token = TestSmartQRAttendance._token

        r = api_client.post(f"{BASE_URL}/api/owner/attendance/qr/regenerate", headers=owner_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["qr_code"].startswith("data:image/png;base64,")
        assert body["qr_generated_at"]

        new_settings = self._settings(api_client, owner_headers)
        new_token = new_settings["qr_token"]
        assert new_token != old_token
        assert new_settings["settings"]["qr_generated_at"] == body["qr_generated_at"]

        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": old_token, "latitude": 24.7, "longitude": 46.6},
                            headers=employee_headers)
        assert r.status_code == 400
        assert "رمز" in r.json()["detail"]

        TestSmartQRAttendance._token = new_token

    def test_company_qr_endpoint_requires_auth(self, api_client):
        r = requests.get(f"{BASE_URL}/api/company/company-001/qr")
        assert r.status_code in (401, 403)

    def test_employee_cannot_read_settings(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance/settings", headers=employee_headers)
        assert r.status_code == 403

    def test_settings_rejects_partial_coordinates(self, api_client, owner_headers):
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/settings",
                           json={"latitude": 24.7}, headers=owner_headers)
        assert r.status_code == 400

    def test_create_fresh_employee(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        payload = {
            "email": f"TEST_qr_{unique}@example.com",
            "phone": f"059{unique[:7]}",
            "password": "testpass123",
            "name": "TEST_QR_Employee",
            "role": "employee",
            "department": "QA",
        }
        r = api_client.post(f"{BASE_URL}/api/owner/employees", json=payload, headers=owner_headers)
        assert r.status_code == 200, r.text
        TestSmartQRAttendance._emp_id = r.json()["id"]
        login = api_client.post(f"{BASE_URL}/api/auth/login",
                                json={"email_or_phone": payload["email"], "password": "testpass123"})
        assert login.status_code == 200
        TestSmartQRAttendance._emp_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    def test_check_out_without_check_in_rejected(self, api_client):
        # This employee was just created and has never checked in today -
        # check-out must be rejected with the dedicated message, not treated
        # as an "already checked out" case.
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "no check-in record found" in r.json()["detail"].lower()

    def test_check_in_requires_gps(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": TestSmartQRAttendance._token},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "GPS" in r.json()["detail"]

    def test_check_in_rejects_invalid_token(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": "not-a-real-token", "latitude": 24.7, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400

    def test_qr_disable_blocks_check_in(self, api_client, owner_headers):
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/settings",
                           json={"qr_enabled": False}, headers=owner_headers)
        assert r.status_code == 200
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "معطل" in r.json()["detail"]
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/settings",
                           json={"qr_enabled": True}, headers=owner_headers)
        assert r.status_code == 200

    def test_radius_enforced_once_location_configured(self, api_client, owner_headers):
        # Configure company location with a 50m radius, then check in from ~5km away.
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/settings",
                           json={"latitude": 24.7, "longitude": 46.6, "radius_meters": 50},
                           headers=owner_headers)
        assert r.status_code == 200
        assert r.json()["location_configured"] is True
        # updated_at/updated_by/updated_by_name are always server-derived from
        # the session - the request body above never included them.
        stamped = r.json()["settings"]
        assert stamped["updated_at"]
        assert stamped["updated_by_name"] == "أحمد محمد"  # owner@demo.com's seeded name
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.745, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "خارج النطاق" in r.json()["detail"]

    def test_check_in_inside_radius_succeeds(self, api_client, owner_headers):
        # Widen the radius so the same coordinates fall inside it. This is a
        # radius-only update (no latitude/longitude in the payload) - it must
        # succeed without requiring the location to be resubmitted, and must
        # leave the saved location untouched.
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/settings",
                           json={"radius_meters": 100000}, headers=owner_headers)
        assert r.status_code == 200
        assert r.json()["settings"]["latitude"] == 24.7
        assert r.json()["settings"]["longitude"] == 46.6
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.6,
                                  "accuracy": 12.5, "device_info": "pytest-agent"},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 200, r.text

    def test_duplicate_check_in_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "already checked in" in r.json()["detail"].lower()

    def test_fake_gps_impossible_velocity_rejected(self, api_client):
        # Seconds after checking in at 24.7, a checkout from ~111km away is
        # physically impossible and must be rejected before anything is saved.
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 25.7, "longitude": 46.6},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "غير موثوق" in r.json()["detail"]

    def test_check_out_valid_and_duplicate_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.601},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 200, r.text
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                            json={"qr_code": TestSmartQRAttendance._token, "latitude": 24.7, "longitude": 46.601},
                            headers=TestSmartQRAttendance._emp_headers)
        assert r.status_code == 400
        assert "Already checked out" in r.json()["detail"]

    def test_record_stores_new_fields(self, api_client, owner_headers):
        r = api_client.get(
            f"{BASE_URL}/api/owner/attendance?employee_id={TestSmartQRAttendance._emp_id}",
            headers=owner_headers)
        assert r.status_code == 200
        records = r.json()
        assert len(records) == 1
        rec = records[0]
        assert rec["employee_department"] == "QA"  # snapshot at creation
        assert rec["device_info"] == "pytest-agent"
        assert rec["created_at"]
        assert rec["working_duration_minutes"] is not None
        assert rec["distance_from_company_meters"] is not None
        TestSmartQRAttendance._record_id = rec["id"]

    def test_filters_work(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance?department=QA&status=checked_out",
                           headers=owner_headers)
        assert r.status_code == 200
        assert any(rec["employee_id"] == TestSmartQRAttendance._emp_id for rec in r.json())
        r = api_client.get(f"{BASE_URL}/api/owner/attendance?search=TEST_QR", headers=owner_headers)
        assert r.status_code == 200
        assert any(rec["employee_id"] == TestSmartQRAttendance._emp_id for rec in r.json())
        r = api_client.get(f"{BASE_URL}/api/owner/attendance?department=NoSuchDept", headers=owner_headers)
        assert r.status_code == 200
        assert all(rec["employee_id"] != TestSmartQRAttendance._emp_id for rec in r.json())

    def test_manual_edit_creates_audit_entry(self, api_client, owner_headers):
        r = api_client.get(
            f"{BASE_URL}/api/owner/attendance?employee_id={TestSmartQRAttendance._emp_id}",
            headers=owner_headers)
        current_status = r.json()[0]["status"]
        new_status = "late" if current_status != "late" else "present"
        TestSmartQRAttendance._new_status = new_status
        r = api_client.patch(f"{BASE_URL}/api/owner/attendance/{TestSmartQRAttendance._record_id}",
                             json={"status": new_status}, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["audit_entries"] == 1

    def test_audit_log_read_only_viewer(self, api_client, owner_headers, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance/audit-log", headers=owner_headers)
        assert r.status_code == 200, r.text
        entries = r.json()
        assert len(entries) >= 1
        entry = next(e for e in entries if e["attendance_id"] == TestSmartQRAttendance._record_id)
        assert entry["field"] == "status"
        assert entry["new_value"] == TestSmartQRAttendance._new_status
        assert entry["edited_by_name"] == "أحمد محمد"
        assert entry["employee_name"] == "TEST_QR_Employee"
        assert entry["edited_at"]
        # Newest first
        timestamps = [e["edited_at"] for e in entries]
        assert timestamps == sorted(timestamps, reverse=True)
        # Read-only: no route accepts a mutation against this collection.
        r = api_client.put(f"{BASE_URL}/api/owner/attendance/audit-log/{entry['id']}",
                           json={}, headers=owner_headers)
        assert r.status_code == 404
        r = api_client.get(f"{BASE_URL}/api/owner/attendance/audit-log", headers=employee_headers)
        assert r.status_code == 403

    def test_dashboard_new_fields(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/dashboard", headers=owner_headers)
        assert r.status_code == 200
        data = r.json()
        assert "checked_out_today" in data
        assert "attendance_percentage" in data

    def test_analytics_shape(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance/analytics", headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        # Every metric that existed before this round of refinements must
        # still be present and untouched - only additive fields below.
        for key in ("average_check_in_time", "average_check_out_time", "average_working_hours",
                    "attendance_percentage", "late_count", "absence_count", "employee_ranking",
                    "late_percentage", "average_distance_meters", "department_attendance"):
            assert key in data
        for chart in ("attendance_trend", "late_trend", "working_hours_trend"):
            assert isinstance(data["charts"][chart], list)
        assert isinstance(data["department_attendance"], list)
        qa_dept = next((d for d in data["department_attendance"] if d["department"] == "QA"), None)
        assert qa_dept is not None
        assert 0 <= qa_dept["attendance_rate"] <= 100
        assert data["average_distance_meters"] is not None

    def test_qr_token_stable_after_regeneration(self, api_client, owner_headers):
        # The token is rotated exactly once in this class, by
        # test_regenerate_creates_new_token_and_invalidates_old - every test
        # since then (and this one) must see that same post-regeneration
        # token, not a further, unexpected rotation.
        current = self._settings(api_client, owner_headers)
        assert current["qr_token"] == TestSmartQRAttendance._token

    def test_cleanup(self, api_client, owner_headers):
        if TestSmartQRAttendance._emp_id:
            api_client.delete(f"{BASE_URL}/api/owner/employees/{TestSmartQRAttendance._emp_id}",
                              headers=owner_headers)


# ============ WORK MESSAGING SYSTEM ============
class TestWorkMessaging:
    """Covers compose/draft/send, threads+replies, workflow transitions,
    delivery progress, department fan-out, attachments (lazy-load + size cap),
    reminders, pin/close, search/filters, Communication Center, timeline,
    audit-free activity log, and cross-company isolation."""
    _emp_a_id = None
    _emp_a_headers = None
    _emp_b_id = None
    _emp_b_headers = None
    _dept = None
    _root_id = None
    _root_ref = None
    _draft_id = None
    _other_company_owner_headers = None

    def test_setup_employees(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        TestWorkMessaging._dept = f"TEST_Dept_{unique}"
        for label in ("a", "b"):
            payload = {
                "email": f"TEST_msg_{label}_{unique}@example.com",
                "phone": f"057{uuid.uuid4().hex[:7]}",
                "password": "testpass123",
                "name": f"TEST_Messaging_{label}",
                "role": "employee",
                "department": TestWorkMessaging._dept,
            }
            r = api_client.post(f"{BASE_URL}/api/owner/employees", json=payload, headers=owner_headers)
            assert r.status_code == 200, r.text
            login = api_client.post(f"{BASE_URL}/api/auth/login",
                                    json={"email_or_phone": payload["email"], "password": "testpass123"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            if label == "a":
                TestWorkMessaging._emp_a_id = r.json()["id"]
                TestWorkMessaging._emp_a_headers = headers
            else:
                TestWorkMessaging._emp_b_id = r.json()["id"]
                TestWorkMessaging._emp_b_headers = headers

    def test_setup_other_company(self, api_client, admin_headers):
        unique = uuid.uuid4().hex[:8]
        owner_email = f"TEST_otherowner_{unique}@example.com"
        plan = api_client.post(f"{BASE_URL}/api/admin/subscription-plans",
                               json={"name": f"TEST_Plan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
                               headers=admin_headers).json()
        company = api_client.post(f"{BASE_URL}/api/admin/companies", json={
            "name": f"TEST_OtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Owner", "owner_password": "pass1234", "owner_phone": f"058{unique[:7]}",
            "subscription_plan_id": plan["id"],
        }, headers=admin_headers)
        assert company.status_code == 200, company.text
        # CompanyResponse.owner_email is only populated by the list endpoint's
        # join, not by create - use the email we just sent, not the response.
        login = api_client.post(f"{BASE_URL}/api/auth/login",
                                json={"email_or_phone": owner_email, "password": "pass1234"})
        assert login.status_code == 200, login.text
        TestWorkMessaging._other_company_owner_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # ---- Compose / draft / send ----

    def test_reference_number_sequential(self, api_client, owner_headers):
        r1 = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "TEST_Seq_1", "body": "b", "recipient_type": "employee",
            "recipient_ids": [TestWorkMessaging._emp_a_id],
        }, headers=owner_headers)
        r2 = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "TEST_Seq_2", "body": "b", "recipient_type": "employee",
            "recipient_ids": [TestWorkMessaging._emp_a_id],
        }, headers=owner_headers)
        assert r1.status_code == 200 and r2.status_code == 200
        n1 = int(r1.json()["reference_number"].split("-")[1])
        n2 = int(r2.json()["reference_number"].split("-")[1])
        assert n2 == n1 + 1
        assert r1.json()["reference_number"].startswith("MSG-")

    def test_draft_not_delivered_until_sent(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "TEST_Draft", "body": "draft body", "recipient_type": "employee",
            "recipient_ids": [TestWorkMessaging._emp_a_id], "is_draft": True,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["is_draft"] is True
        TestWorkMessaging._draft_id = r.json()["id"]

        drafts = api_client.get(f"{BASE_URL}/api/messages/drafts", headers=owner_headers).json()
        assert any(d["id"] == TestWorkMessaging._draft_id for d in drafts["items"])

        inbox = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=TestWorkMessaging._emp_a_headers).json()
        assert all(m["id"] != TestWorkMessaging._draft_id for m in inbox["items"])

    def test_send_draft_delivers_it(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._draft_id}/send", headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["is_draft"] is False
        inbox = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=TestWorkMessaging._emp_a_headers).json()
        assert any(m["id"] == TestWorkMessaging._draft_id for m in inbox["items"])

    def test_create_root_message_with_full_options(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "TEST_Report", "body": "Please review", "priority": "urgent",
            "confidentiality": "confidential", "tags": ["Sales", "Report"],
            "recipient_type": "employee", "recipient_ids": [TestWorkMessaging._emp_a_id],
            "requires_acknowledgement": True, "completion_required": True,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["priority"] == "urgent"
        assert data["confidentiality"] == "confidential"
        assert set(data["tags"]) == {"Sales", "Report"}
        assert data["delivery_progress"] == {"total": 1, "delivered": 1, "seen": 0, "accepted": 0, "completed": 0}
        assert data["attachment_count"] == 0
        assert data["reply_count"] == 0
        TestWorkMessaging._root_id = data["id"]
        TestWorkMessaging._root_ref = data["reference_number"]

    def test_invalid_priority_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "x", "body": "y", "priority": "critical",
            "recipient_type": "employee", "recipient_ids": [TestWorkMessaging._emp_a_id],
        }, headers=owner_headers)
        assert r.status_code == 400

    def test_recipient_outside_company_rejected(self, api_client, owner_headers, employee_headers):
        # employee_headers is employee1@demo.com - a real user, but from the
        # SAME company here; use a bogus id to prove cross-company/nonexistent
        # recipients are rejected server-side regardless of client input.
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "x", "body": "y", "recipient_type": "employee",
            "recipient_ids": ["not-a-real-user-id"],
        }, headers=owner_headers)
        assert r.status_code == 400

    def test_department_fan_out(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "TEST_DeptWide", "body": "dept message", "recipient_type": "department",
            "recipient_ids": [TestWorkMessaging._dept],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["delivery_progress"]["total"] == 2  # both TEST employees share the department
        assert r.json()["recipient_department"] == TestWorkMessaging._dept
        for headers in (TestWorkMessaging._emp_a_headers, TestWorkMessaging._emp_b_headers):
            inbox = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=headers).json()
            assert any(m["subject"] == "TEST_DeptWide" for m in inbox["items"])

    # ---- Workflow: open -> accept -> complete, and guards ----

    def test_complete_before_accept_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/complete",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 400
        assert "accepted" in r.json()["detail"].lower()

    def test_sender_cannot_accept_own_message(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/accept", headers=owner_headers)
        assert r.status_code == 400

    def test_open_then_edit_blocked(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/open",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 200
        r = api_client.patch(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}",
                             json={"subject": "edited"}, headers=owner_headers)
        assert r.status_code == 400

    def test_accept_then_complete(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/accept",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 200, r.text
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/complete",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 200, r.text
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/complete",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 400  # already completed

    def test_delivery_progress_reflects_completion(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/messages/{TestWorkMessaging._root_id}/timeline", headers=owner_headers)
        assert r.status_code == 200, r.text
        progress = r.json()["delivery_progress"]
        assert progress == {"total": 1, "delivered": 1, "seen": 1, "accepted": 1, "completed": 1}
        labels = [e["label"] for e in r.json()["events"]]
        for expected in ("delivered", "sent", "opened", "accepted", "completed"):
            assert expected in labels

    # ---- Threads / replies bump the thread, never create a separate inbox row ----

    def test_reply_stays_one_thread_and_bumps_unread(self, api_client, owner_headers):
        before = api_client.get(f"{BASE_URL}/api/messages/sent", headers=owner_headers).json()
        root_before = next(m for m in before["items"] if m["id"] == TestWorkMessaging._root_id)
        assert root_before["reply_count"] == 0

        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/reply",
                            json={"body": "On it"}, headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 200, r.text

        after = api_client.get(f"{BASE_URL}/api/messages/sent", headers=owner_headers).json()
        root_after = next(m for m in after["items"] if m["id"] == TestWorkMessaging._root_id)
        assert root_after["reply_count"] == 1
        assert root_after["my_is_unread"] is True  # owner is notified of the reply
        assert after["total"] == before["total"]  # no new top-level row was created

    def test_unread_only_badge_count(self, api_client, owner_headers):
        # Powers the sidebar badge (Layout.js) via the same inbox endpoint,
        # no dedicated count endpoint needed. Unlike the normal Inbox folder
        # (role="recipient" only), the badge spans ALL of the owner's rows -
        # so it correctly counts the unread reply on the thread the OWNER
        # sent (a role="sender" row), which the plain /inbox view excludes
        # by design (Sent items don't belong in Inbox).
        r = api_client.get(f"{BASE_URL}/api/messages/inbox?unread_only=true&page_size=1", headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["total"] >= 1

    def test_thread_detail_includes_reply(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}", headers=owner_headers)
        assert r.status_code == 200
        subjects = [m["subject"] for m in r.json()["messages"]]
        assert len(r.json()["messages"]) == 2
        assert any(s.startswith("Re:") for s in subjects)

    def test_forward_creates_new_thread(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/forward", json={
            "subject": "ignored", "body": "FYI", "recipient_type": "employee",
            "recipient_ids": [TestWorkMessaging._emp_b_id],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["is_forward"] is True
        assert r.json()["thread_id"] == r.json()["id"]  # brand new thread
        assert r.json()["subject"].startswith("Fwd:")
        inbox_b = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=TestWorkMessaging._emp_b_headers).json()
        assert any(m["is_forward"] for m in inbox_b["items"])

    # ---- Star / archive / unread / close / pin ----

    def test_star_and_starred_folder(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/star", headers=owner_headers)
        assert r.status_code == 200 and r.json()["is_starred"] is True
        starred = api_client.get(f"{BASE_URL}/api/messages/starred", headers=owner_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in starred["items"])

    def test_archive_then_reappears_in_archived_not_inbox(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/archive",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 200
        inbox = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=TestWorkMessaging._emp_a_headers).json()
        assert all(m["id"] != TestWorkMessaging._root_id for m in inbox["items"])
        archived = api_client.get(f"{BASE_URL}/api/messages/archived", headers=TestWorkMessaging._emp_a_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in archived["items"])

    def test_mark_unread(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/unread", headers=owner_headers)
        assert r.status_code == 200
        sent = api_client.get(f"{BASE_URL}/api/messages/sent", headers=owner_headers).json()
        row = next(m for m in sent["items"] if m["id"] == TestWorkMessaging._root_id)
        assert row["my_is_unread"] is True

    def test_close_thread_sender_only(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/close",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 404  # not the sender
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/close", headers=owner_headers)
        assert r.status_code == 200, r.text
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/close", headers=owner_headers)
        assert r.status_code == 400  # already closed

    def test_pin_owner_only(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/owner/messages/{TestWorkMessaging._root_id}/pin",
                            headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 403
        r = api_client.post(f"{BASE_URL}/api/owner/messages/{TestWorkMessaging._root_id}/pin", headers=owner_headers)
        assert r.status_code == 200 and r.json()["is_pinned"] is True

    # ---- Attachments: lazy-load + size cap ----

    def test_attachment_upload_and_lazy_fetch(self, api_client, owner_headers):
        small_png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEX///+nxBvIAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/attachments", json={
            "filename": "proof.png", "mime_type": "image/png", "data": small_png,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["attachment_type"] == "image"
        attachment_id = r.json()["id"]

        # List views never carry the payload - only the count.
        sent = api_client.get(f"{BASE_URL}/api/messages/sent", headers=owner_headers).json()
        row = next(m for m in sent["items"] if m["id"] == TestWorkMessaging._root_id)
        assert row["attachment_count"] == 1

        thread = api_client.get(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}", headers=owner_headers).json()
        root_attachments = thread["messages"][0]["attachments"]
        assert len(root_attachments) == 1
        assert "data" not in root_attachments[0]

        full = api_client.get(f"{BASE_URL}/api/message-attachments/{attachment_id}", headers=owner_headers)
        assert full.status_code == 200
        assert full.json()["data"] == small_png

    def test_attachment_oversized_rejected(self, api_client, owner_headers):
        raw_len = int(11 * 1024 * 1024 * 4 / 3)
        raw_len -= raw_len % 4  # base64 length must be a multiple of 4 to decode cleanly
        oversized = "A" * raw_len  # ~11MB once decoded
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/attachments", json={
            "filename": "huge.zip", "mime_type": "application/zip", "data": oversized,
        }, headers=owner_headers)
        assert r.status_code == 400
        assert "MB" in r.json()["detail"]

    def test_attachment_not_accessible_to_non_participant(self, api_client, owner_headers, employee_headers):
        small_png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEX///+nxBvIAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        att = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/attachments", json={
            "filename": "x.png", "mime_type": "image/png", "data": small_png,
        }, headers=owner_headers).json()
        # employee1@demo.com (employee_headers) is a real same-company user
        # but never a participant of this specific thread.
        r = api_client.get(f"{BASE_URL}/api/message-attachments/{att['id']}", headers=employee_headers)
        assert r.status_code == 404

    # ---- Reminders ----

    def test_reminder_preset_and_immediate_delivery(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/reminder",
                            json={"remind_at": "2020-01-01T00:00:00+00:00"}, headers=owner_headers)
        assert r.status_code == 200, r.text
        # Reminder is already due (set in the past) - self-heals into a real
        # notification the next time /notifications is read, for this user only.
        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "message_reminder" for n in notifications)
        other_notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=TestWorkMessaging._emp_a_headers).json()
        assert not any(n["type"] == "message_reminder" for n in other_notifications)

    def test_reminder_invalid_preset_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}/reminder",
                            json={"preset": "next_week"}, headers=owner_headers)
        assert r.status_code == 400

    # ---- Search / filters / Communication Center ----

    def test_communication_center_filters(self, api_client, owner_headers):
        base = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center", headers=owner_headers).json()
        assert base["total"] >= 1
        assert all(m["reply_count"] is not None for m in base["items"])

        by_ref = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?reference_number={TestWorkMessaging._root_ref}",
                                headers=owner_headers).json()
        assert by_ref["total"] == 1
        assert by_ref["items"][0]["id"] == TestWorkMessaging._root_id

        by_tag = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?tags=Sales", headers=owner_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in by_tag["items"])

        # Status is "archived" by now (test_archive_then_reappears_in_archived_not_inbox
        # ran earlier in this class and archived employee A's recipient row).
        by_status = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?status=archived", headers=owner_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in by_status["items"])

        by_dept = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?department={TestWorkMessaging._dept}",
                                 headers=owner_headers).json()
        assert by_dept["total"] >= 1

        by_attachment = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?attachment_type=image",
                                       headers=owner_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in by_attachment["items"])

        by_closed = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?status=closed", headers=owner_headers).json()
        assert any(m["id"] == TestWorkMessaging._root_id for m in by_closed["items"])

        # Replies never appear as their own row.
        for item in base["items"]:
            assert item["id"] == item["thread_id"]

    def test_communication_center_owner_only(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center",
                           headers=TestWorkMessaging._emp_a_headers)
        assert r.status_code == 403

    def test_pagination(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/messages/communication-center?page=1&page_size=1", headers=owner_headers)
        data = r.json()
        assert data["page_size"] == 1
        assert len(data["items"]) <= 1
        assert data["total"] >= 2

    # ---- Company isolation ----

    def test_cross_company_cannot_see_message(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/messages/{TestWorkMessaging._root_id}",
                           headers=TestWorkMessaging._other_company_owner_headers)
        assert r.status_code == 404

    def test_cross_company_recipient_rejected(self, api_client, owner_headers):
        # Even though the id is a real user in the platform, they're in a
        # different company - resolve_recipients must reject it.
        r = api_client.post(f"{BASE_URL}/api/messages", json={
            "subject": "x", "body": "y", "recipient_type": "employee",
            "recipient_ids": [TestWorkMessaging._emp_a_id],
        }, headers=TestWorkMessaging._other_company_owner_headers)
        assert r.status_code == 400

    def test_super_admin_has_no_messaging_route(self, api_client, admin_headers):
        r = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=admin_headers)
        assert r.status_code == 403

    # ---- Backward compatibility spot-check ----

    def test_existing_notifications_endpoint_unaffected_for_users_without_reminders(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/notifications", headers=employee_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_cleanup(self, api_client, owner_headers):
        for emp_id in (TestWorkMessaging._emp_a_id, TestWorkMessaging._emp_b_id):
            if emp_id:
                api_client.delete(f"{BASE_URL}/api/owner/employees/{emp_id}", headers=owner_headers)


# ============ SMART COMPANY CALENDAR (Feature #7) ============
class TestSmartCalendar:
    """Covers reference numbers, recurrence expansion + exceptions (This
    Event Only / This And Future / Entire Series), conflict detection
    (including the all-day-only-vs-all-day rule and holiday warnings),
    company-wide/multi-department recipient resolution, attendance
    responses + owner notification fan-out, attachments, reminders,
    search, owner monitor + activity log, working hours, dashboard
    widgets, and company isolation."""
    _emp_a_id = None
    _emp_a_headers = None
    _emp_b_id = None
    _dept = None
    _other_company_owner_headers = None
    _root_id = None
    _root_ref = None

    def test_setup_employees(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        TestSmartCalendar._dept = f"TEST_CalDept_{unique}"
        for label in ("a", "b"):
            payload = {
                "email": f"TEST_cal_{label}_{unique}@example.com",
                "phone": f"056{uuid.uuid4().hex[:7]}",
                "password": "testpass123",
                "name": f"TEST_Calendar_{label}",
                "role": "employee",
                "department": TestSmartCalendar._dept,
            }
            r = api_client.post(f"{BASE_URL}/api/owner/employees", json=payload, headers=owner_headers)
            assert r.status_code == 200, r.text
            if label == "a":
                TestSmartCalendar._emp_a_id = r.json()["id"]
                login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": payload["email"], "password": "testpass123"})
                TestSmartCalendar._emp_a_headers = {"Authorization": f"Bearer {login.json()['token']}"}
            else:
                TestSmartCalendar._emp_b_id = r.json()["id"]

    def test_setup_other_company(self, api_client, admin_headers):
        unique = uuid.uuid4().hex[:8]
        owner_email = f"TEST_calother_{unique}@example.com"
        plan = api_client.post(f"{BASE_URL}/api/admin/subscription-plans",
                               json={"name": f"TEST_CalPlan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
                               headers=admin_headers).json()
        company = api_client.post(f"{BASE_URL}/api/admin/companies", json={
            "name": f"TEST_CalOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Cal Owner", "owner_password": "pass1234", "owner_phone": f"059{unique[:7]}",
            "subscription_plan_id": plan["id"],
        }, headers=admin_headers)
        assert company.status_code == 200, company.text
        login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
        assert login.status_code == 200
        TestSmartCalendar._other_company_owner_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # ---- Create / reference numbers / validation ----

    def test_reference_number_sequential(self, api_client, owner_headers):
        r1 = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Seq1", "category": "meeting", "start_date": "2026-08-01", "start_time": "09:00",
            "end_date": "2026-08-01", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        r2 = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Seq2", "category": "meeting", "start_date": "2026-08-01", "start_time": "11:00",
            "end_date": "2026-08-01", "end_time": "11:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r1.status_code == 200 and r2.status_code == 200
        n1 = int(r1.json()["reference_number"].split("-")[1])
        n2 = int(r2.json()["reference_number"].split("-")[1])
        assert n2 == n1 + 1
        assert r1.json()["reference_number"].startswith("CAL-")

    def test_invalid_category_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "x", "category": "not_a_real_category", "start_date": "2026-08-01", "end_date": "2026-08-01",
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r.status_code == 400

    def test_recipient_outside_company_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "x", "category": "meeting", "start_date": "2026-08-01", "end_date": "2026-08-01",
            "recipient_type": "employee", "recipient_ids": ["not-a-real-user"],
        }, headers=owner_headers)
        assert r.status_code == 400

    def test_view_range_required(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/calendar/events", headers=owner_headers)
        assert r.status_code == 400

    def test_create_meeting_with_full_options(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Full_Meeting", "description": "desc", "category": "meeting", "priority": "high",
            "start_date": "2026-08-05", "start_time": "10:00", "end_date": "2026-08-05", "end_time": "11:00",
            "location_type": "online", "online_link": "https://example.com/call",
            "visibility": "company", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["priority"] == "high"
        assert data["location_type"] == "online"
        assert data["response_progress"] == {"total": 1, "responded": 0}
        TestSmartCalendar._root_id = data["id"]
        TestSmartCalendar._root_ref = data["reference_number"]

    def test_list_shows_event_for_owner_and_participant(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-08-01&view_end=2026-08-31", headers=owner_headers)
        assert r.status_code == 200
        assert any(e["id"] == TestSmartCalendar._root_id for e in r.json()["items"])
        r2 = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-08-01&view_end=2026-08-31", headers=TestSmartCalendar._emp_a_headers)
        assert any(e["id"] == TestSmartCalendar._root_id for e in r2.json()["items"])

    # ---- Recurrence + exceptions ----

    def test_weekly_recurrence_expands_correctly(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Weekly_Standup", "category": "meeting", "start_date": "2026-09-07", "start_time": "09:00",
            "end_date": "2026-09-07", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "recurrence_type": "weekly", "recurrence_interval": 1, "recurrence_end_type": "after_count", "recurrence_end_value": "4",
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        event_id = r.json()["id"]
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-09-01&view_end=2026-10-05", headers=owner_headers).json()
        occs = [i for i in listing["items"] if i["id"] == event_id]
        assert len(occs) == 4
        assert [o["occurrence_date"] for o in occs] == ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
        TestSmartCalendar._recurring_id = event_id

    def test_this_event_only_cancel(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._recurring_id}/cancel?scope=this_event_only&occurrence_date=2026-09-14",
            headers=owner_headers)
        assert r.status_code == 200, r.text
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-09-01&view_end=2026-10-05", headers=owner_headers).json()
        occs = [i for i in listing["items"] if i["id"] == TestSmartCalendar._recurring_id]
        assert [o["occurrence_date"] for o in occs] == ["2026-09-07", "2026-09-21", "2026-09-28"]

    def test_this_event_only_override_edit(self, api_client, owner_headers):
        r = api_client.patch(
            f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._recurring_id}?scope=this_event_only&occurrence_date=2026-09-21",
            json={"title": "TEST_Special_Standup"}, headers=owner_headers)
        assert r.status_code == 200, r.text
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-09-01&view_end=2026-10-05", headers=owner_headers).json()
        occs = {i["occurrence_date"]: i["title"] for i in listing["items"] if i["id"] == TestSmartCalendar._recurring_id}
        assert occs["2026-09-21"] == "TEST_Special_Standup"
        assert occs["2026-09-07"] == "TEST_Weekly_Standup"  # unaffected

    def test_this_and_future_splits_series(self, api_client, owner_headers):
        r = api_client.patch(
            f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._recurring_id}?scope=this_and_future&occurrence_date=2026-09-28",
            json={"start_time": "09:15", "end_time": "09:45"}, headers=owner_headers)
        assert r.status_code == 200, r.text
        new_series_id = r.json()["id"]
        assert new_series_id != TestSmartCalendar._recurring_id
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-09-01&view_end=2026-10-05", headers=owner_headers).json()
        old_occs = [i for i in listing["items"] if i["id"] == TestSmartCalendar._recurring_id]
        new_occs = [i for i in listing["items"] if i["id"] == new_series_id]
        assert [o["start_time"] for o in old_occs] == ["09:00", "09:00"]  # 09-07 and the overridden 09-21, unaffected
        assert [o["start_time"] for o in new_occs] == ["09:15"]  # only 09-28 onward

    def test_entire_series_edit(self, api_client, owner_headers):
        r = api_client.patch(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._recurring_id}?scope=entire_series",
                             json={"description": "TEST_updated_desc"}, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "TEST_updated_desc"

    # ---- Conflict detection ----

    def test_conflict_blocks_without_override(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/check-conflicts", json={
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "start_date": "2026-08-05", "start_time": "10:15", "end_date": "2026-08-05", "end_time": "10:45", "all_day": False,
        }, headers=owner_headers)
        assert r.status_code == 200
        assert r.json()["has_conflict"] is True

        r2 = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Conflicting", "category": "meeting", "start_date": "2026-08-05", "start_time": "10:15",
            "end_date": "2026-08-05", "end_time": "10:45", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r2.status_code == 409

    def test_conflict_override_allows_save(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Conflicting", "category": "meeting", "start_date": "2026-08-05", "start_time": "10:15",
            "end_date": "2026-08-05", "end_time": "10:45", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "override_conflicts": True,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text

    def test_all_day_only_conflicts_with_all_day(self, api_client, owner_headers):
        # A timed event on 2026-08-06 must NOT be flagged as conflicting with
        # an all-day event check, and vice versa.
        r_timed = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_TimedOnly", "category": "meeting", "start_date": "2026-08-06", "start_time": "10:00",
            "end_date": "2026-08-06", "end_time": "11:00", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r_timed.status_code == 200, r_timed.text

        conflict_check = api_client.post(f"{BASE_URL}/api/calendar/events/check-conflicts", json={
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "start_date": "2026-08-06", "end_date": "2026-08-06", "all_day": True,
        }, headers=owner_headers).json()
        assert conflict_check["has_conflict"] is False

    def test_holiday_conflict_warning(self, api_client, owner_headers):
        holiday = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_National_Day", "category": "company_holiday", "all_day": True,
            "start_date": "2026-10-15", "end_date": "2026-10-15", "recipient_type": "company", "visibility": "company",
        }, headers=owner_headers)
        assert holiday.status_code == 200, holiday.text

        check = api_client.post(f"{BASE_URL}/api/calendar/events/check-conflicts", json={
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "start_date": "2026-10-15", "start_time": "10:00", "end_date": "2026-10-15", "end_time": "11:00", "all_day": False,
        }, headers=owner_headers).json()
        assert check["holiday_conflict"] is not None
        assert check["holiday_conflict"]["title"] == "TEST_National_Day"

    def test_company_wide_recipients_resolve_to_all_employees(self, api_client, owner_headers):
        # response_progress/participants are detail-view fields, not carried
        # by the list/range endpoint (which stays lightweight for grid views).
        r = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-10-15&view_end=2026-10-15", headers=owner_headers)
        holiday_summary = next(e for e in r.json()["items"] if e["title"] == "TEST_National_Day")
        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{holiday_summary['id']}", headers=owner_headers).json()
        assert detail["response_progress"]["total"] >= 3  # owner + at least emp_a, emp_b

    def test_multi_department_recipients(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_DeptWide", "category": "training", "start_date": "2026-08-10", "start_time": "09:00",
            "end_date": "2026-08-10", "end_time": "10:00", "recipient_type": "department", "recipient_ids": [TestSmartCalendar._dept],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["response_progress"]["total"] == 2  # emp_a and emp_b share TEST_CalDept

    # ---- Attendance responses + notifications ----

    def test_respond_and_owner_notified(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/respond",
                            json={"status": "accepted"}, headers=TestSmartCalendar._emp_a_headers)
        assert r.status_code == 200, r.text
        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "calendar_accepted" for n in notifications)

    def test_employee_created_event_notifies_owner(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_EmployeeCreated", "category": "meeting", "start_date": "2026-08-12", "start_time": "09:00",
            "end_date": "2026-08-12", "end_time": "09:30", "recipient_type": "owner_plus_employees",
            "recipient_ids": [TestSmartCalendar._emp_b_id],
        }, headers=TestSmartCalendar._emp_a_headers)
        assert r.status_code == 200, r.text
        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "calendar_owner_created" for n in notifications)

    def test_invalid_response_status_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/respond",
                            json={"status": "maybe_later"}, headers=TestSmartCalendar._emp_a_headers)
        assert r.status_code == 400

    # ---- Participants ----

    def test_add_and_remove_participant(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/participants/add",
                            json={"participant_ids": [TestSmartCalendar._emp_b_id]}, headers=owner_headers)
        assert r.status_code == 200, r.text
        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}", headers=owner_headers).json()
        assert any(p["participant_id"] == TestSmartCalendar._emp_b_id for p in detail["participants"])

        r2 = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/participants/remove",
                             json={"participant_ids": [TestSmartCalendar._emp_b_id]}, headers=owner_headers)
        assert r2.status_code == 200
        detail2 = api_client.get(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}", headers=owner_headers).json()
        assert not any(p["participant_id"] == TestSmartCalendar._emp_b_id for p in detail2["participants"])

    # ---- Attachments ----

    def test_attachment_upload_lazy_fetch_and_size_cap(self, api_client, owner_headers):
        small_png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEX///+nxBvIAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/attachments",
                            json={"filename": "agenda.png", "mime_type": "image/png", "data": small_png}, headers=owner_headers)
        assert r.status_code == 200, r.text
        attachment_id = r.json()["id"]

        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}", headers=owner_headers).json()
        assert detail["attachment_count"] == 1

        full = api_client.get(f"{BASE_URL}/api/calendar-attachments/{attachment_id}", headers=owner_headers)
        assert full.status_code == 200
        assert full.json()["data"] == small_png

        raw_len = int(11 * 1024 * 1024 * 4 / 3)
        raw_len -= raw_len % 4
        oversized = "A" * raw_len
        r2 = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/attachments",
                             json={"filename": "huge.zip", "mime_type": "application/zip", "data": oversized}, headers=owner_headers)
        assert r2.status_code == 400
        assert "MB" in r2.json()["detail"]

    # ---- Reminders ----

    def test_reminder_preset_and_self_heal_delivery(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/reminders",
                            json={"remind_at": "2020-01-01T00:00:00+00:00"}, headers=owner_headers)
        assert r.status_code == 200, r.text
        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "calendar_reminder" for n in notifications)
        other = api_client.get(f"{BASE_URL}/api/notifications", headers=TestSmartCalendar._emp_a_headers).json()
        assert not any(n["type"] == "calendar_reminder" and "TEST_Full_Meeting" in n["message"] for n in other)

    def test_reminder_invalid_preset_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/reminders",
                            json={"preset": "next_tuesday"}, headers=owner_headers)
        assert r.status_code == 400

    # ---- Search / monitor / activity / working hours ----

    def test_search_filters(self, api_client, owner_headers):
        by_ref = api_client.get(f"{BASE_URL}/api/calendar/events/search?reference_number={TestSmartCalendar._root_ref}", headers=owner_headers).json()
        assert by_ref["total"] == 1
        assert by_ref["items"][0]["id"] == TestSmartCalendar._root_id

        by_category = api_client.get(f"{BASE_URL}/api/calendar/events/search?category=meeting&date_from=2026-08-01&date_to=2026-08-31", headers=owner_headers).json()
        assert any(e["id"] == TestSmartCalendar._root_id for e in by_category["items"])

        by_priority = api_client.get(f"{BASE_URL}/api/calendar/events/search?priority=high&date_from=2026-08-01&date_to=2026-08-31", headers=owner_headers).json()
        assert any(e["id"] == TestSmartCalendar._root_id for e in by_priority["items"])

    def test_owner_monitor_and_activity_log(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/calendar/monitor", headers=owner_headers)
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        row = next(i for i in r.json()["items"] if i["id"] == TestSmartCalendar._root_id)
        assert len(row["participants"]) >= 1

        activity = api_client.get(f"{BASE_URL}/api/owner/calendar/events/{TestSmartCalendar._root_id}/activity", headers=owner_headers)
        assert activity.status_code == 200
        verbs = [a["verb"] for a in activity.json()]
        assert "created" in verbs
        assert "accepted" in verbs

    def test_monitor_and_working_hours_owner_only(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/owner/calendar/monitor", headers=TestSmartCalendar._emp_a_headers)
        assert r.status_code == 403
        r2 = api_client.put(f"{BASE_URL}/api/owner/calendar/working-hours",
                            json={"working_days": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "18:00"},
                            headers=TestSmartCalendar._emp_a_headers)
        assert r2.status_code == 403

    def test_working_hours_get_put(self, api_client, owner_headers):
        r = api_client.put(f"{BASE_URL}/api/owner/calendar/working-hours",
                           json={"working_days": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "18:00"}, headers=owner_headers)
        assert r.status_code == 200
        r2 = api_client.get(f"{BASE_URL}/api/owner/calendar/working-hours", headers=owner_headers)
        assert r2.json() == {"working_days": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "18:00"}
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-08-01&view_end=2026-08-01", headers=owner_headers).json()
        assert listing["working_hours"]["start_time"] == "09:00"

    # ---- Dashboard widgets ----

    def test_dashboard_widgets_present(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/dashboard", headers=owner_headers)
        assert r.status_code == 200
        widgets = r.json()["calendar_widgets"]
        for key in ("today_events", "tomorrow_events", "next_event", "upcoming_meetings",
                    "upcoming_holidays", "meetings_starting_soon", "events_requiring_response", "high_priority_events"):
            assert key in widgets

        r2 = api_client.get(f"{BASE_URL}/api/employee/dashboard", headers=TestSmartCalendar._emp_a_headers)
        assert r2.status_code == 200
        emp_widgets = r2.json()["calendar_widgets"]
        for key in ("today_events", "next_event", "upcoming_meetings", "upcoming_holidays", "unanswered_invitations"):
            assert key in emp_widgets

    # ---- Soft delete / archive ----

    def test_cancel_is_soft_never_removes_row(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}/cancel?scope=entire_series", headers=owner_headers)
        assert r.status_code == 200, r.text
        # Still fetchable directly and still appears in owner monitor - never deleted.
        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}", headers=owner_headers)
        assert detail.status_code == 200
        assert detail.json()["display_status"] == "cancelled"
        monitor = api_client.get(f"{BASE_URL}/api/owner/calendar/monitor", headers=owner_headers).json()
        assert any(i["id"] == TestSmartCalendar._root_id for i in monitor["items"])

    # ---- Visibility enforcement ----

    def test_private_event_not_visible_to_non_participant(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_Private", "category": "personal", "start_date": "2026-08-20", "start_time": "09:00",
            "end_date": "2026-08-20", "end_time": "09:30", "visibility": "private",
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_b_id],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        private_id = r.json()["id"]
        # emp_a is neither creator nor participant nor same-department-visible (visibility=private)
        r2 = api_client.get(f"{BASE_URL}/api/calendar/events/{private_id}", headers=TestSmartCalendar._emp_a_headers)
        assert r2.status_code == 404
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-08-20&view_end=2026-08-20", headers=TestSmartCalendar._emp_a_headers).json()
        assert all(e["id"] != private_id for e in listing["items"])

    # ---- Company isolation ----

    def test_cross_company_cannot_see_event(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/calendar/events/{TestSmartCalendar._root_id}", headers=TestSmartCalendar._other_company_owner_headers)
        assert r.status_code == 404

    def test_cross_company_recipient_rejected(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "x", "category": "meeting", "start_date": "2026-08-01", "end_date": "2026-08-01",
            "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=TestSmartCalendar._other_company_owner_headers)
        assert r.status_code == 400

    def test_super_admin_has_no_calendar_access(self, api_client, admin_headers):
        r = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-08-01&view_end=2026-08-31", headers=admin_headers)
        assert r.status_code == 403

    def test_pagination(self, api_client, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/calendar/monitor?page=1&page_size=2", headers=owner_headers)
        data = r.json()
        assert data["page_size"] == 2
        assert len(data["items"]) <= 2
        assert data["total"] >= 5

    # ---- Backward compatibility spot-check ----

    def test_existing_notification_and_task_endpoints_unaffected(self, api_client, employee_headers, owner_headers):
        r = api_client.get(f"{BASE_URL}/api/notifications", headers=employee_headers)
        assert r.status_code == 200
        r2 = api_client.get(f"{BASE_URL}/api/owner/tasks", headers=owner_headers)
        assert r2.status_code == 200

    # ============ Refinements: Open Conversation, Final Attendance, ============
    # ============ Meeting Notes, hardened cancellation scoping         ============

    def test_open_conversation_creates_and_is_idempotent(self, api_client, owner_headers, employee_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_OpenConvo", "category": "meeting", "start_date": "2026-08-15", "start_time": "09:00",
            "end_date": "2026-08-15", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        event_id = r.json()["id"]

        first = api_client.post(f"{BASE_URL}/api/calendar/events/{event_id}/open-conversation", headers=owner_headers)
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True
        thread_id = first.json()["thread_id"]

        second = api_client.post(f"{BASE_URL}/api/calendar/events/{event_id}/open-conversation", headers=owner_headers)
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["thread_id"] == thread_id

        # The thread genuinely exists in Work Messages and reached the invited employee.
        inbox = api_client.get(f"{BASE_URL}/api/messages/inbox", headers=TestSmartCalendar._emp_a_headers).json()
        assert any(m["id"] == thread_id for m in inbox["items"])

        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{event_id}", headers=owner_headers).json()
        assert detail["linked_thread_id"] == thread_id

    def test_open_conversation_requires_other_participants(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_SoloEvent", "category": "personal", "start_date": "2026-08-16", "start_time": "09:00",
            "end_date": "2026-08-16", "end_time": "09:30", "recipient_type": "owner",
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        event_id = r.json()["id"]
        r2 = api_client.post(f"{BASE_URL}/api/calendar/events/{event_id}/open-conversation", headers=owner_headers)
        assert r2.status_code == 400

    def test_final_attendance_gated_until_meeting_ends(self, api_client, owner_headers):
        past = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_PastMeeting", "category": "meeting", "start_date": "2026-06-01", "start_time": "09:00",
            "end_date": "2026-06-01", "end_time": "10:00", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert past.status_code == 200, past.text
        past_id = past.json()["id"]

        future = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_FutureMeeting", "category": "meeting", "start_date": "2027-06-01", "start_time": "09:00",
            "end_date": "2027-06-01", "end_time": "10:00", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert future.status_code == 200, future.text
        future_id = future.json()["id"]

        r_future = api_client.post(f"{BASE_URL}/api/calendar/events/{future_id}/participants/{TestSmartCalendar._emp_a_id}/attendance",
                                   json={"status": "attended"}, headers=owner_headers)
        assert r_future.status_code == 400

        r_past = api_client.post(f"{BASE_URL}/api/calendar/events/{past_id}/participants/{TestSmartCalendar._emp_a_id}/attendance",
                                 json={"status": "attended"}, headers=owner_headers)
        assert r_past.status_code == 200, r_past.text

        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{past_id}", headers=owner_headers).json()
        participant = next(p for p in detail["participants"] if p["participant_id"] == TestSmartCalendar._emp_a_id)
        assert participant["final_attendance"] == "attended"
        assert participant["attendance_status"] == "no_response"  # RSVP is untouched by the final-attendance mark

        activity = api_client.get(f"{BASE_URL}/api/owner/calendar/events/{past_id}/activity", headers=owner_headers).json()
        assert any(a["verb"] == "marked_attended" for a in activity)

    def test_final_attendance_invalid_status_rejected(self, api_client, owner_headers):
        past = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_PastMeeting2", "category": "meeting", "start_date": "2026-06-02", "start_time": "09:00",
            "end_date": "2026-06-02", "end_time": "10:00", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers).json()
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{past['id']}/participants/{TestSmartCalendar._emp_a_id}/attendance",
                            json={"status": "late"}, headers=owner_headers)
        assert r.status_code == 400

    def test_meeting_notes_stored_and_never_notify(self, api_client, owner_headers, employee_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_NotesMeeting", "category": "meeting", "start_date": "2026-08-17", "start_time": "09:00",
            "end_date": "2026-08-17", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        event_id = r.json()["id"]

        before = api_client.get(f"{BASE_URL}/api/notifications", headers=TestSmartCalendar._emp_a_headers).json()
        notes = api_client.put(f"{BASE_URL}/api/calendar/events/{event_id}/notes", json={
            "summary": "TEST_summary", "decisions": "TEST_decisions", "action_items": "TEST_actions",
        }, headers=owner_headers)
        assert notes.status_code == 200, notes.text
        after = api_client.get(f"{BASE_URL}/api/notifications", headers=TestSmartCalendar._emp_a_headers).json()
        assert len(after) == len(before)  # meeting notes must never generate a notification

        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{event_id}", headers=owner_headers).json()
        assert detail["meeting_notes"] == {"summary": "TEST_summary", "decisions": "TEST_decisions", "action_items": "TEST_actions"}

        # Activity log records that notes were updated, but never the content itself.
        activity = api_client.get(f"{BASE_URL}/api/owner/calendar/events/{event_id}/activity", headers=owner_headers).json()
        notes_entries = [a for a in activity if a["verb"] == "notes_updated"]
        assert len(notes_entries) == 1
        assert "TEST_summary" not in str(notes_entries[0])
        assert "TEST_decisions" not in str(notes_entries[0])

    def test_recurring_cancel_this_event_only_vs_entire_series(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_RecurCancel", "category": "meeting", "start_date": "2026-11-02", "start_time": "09:00",
            "end_date": "2026-11-02", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
            "recurrence_type": "weekly", "recurrence_interval": 1, "recurrence_end_type": "after_count", "recurrence_end_value": "3",
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        event_id = r.json()["id"]

        # This Event Only: cancels a single occurrence, the series survives untouched otherwise.
        c1 = api_client.post(f"{BASE_URL}/api/calendar/events/{event_id}/cancel?scope=this_event_only&occurrence_date=2026-11-09",
                             headers=owner_headers)
        assert c1.status_code == 200, c1.text
        listing = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2026-11-01&view_end=2026-11-30", headers=owner_headers).json()
        occs = [i for i in listing["items"] if i["id"] == event_id]
        assert [o["occurrence_date"] for o in occs] == ["2026-11-02", "2026-11-16"]
        detail = api_client.get(f"{BASE_URL}/api/calendar/events/{event_id}", headers=owner_headers)
        assert detail.status_code == 200
        assert detail.json()["display_status"] != "cancelled"  # series itself still active

        # Entire Series: the whole series (never deleted, just soft-cancelled).
        c2 = api_client.post(f"{BASE_URL}/api/calendar/events/{event_id}/cancel?scope=entire_series", headers=owner_headers)
        assert c2.status_code == 200, c2.text
        detail2 = api_client.get(f"{BASE_URL}/api/calendar/events/{event_id}", headers=owner_headers)
        assert detail2.status_code == 200  # still fetchable - never deleted
        assert detail2.json()["display_status"] == "cancelled"
        monitor = api_client.get(f"{BASE_URL}/api/owner/calendar/monitor", headers=owner_headers).json()
        assert any(i["id"] == event_id for i in monitor["items"])  # still present in the archive/monitor

    def test_cancel_rejects_this_and_future_scope(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_RejectScope", "category": "meeting", "start_date": "2026-11-03", "start_time": "09:00",
            "end_date": "2026-11-03", "end_time": "09:30", "recipient_type": "employee", "recipient_ids": [TestSmartCalendar._emp_a_id],
        }, headers=owner_headers).json()
        c = api_client.post(f"{BASE_URL}/api/calendar/events/{r['id']}/cancel?scope=this_and_future", headers=owner_headers)
        assert c.status_code == 400
        c2 = api_client.post(f"{BASE_URL}/api/calendar/events/{r['id']}/cancel?scope=not_a_real_scope", headers=owner_headers)
        assert c2.status_code == 400

    def test_cleanup(self, api_client, owner_headers):
        for emp_id in (TestSmartCalendar._emp_a_id, TestSmartCalendar._emp_b_id):
            if emp_id:
                api_client.delete(f"{BASE_URL}/api/owner/employees/{emp_id}", headers=owner_headers)


# ============ CRITICAL TASK ALERT SYSTEM ============
class TestCriticalTaskAlert:
    """Covers priority=critical + status=received, the dedicated /receive
    endpoint and the generic /status endpoint reused for "Start Task",
    heartbeat-driven pending_critical_tasks detection with self-healing
    alert_delivered_at + owner notification fan-out (delivered/received/
    started), duplicate-notification prevention, and assigned-employee-only
    security."""
    _emp_id = None
    _emp_headers = None
    _other_emp_id = None
    _other_emp_headers = None
    _task_id = None
    _task_title = None

    def test_setup_employees(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        for label in ("a", "b"):
            payload = {
                "email": f"TEST_crit_{label}_{unique}@example.com",
                "phone": f"057{uuid.uuid4().hex[:7]}",
                "password": "testpass123",
                "name": f"TEST_Critical_{label}",
                "role": "employee",
            }
            r = api_client.post(f"{BASE_URL}/api/owner/employees", json=payload, headers=owner_headers)
            assert r.status_code == 200, r.text
            login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": payload["email"], "password": "testpass123"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            if label == "a":
                TestCriticalTaskAlert._emp_id = r.json()["id"]
                TestCriticalTaskAlert._emp_headers = headers
            else:
                TestCriticalTaskAlert._other_emp_id = r.json()["id"]
                TestCriticalTaskAlert._other_emp_headers = headers

    def test_create_critical_task(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        TestCriticalTaskAlert._task_title = f"TEST_CriticalTask_{unique}"
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json={
            "title": TestCriticalTaskAlert._task_title, "description": "urgent", "priority": "critical",
            "assigned_to": TestCriticalTaskAlert._emp_id, "due_date": "2026-12-31", "requires_proof": False,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["priority"] == "critical"
        assert data["status"] == "new"
        assert data["alert_delivered_at"] is None
        assert data["received_at"] is None
        TestCriticalTaskAlert._task_id = data["id"]

    def test_heartbeat_detects_and_self_heals_delivery(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/heartbeat", headers=TestCriticalTaskAlert._emp_headers)
        assert r.status_code == 200
        pending = r.json()["pending_critical_tasks"]
        assert any(t["id"] == TestCriticalTaskAlert._task_id for t in pending)

        tasks = api_client.get(f"{BASE_URL}/api/employee/tasks", headers=TestCriticalTaskAlert._emp_headers).json()
        task = next(t for t in tasks if t["id"] == TestCriticalTaskAlert._task_id)
        assert task["alert_delivered_at"] is not None

        notifs = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        delivered = [n for n in notifs if n["type"] == "critical_task_delivered" and TestCriticalTaskAlert._task_title in n["message"]]
        assert len(delivered) == 1

    def test_heartbeat_does_not_duplicate_delivery_notification(self, api_client, owner_headers):
        api_client.post(f"{BASE_URL}/api/heartbeat", headers=TestCriticalTaskAlert._emp_headers)
        api_client.post(f"{BASE_URL}/api/heartbeat", headers=TestCriticalTaskAlert._emp_headers)
        notifs = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        delivered = [n for n in notifs if n["type"] == "critical_task_delivered" and TestCriticalTaskAlert._task_title in n["message"]]
        assert len(delivered) == 1

    def test_other_employee_cannot_receive(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/tasks/{TestCriticalTaskAlert._task_id}/receive",
                             headers=TestCriticalTaskAlert._other_emp_headers)
        assert r.status_code == 404  # not assigned to them

    def test_receive_non_critical_task_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json={
            "title": "TEST_NotCritical", "description": "x", "priority": "high",
            "assigned_to": TestCriticalTaskAlert._emp_id, "due_date": "2026-12-31", "requires_proof": False,
        }, headers=owner_headers)
        task_id = r.json()["id"]
        recv = api_client.post(f"{BASE_URL}/api/employee/tasks/{task_id}/receive", headers=TestCriticalTaskAlert._emp_headers)
        assert recv.status_code == 400
        api_client.delete(f"{BASE_URL}/api/owner/tasks/{task_id}", headers=owner_headers)

    def test_receive_transitions_and_notifies_owner(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/employee/tasks/{TestCriticalTaskAlert._task_id}/receive",
                             headers=TestCriticalTaskAlert._emp_headers)
        assert r.status_code == 200, r.text
        assert r.json()["received_at"]

        tasks = api_client.get(f"{BASE_URL}/api/employee/tasks", headers=TestCriticalTaskAlert._emp_headers).json()
        task = next(t for t in tasks if t["id"] == TestCriticalTaskAlert._task_id)
        assert task["status"] == "received"
        assert task["received_at"] is not None

        notifs = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "critical_task_received" and TestCriticalTaskAlert._task_title in n["message"] for n in notifs)

    def test_receive_again_fails(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/employee/tasks/{TestCriticalTaskAlert._task_id}/receive",
                             headers=TestCriticalTaskAlert._emp_headers)
        assert r.status_code == 400

    def test_heartbeat_excludes_received_task_from_queue(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/heartbeat", headers=TestCriticalTaskAlert._emp_headers)
        pending_ids = [t["id"] for t in r.json()["pending_critical_tasks"]]
        assert TestCriticalTaskAlert._task_id not in pending_ids

    def test_start_via_generic_status_stamps_started_and_notifies(self, api_client, owner_headers):
        r = api_client.put(f"{BASE_URL}/api/employee/tasks/{TestCriticalTaskAlert._task_id}/status",
                            json={"status": "in_progress"}, headers=TestCriticalTaskAlert._emp_headers)
        assert r.status_code == 200

        tasks = api_client.get(f"{BASE_URL}/api/employee/tasks", headers=TestCriticalTaskAlert._emp_headers).json()
        task = next(t for t in tasks if t["id"] == TestCriticalTaskAlert._task_id)
        assert task["status"] == "in_progress"
        assert task["started_at"] is not None

        notifs = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        assert any(n["type"] == "critical_task_started" and TestCriticalTaskAlert._task_title in n["message"] for n in notifs)

    def test_cleanup(self, api_client, owner_headers):
        # The critical task ended in_progress, which the existing "no delete
        # while in_progress" rule blocks - left as TEST_-prefixed residue,
        # same convention as calendar_events (soft-state only, never a hard
        # delete path). Only the employees created for this class are torn down.
        for emp_id in (TestCriticalTaskAlert._emp_id, TestCriticalTaskAlert._other_emp_id):
            if emp_id:
                api_client.delete(f"{BASE_URL}/api/owner/employees/{emp_id}", headers=owner_headers)


# ============ COMPANY HOLIDAY MANAGEMENT ============
class TestCompanyHolidayManagement:
    """Covers holiday CRUD (calendar_events with category=company_holiday),
    the is_active enable/disable toggle, weekly permanent-pattern creation
    with far-future recurrence expansion, owner-only access, and dashboard
    suppression of late/absent violations on a holiday date. Any permanent
    (never-ending) weekly holiday created here is cancelled within the same
    test method, not deferred to a later cleanup step, to keep the window
    where it could affect concurrently-running tests as short as possible."""
    _holiday_id = None

    def test_create_holiday(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        TestCompanyHolidayManagement._title = f"TEST_Holiday_{unique}"
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": TestCompanyHolidayManagement._title, "description": "test",
            "category": "company_holiday", "start_date": "2026-10-01", "end_date": "2026-10-02",
            "all_day": True, "visibility": "company", "recipient_type": "company",
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_active"] is True
        TestCompanyHolidayManagement._holiday_id = data["id"]

    def test_list_holidays_owner_only(self, api_client, owner_headers, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/calendar/holidays", headers=owner_headers)
        assert r.status_code == 200
        assert any(h["id"] == TestCompanyHolidayManagement._holiday_id for h in r.json())
        r2 = api_client.get(f"{BASE_URL}/api/calendar/holidays", headers=employee_headers)
        assert r2.status_code == 403

    def test_deactivate_reactivate(self, api_client, owner_headers, employee_headers):
        forbidden = api_client.post(f"{BASE_URL}/api/calendar/events/{TestCompanyHolidayManagement._holiday_id}/deactivate", headers=employee_headers)
        assert forbidden.status_code == 403

        d = api_client.post(f"{BASE_URL}/api/calendar/events/{TestCompanyHolidayManagement._holiday_id}/deactivate", headers=owner_headers)
        assert d.status_code == 200
        assert d.json()["is_active"] is False

        react = api_client.post(f"{BASE_URL}/api/calendar/events/{TestCompanyHolidayManagement._holiday_id}/reactivate", headers=owner_headers)
        assert react.status_code == 200
        assert react.json()["is_active"] is True

    def test_deactivate_non_holiday_category_rejected(self, api_client, owner_headers):
        meeting = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_NotAHoliday", "category": "meeting", "start_date": "2026-10-05", "start_time": "09:00",
            "end_date": "2026-10-05", "end_time": "09:30", "recipient_type": "owner",
        }, headers=owner_headers).json()
        r = api_client.post(f"{BASE_URL}/api/calendar/events/{meeting['id']}/deactivate", headers=owner_headers)
        assert r.status_code == 400
        api_client.post(f"{BASE_URL}/api/calendar/events/{meeting['id']}/cancel?scope=entire_series", headers=owner_headers)

    def test_weekly_pattern_creates_permanent_recurrence(self, api_client, owner_headers):
        unique = uuid.uuid4().hex[:8]
        title = f"TEST_WeeklyHoliday_{unique}"
        r = api_client.post(f"{BASE_URL}/api/calendar/holidays/weekly-pattern", json={
            "weekdays": [5, 6], "title": title, "description": "",
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert len(created) == 2
        weekly_ids = [c["id"] for c in created]
        try:
            for c in created:
                assert c["recurrence_type"] == "weekly"
                assert c["recurrence_end_type"] == "never"
                assert c["category"] == "company_holiday"

            # A year-out window must still show occurrences - proves the
            # recurrence isn't silently bounded to a near-term window.
            far = api_client.get(f"{BASE_URL}/api/calendar/events?view_start=2027-06-01&view_end=2027-06-30", headers=owner_headers)
            assert far.status_code == 200
            far_ids = {i["id"] for i in far.json()["items"]}
            assert set(weekly_ids) & far_ids
        finally:
            # Permanent recurring holiday - must not survive past this test.
            for wid in weekly_ids:
                api_client.post(f"{BASE_URL}/api/calendar/events/{wid}/cancel?scope=entire_series", headers=owner_headers)

    def test_invalid_weekday_rejected(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/calendar/holidays/weekly-pattern", json={"weekdays": [9], "title": "x"}, headers=owner_headers)
        assert r.status_code == 400
        r2 = api_client.post(f"{BASE_URL}/api/calendar/holidays/weekly-pattern", json={"weekdays": [], "title": "x"}, headers=owner_headers)
        assert r2.status_code == 400

    def test_today_holiday_suppresses_dashboard_violations(self, api_client, owner_headers, employee_headers):
        today = datetime.now(timezone.utc).date().isoformat()
        r = api_client.post(f"{BASE_URL}/api/calendar/events", json={
            "title": "TEST_TodayHolidayDash", "category": "company_holiday",
            "start_date": today, "end_date": today, "all_day": True,
            "visibility": "company", "recipient_type": "company", "override_conflicts": True,
        }, headers=owner_headers)
        assert r.status_code == 200, r.text
        holiday_id = r.json()["id"]
        try:
            dash = api_client.get(f"{BASE_URL}/api/owner/dashboard", headers=owner_headers).json()
            assert dash["today_is_holiday"] is True
            assert dash["today_holiday_title"] == "TEST_TodayHolidayDash"
            assert dash["late_today"] == 0
            assert dash["absent_today"] == 0

            emp_dash = api_client.get(f"{BASE_URL}/api/employee/dashboard", headers=employee_headers).json()
            assert emp_dash["today_is_holiday"] is True
            assert emp_dash["today_holiday_title"] == "TEST_TodayHolidayDash"
        finally:
            api_client.post(f"{BASE_URL}/api/calendar/events/{holiday_id}/cancel?scope=entire_series", headers=owner_headers)

    def test_cleanup(self, api_client, owner_headers):
        api_client.post(f"{BASE_URL}/api/calendar/events/{TestCompanyHolidayManagement._holiday_id}/cancel?scope=entire_series", headers=owner_headers)


class TestDeviceTokens:
    """Covers push-notification device registration: register/refresh
    (upsert-by-token, same row updated in place), reassignment when a
    different account registers the same token (same physical device,
    different login - the row's owner changes rather than a duplicate
    being created), ownership-scoped listing/deletion, platform
    validation, and the super_admin debug-listing endpoint. Rows are
    hard-deleted (models.DeviceToken has no soft-delete column), so every
    token registered here is explicitly unregistered before the test ends
    rather than left as permanent debris."""

    def test_register_creates_and_refresh_updates_in_place(self, api_client, employee_headers):
        unique = uuid.uuid4().hex[:12]
        token = f"TEST_fcm_token_{unique}"
        r = api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "android"}, headers=employee_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["platform"] == "android"
        assert data["app_version"] is None
        device_id = data["id"]

        try:
            # Refresh: same token, new app_version - updates the existing
            # row (same id), never creates a second one.
            r2 = api_client.post(
                f"{BASE_URL}/api/devices", json={"token": token, "platform": "android", "app_version": "1.2.3"}, headers=employee_headers
            )
            assert r2.status_code == 200
            assert r2.json()["id"] == device_id
            assert r2.json()["app_version"] == "1.2.3"

            listed = api_client.get(f"{BASE_URL}/api/devices", headers=employee_headers).json()
            assert sum(1 for d in listed if d["id"] == device_id) == 1
        finally:
            api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=employee_headers)

    def test_invalid_platform_rejected(self, api_client, employee_headers):
        r = api_client.post(f"{BASE_URL}/api/devices", json={"token": "TEST_invalid_platform", "platform": "windows"}, headers=employee_headers)
        assert r.status_code == 422

    def test_reassignment_on_relogin_different_account(self, api_client, employee_headers, owner_headers):
        unique = uuid.uuid4().hex[:12]
        token = f"TEST_fcm_shared_device_{unique}"
        r1 = api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "ios"}, headers=employee_headers)
        assert r1.status_code == 200
        device_id = r1.json()["id"]

        try:
            emp_list = api_client.get(f"{BASE_URL}/api/devices", headers=employee_headers).json()
            assert any(d["id"] == device_id for d in emp_list)

            # Same physical device, a different account logs in and
            # registers the same token - reassigns the existing row.
            r2 = api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "ios"}, headers=owner_headers)
            assert r2.status_code == 200
            assert r2.json()["id"] == device_id

            emp_list_after = api_client.get(f"{BASE_URL}/api/devices", headers=employee_headers).json()
            assert not any(d["id"] == device_id for d in emp_list_after)
            owner_list = api_client.get(f"{BASE_URL}/api/devices", headers=owner_headers).json()
            assert any(d["id"] == device_id for d in owner_list)
        finally:
            api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=owner_headers)

    def test_delete_scoped_to_owner(self, api_client, employee_headers, owner_headers):
        unique = uuid.uuid4().hex[:12]
        token = f"TEST_fcm_owned_by_employee_{unique}"
        r = api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "android"}, headers=employee_headers)
        assert r.status_code == 200

        # A different account can't delete a token it doesn't own, even
        # knowing the exact token string.
        forbidden = api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=owner_headers)
        assert forbidden.status_code == 404

        ok = api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=employee_headers)
        assert ok.status_code == 200

        already_gone = api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=employee_headers)
        assert already_gone.status_code == 404

    def test_admin_can_list_user_devices_forbidden_for_others(self, api_client, employee_headers, owner_headers, admin_headers, employee_user):
        unique = uuid.uuid4().hex[:12]
        token = f"TEST_fcm_admin_view_{unique}"
        api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "web"}, headers=employee_headers)

        try:
            r = api_client.get(f"{BASE_URL}/api/admin/users/{employee_user['id']}/devices", headers=admin_headers)
            assert r.status_code == 200
            assert any(d["platform"] == "web" for d in r.json())

            forbidden = api_client.get(f"{BASE_URL}/api/admin/users/{employee_user['id']}/devices", headers=owner_headers)
            assert forbidden.status_code == 403
        finally:
            api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=employee_headers)

    def test_publish_still_succeeds_with_a_device_registered(self, api_client, employee_headers, owner_headers, employee_user):
        """publish() (services/notifications.py) now also calls
        services.push.send_push_to_user() - this proves that call path
        doesn't break notification-producing actions even with a real
        token on file and no Firebase credentials configured (push
        degrades to a silent no-op, per the file's own docstring)."""
        unique = uuid.uuid4().hex[:12]
        token = f"TEST_fcm_publish_smoke_{unique}"
        api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": "android"}, headers=employee_headers)

        try:
            r = api_client.post(f"{BASE_URL}/api/owner/tasks", json={
                "title": "TEST_PushSmokeTask", "description": "verifies publish() tolerates a registered device token",
                "priority": "low", "assigned_to": employee_user["id"], "due_date": "2030-01-01",
            }, headers=owner_headers)
            assert r.status_code == 200, r.text
            task_id = r.json()["id"]
            assert r.json()["status"] == "new"
            api_client.delete(f"{BASE_URL}/api/owner/tasks/{task_id}", headers=owner_headers)
        finally:
            api_client.delete(f"{BASE_URL}/api/devices", params={"token": token}, headers=employee_headers)
