"""Live HTTP integration tests for Work Locations and Attendance Groups /
Assignments - same style as tests/test_company_notifications.py: real
requests against a running backend, using the session-scoped
admin/owner/employee fixtures. Each test creates its own uniquely-named
location/group so parallel workers (pytest.ini's `-n 2 --dist loadscope`)
never collide.
"""
import uuid

import pytest

from tests.conftest import BASE_URL, auth_headers


def _unique(prefix):
    return f"TEST_{prefix}_{uuid.uuid4().hex[:8]}"


def _create_location(api_client, owner_headers, **overrides):
    body = {"name": _unique("Loc"), "latitude": 33.31, "longitude": 44.43, "radius_meters": 75, **overrides}
    r = api_client.post(f"{BASE_URL}/api/owner/work-locations", json=body, headers=owner_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _create_fresh_employee(api_client, owner_headers):
    """A brand-new employee, isolated from the shared session-scoped
    employee_user fixture - needed for exact-membership-set assertions,
    since employee_user is reused (and accumulates group memberships)
    across every test in this module."""
    unique = uuid.uuid4().hex[:8]
    r = api_client.post(
        f"{BASE_URL}/api/owner/employees",
        json={"email": f"TEST_freshemp_{unique}@example.com", "phone": f"051{unique[:7]}",
              "password": "testpass123", "name": f"TEST_FreshEmp_{unique}", "role": "employee"},
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_group(api_client, owner_headers, **overrides):
    body = {
        "name": _unique("Group"), "require_zone": True, "require_qr": True,
        "photo_proof_required": False, "grace_period_minutes": 180, "location_ids": [],
        **overrides,
    }
    r = api_client.post(f"{BASE_URL}/api/owner/attendance-groups", json=body, headers=owner_headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def other_company_owner_headers(api_client, admin_token):
    """A second, distinct company/owner - tenant-isolation tests must never
    reach across this boundary. Mirrors backend_test.py's
    TestWorkMessaging.test_setup_other_company pattern. Builds headers from
    the session-scoped `admin_token` directly (not the function-scoped
    `admin_headers`) since this fixture is itself module-scoped."""
    admin_hdrs = auth_headers(admin_token)
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_Plan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_hdrs,
    ).json()
    owner_email = f"TEST_groupsowner_{unique}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_GroupsOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Owner", "owner_password": "pass1234", "owner_phone": f"057{unique[:7]}",
            "subscription_plan_id": plan["id"],
        },
        headers=admin_hdrs,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


class TestWorkLocations:
    def test_create_list_update(self, api_client, owner_headers):
        location = _create_location(api_client, owner_headers)
        assert location["qr_code"].startswith("data:image/png;base64,")
        assert location["is_active"] is True

        listed = api_client.get(f"{BASE_URL}/api/owner/work-locations", headers=owner_headers).json()
        assert any(loc["id"] == location["id"] for loc in listed)

        updated = api_client.put(
            f"{BASE_URL}/api/owner/work-locations/{location['id']}",
            json={"name": "Renamed", "latitude": 1.0, "longitude": 2.0, "radius_meters": 200},
            headers=owner_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["radius_meters"] == 200

    def test_qr_regenerate_changes_token(self, api_client, owner_headers):
        location = _create_location(api_client, owner_headers)
        original_token = location["qr_token"]
        r = api_client.post(f"{BASE_URL}/api/owner/work-locations/{location['id']}/qr/regenerate", headers=owner_headers)
        assert r.status_code == 200, r.text
        assert r.json()["qr_code"] != location["qr_code"]

    def test_invalid_coordinates_rejected(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-locations",
            json={"name": "Bad", "latitude": 999, "longitude": 0},
            headers=owner_headers,
        )
        assert r.status_code == 400, r.text

    def test_deactivate_blocked_while_linked_to_active_group(self, api_client, owner_headers):
        location = _create_location(api_client, owner_headers)
        group = _create_group(api_client, owner_headers, location_ids=[location["id"]])

        r = api_client.post(f"{BASE_URL}/api/owner/work-locations/{location['id']}/deactivate", headers=owner_headers)
        assert r.status_code == 400, r.text

        # Unlink, then deactivate should succeed.
        api_client.put(
            f"{BASE_URL}/api/owner/attendance-groups/{group['id']}",
            json={**group, "location_ids": []}, headers=owner_headers,
        )
        r = api_client.post(f"{BASE_URL}/api/owner/work-locations/{location['id']}/deactivate", headers=owner_headers)
        assert r.status_code == 200, r.text

    def test_employee_forbidden(self, api_client, employee_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-locations",
            json={"name": "Nope", "latitude": 0, "longitude": 0}, headers=employee_headers,
        )
        assert r.status_code == 403

    def test_cross_tenant_update_returns_404(self, api_client, owner_headers, other_company_owner_headers):
        location = _create_location(api_client, owner_headers)
        r = api_client.put(
            f"{BASE_URL}/api/owner/work-locations/{location['id']}",
            json={"name": "Hijacked", "latitude": 0, "longitude": 0}, headers=other_company_owner_headers,
        )
        assert r.status_code == 404, r.text

    def test_cross_tenant_qr_regenerate_returns_404(self, api_client, owner_headers, other_company_owner_headers):
        location = _create_location(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/owner/work-locations/{location['id']}/qr/regenerate",
            headers=other_company_owner_headers,
        )
        assert r.status_code == 404, r.text


class TestAttendanceGroups:
    def test_create_with_independent_qr_gps_photo_flags(self, api_client, owner_headers):
        location = _create_location(api_client, owner_headers)
        group = _create_group(
            api_client, owner_headers, require_zone=True, require_qr=False,
            photo_proof_required=True, grace_period_minutes=90, location_ids=[location["id"]],
        )
        assert group["require_zone"] is True
        assert group["require_qr"] is False
        assert group["photo_proof_required"] is True
        assert group["grace_period_minutes"] == 90
        assert group["location_ids"] == [location["id"]]

    @pytest.mark.parametrize("require_zone,require_qr", [(True, True), (True, False), (False, True), (False, False)])
    def test_all_four_qr_gps_combinations_are_storable(self, api_client, owner_headers, require_zone, require_qr):
        group = _create_group(api_client, owner_headers, require_zone=require_zone, require_qr=require_qr)
        assert group["require_zone"] == require_zone
        assert group["require_qr"] == require_qr

    def test_negative_grace_period_rejected(self, api_client, owner_headers):
        r = api_client.post(
            f"{BASE_URL}/api/owner/attendance-groups",
            json={"name": _unique("Bad"), "grace_period_minutes": -1, "location_ids": []},
            headers=owner_headers,
        )
        assert r.status_code == 400, r.text

    def test_cross_tenant_location_link_rejected(self, api_client, owner_headers, other_company_owner_headers):
        location = _create_location(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/owner/attendance-groups",
            json={"name": _unique("CrossTenant"), "location_ids": [location["id"]]},
            headers=other_company_owner_headers,
        )
        assert r.status_code == 400, r.text

    def test_cross_tenant_update_returns_404(self, api_client, owner_headers, other_company_owner_headers):
        group = _create_group(api_client, owner_headers)
        r = api_client.put(
            f"{BASE_URL}/api/owner/attendance-groups/{group['id']}",
            json={**group, "name": "Hijacked"}, headers=other_company_owner_headers,
        )
        assert r.status_code == 404, r.text

    def test_employee_forbidden(self, api_client, employee_headers):
        r = api_client.get(f"{BASE_URL}/api/owner/attendance-groups", headers=employee_headers)
        assert r.status_code == 403


class TestEmployeeGroupMembership:
    """An employee may belong to MULTIPLE Attendance Groups at once (e.g.
    "Sales Representatives" AND "Al-Bayaa Branch" simultaneously) -
    EmployeeGroupAssignment is the sole authoritative membership
    relationship, never a single scalar on User."""

    def test_add_updates_membership_and_history(self, api_client, owner_headers, employee_user):
        group = _create_group(api_client, owner_headers)
        employee_id = employee_user["id"]

        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-groups/{group['id']}", headers=owner_headers,
        )
        assert r.status_code == 200, r.text

        members = api_client.get(
            f"{BASE_URL}/api/owner/attendance-groups/{group['id']}/members", headers=owner_headers,
        ).json()
        assert any(m["id"] == employee_id for m in members)

        history = api_client.get(
            f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-group/history", headers=owner_headers,
        ).json()
        assert history[0]["group_id"] == group["id"]
        assert history[0]["effective_to"] is None

        # Remove - the entry must CLOSE (effective_to set), never
        # disappear (Part 9's "preserve historical assignment records").
        r = api_client.delete(
            f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-groups/{group['id']}", headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(
            f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-group/history", headers=owner_headers,
        ).json()
        assert history[0]["group_id"] == group["id"]
        assert history[0]["effective_to"] is not None

    def test_scenario_a_single_group_membership(self, api_client, owner_headers):
        employee = _create_fresh_employee(api_client, owner_headers)
        group = _create_group(api_client, owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group['id']}", headers=owner_headers)

        current = api_client.get(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups", headers=owner_headers).json()
        assert [g["id"] for g in current] == [group["id"]]

    def test_scenario_b_two_simultaneous_group_memberships(self, api_client, owner_headers):
        employee = _create_fresh_employee(api_client, owner_headers)
        group_a = _create_group(api_client, owner_headers)
        group_b = _create_group(api_client, owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group_a['id']}", headers=owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group_b['id']}", headers=owner_headers)

        current = api_client.get(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups", headers=owner_headers).json()
        assert {g["id"] for g in current} == {group_a["id"], group_b["id"]}

    def test_duplicate_membership_rejected(self, api_client, owner_headers):
        # A fresh, disposable employee - not the shared employee_user
        # fixture - since this test's added membership is never removed
        # (unlike test_add_updates_membership_and_history above), and the
        # shared fixture must stay group-free for OTHER test modules
        # (e.g. tests/test_surprise_attendance.py) that assume it has no
        # zone/QR requirement.
        employee = _create_fresh_employee(api_client, owner_headers)
        group = _create_group(api_client, owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group['id']}", headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group['id']}", headers=owner_headers)
        assert r.status_code == 400, r.text

    def test_scenario_h_removing_one_group_preserves_the_other(self, api_client, owner_headers):
        employee = _create_fresh_employee(api_client, owner_headers)
        group_a = _create_group(api_client, owner_headers)
        group_b = _create_group(api_client, owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group_a['id']}", headers=owner_headers)
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group_b['id']}", headers=owner_headers)

        r = api_client.delete(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group_a['id']}", headers=owner_headers)
        assert r.status_code == 200, r.text

        current = api_client.get(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups", headers=owner_headers).json()
        assert [g["id"] for g in current] == [group_b["id"]]

        # Group B's own membership history is untouched by removing A.
        history = api_client.get(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-group/history", headers=owner_headers).json()
        b_entry = next(h for h in history if h["group_id"] == group_b["id"])
        assert b_entry["effective_to"] is None

    def test_remove_when_not_a_member_rejected(self, api_client, owner_headers, employee_user):
        group = _create_group(api_client, owner_headers)
        r = api_client.delete(f"{BASE_URL}/api/owner/employees/{employee_user['id']}/attendance-groups/{group['id']}", headers=owner_headers)
        assert r.status_code == 400, r.text

    def test_deactivate_blocked_while_group_has_members(self, api_client, owner_headers, employee_user):
        group = _create_group(api_client, owner_headers)
        employee_id = employee_user["id"]
        api_client.post(f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-groups/{group['id']}", headers=owner_headers)

        r = api_client.post(f"{BASE_URL}/api/owner/attendance-groups/{group['id']}/deactivate", headers=owner_headers)
        assert r.status_code == 400, r.text

        api_client.delete(f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-groups/{group['id']}", headers=owner_headers)
        r = api_client.post(f"{BASE_URL}/api/owner/attendance-groups/{group['id']}/deactivate", headers=owner_headers)
        assert r.status_code == 200, r.text

    def test_cross_tenant_add_returns_400(self, api_client, owner_headers, other_company_owner_headers, employee_user):
        """Scenario G: Company B owner must not be able to assign Company
        A's employee to any group at all."""
        group = _create_group(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{employee_user['id']}/attendance-groups/{group['id']}",
            headers=other_company_owner_headers,
        )
        assert r.status_code == 404, r.text  # employee not found in the caller's own company

    def test_cross_tenant_group_id_rejected(self, api_client, owner_headers, other_company_owner_headers):
        """Scenario G, other direction: Company B owner has their own real
        employee but tries to assign them to Company A's group id."""
        group_a = _create_group(api_client, owner_headers)
        new_emp = api_client.post(
            f"{BASE_URL}/api/owner/employees",
            json={"email": f"TEST_xtenant_{uuid.uuid4().hex[:8]}@example.com", "phone": f"054{uuid.uuid4().hex[:7]}",
                  "password": "testpass123", "name": "TEST_XTenant", "role": "employee"},
            headers=other_company_owner_headers,
        ).json()
        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{new_emp['id']}/attendance-groups/{group_a['id']}",
            headers=other_company_owner_headers,
        )
        assert r.status_code == 400, r.text

    def test_cross_tenant_list_returns_404(self, api_client, owner_headers, other_company_owner_headers, employee_user):
        r = api_client.get(
            f"{BASE_URL}/api/owner/employees/{employee_user['id']}/attendance-groups",
            headers=other_company_owner_headers,
        )
        assert r.status_code == 404, r.text
