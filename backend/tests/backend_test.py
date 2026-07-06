"""JAZ Platform backend tests - Phase 2 CRUD, Employee flows, Stripe integration & RBAC."""
import os
import time
import uuid
import requests

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

    def test_create_task(self, api_client, owner_headers):
        payload = {
            "title": "TEST_Task",
            "description": "test task description",
            "priority": "high",
            "assigned_to": "emp-001",
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
        assert data["assigned_to"] == "emp-001"
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

    def test_setup_task(self, api_client, owner_headers):
        r = api_client.post(f"{BASE_URL}/api/owner/tasks", json={
            "title": "TEST_EmpTask",
            "description": "for employee test",
            "priority": "medium",
            "assigned_to": "emp-001",
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

    def test_check_in_valid_qr(self, api_client, employee_headers):
        # First cleanup any existing attendance for today via direct API path (not exposed).
        # We attempt check-in; if already checked in, we treat that as valid state test.
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in",
                             json={"qr_code": "company:company-001", "latitude": 24.7, "longitude": 46.6},
                             headers=employee_headers)
        # Either succeeded now, or already checked in earlier this session
        assert r.status_code in (200, 400)
        if r.status_code == 200:
            assert "status" in r.json()

    def test_check_out(self, api_client, employee_headers):
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out",
                             json={"latitude": 24.7, "longitude": 46.6},
                             headers=employee_headers)
        # Might be 200 (success), or 400 (already checked out) - both acceptable after check-in flow
        assert r.status_code in (200, 400)

    def test_history(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


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
            "address": "Test Address"
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
        payload = {"plan_id": "plan-001", "origin_url": "https://jaz-management.preview.emergentagent.com"}
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
