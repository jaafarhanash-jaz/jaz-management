"""Live HTTP integration tests for Surprise Attendance (Part 9) - same
style as tests/test_employee_groups.py: real requests against a running
backend, using the session-scoped admin/owner/employee fixtures.
"""
import uuid

import pytest

from tests.conftest import BASE_URL, auth_headers


@pytest.fixture(scope="module")
def other_company_owner_headers(api_client, admin_token):
    """A second, distinct company/owner - tenant-isolation tests must never
    reach across this boundary."""
    admin_hdrs = auth_headers(admin_token)
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_Plan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_hdrs,
    ).json()
    owner_email = f"TEST_surpriseowner_{unique}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_SurpriseOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Owner", "owner_password": "pass1234", "owner_phone": f"056{unique[:7]}",
            "subscription_plan_id": plan["id"],
        },
        headers=admin_hdrs,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


class TestCreateSurpriseAttendance:
    def test_target_everyone(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "everyone", "require_photo": False},
            headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_mode"] == "everyone"
        assert body["requested_count"] >= 1
        assert body["status"] == "pending"

    def test_target_selected_employees(self, api_client, owner_headers, employee_user):
        r = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": True},
            headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requested_count"] == 1
        assert body["require_photo"] is True

    def test_empty_target_list_rejected(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [], "require_photo": False},
            headers=owner_headers,
        )
        assert r.status_code == 400

    def test_employee_forbidden(self, api_client, employee_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "everyone", "require_photo": False},
            headers=employee_headers,
        )
        assert r.status_code == 403

    def test_cross_tenant_employee_id_rejected(self, api_client, owner_headers, other_company_owner_headers, employee_user):
        r = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=other_company_owner_headers,
        )
        assert r.status_code == 400, r.text


class TestListAndDetail:
    def test_list_and_detail_shapes(self, api_client, owner_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()

        listed = api_client.get(f"{BASE_URL}/api/owner/surprise-attendance", headers=owner_headers)
        assert listed.status_code == 200
        assert any(r["id"] == created["id"] for r in listed.json())

        detail = api_client.get(f"{BASE_URL}/api/owner/surprise-attendance/{created['id']}", headers=owner_headers)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert len(body["responses"]) == 1
        assert body["responses"][0]["employee_id"] == employee_user["id"]
        assert body["responses"][0]["status"] == "pending"

    def test_cross_tenant_detail_returns_404(self, api_client, owner_headers, other_company_owner_headers):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "everyone", "require_photo": False},
            headers=owner_headers,
        ).json()
        r = api_client.get(f"{BASE_URL}/api/owner/surprise-attendance/{created['id']}", headers=other_company_owner_headers)
        assert r.status_code == 404


class TestEmployeeRespond:
    def test_pending_list_shows_targeted_request(self, api_client, owner_headers, employee_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()

        pending = api_client.get(f"{BASE_URL}/api/employee/surprise-attendance/pending", headers=employee_headers)
        assert pending.status_code == 200
        assert any(r["id"] == created["id"] for r in pending.json())

    def test_respond_without_zone_or_photo_required_succeeds(self, api_client, owner_headers, employee_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()

        r = api_client.post(
            f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond",
            json={}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"

        # Request auto-completes once every targeted employee has responded.
        detail = api_client.get(f"{BASE_URL}/api/owner/surprise-attendance/{created['id']}", headers=owner_headers).json()
        assert detail["status"] == "completed"
        assert detail["responded_count"] == 1

    def test_respond_twice_rejected(self, api_client, owner_headers, employee_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()
        api_client.post(f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond", json={}, headers=employee_headers)
        r = api_client.post(f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond", json={}, headers=employee_headers)
        assert r.status_code == 400

    def test_photo_required_but_not_provided_rejected(self, api_client, owner_headers, employee_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": True},
            headers=owner_headers,
        ).json()
        r = api_client.post(f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond", json={}, headers=employee_headers)
        assert r.status_code == 400

    def test_non_targeted_employee_cannot_respond(self, api_client, owner_headers, employee_headers):
        # A request targeted at "everyone" via a fresh selected_employees
        # list that excludes this employee - respond must 404, not
        # silently accept a response from someone never asked.
        other_id = str(uuid.uuid4())
        # Use owner's own id (a company_owner, never an employee target)
        # is invalid for selected_employees, so instead target nobody
        # this employee_headers user is in by targeting a throwaway group-
        # style id is not possible without a real employee; simplest
        # reliable non-member is a request scoped to a distinct new
        # employee created just for this test.
        new_emp = api_client.post(
            f"{BASE_URL}/api/owner/employees",
            json={"email": f"TEST_surprise_other_{uuid.uuid4().hex[:8]}@example.com",
                  "phone": f"050{uuid.uuid4().hex[:7]}", "password": "testpass123",
                  "name": "TEST_Surprise_Other", "role": "employee"},
            headers=owner_headers,
        ).json()
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [new_emp["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()
        r = api_client.post(f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond", json={}, headers=employee_headers)
        assert r.status_code == 404

    def test_employee_role_required_to_respond(self, api_client, owner_headers, employee_user):
        created = api_client.post(
            f"{BASE_URL}/api/owner/surprise-attendance",
            json={"target_mode": "selected_employees", "target_ids": [employee_user["id"]], "require_photo": False},
            headers=owner_headers,
        ).json()
        r = api_client.post(f"{BASE_URL}/api/employee/surprise-attendance/{created['id']}/respond", json={}, headers=owner_headers)
        assert r.status_code == 403
