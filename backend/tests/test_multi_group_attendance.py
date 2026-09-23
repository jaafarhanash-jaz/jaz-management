"""Live HTTP integration tests for multi-group attendance resolution -
an employee may belong to MULTIPLE Attendance Groups at once (e.g.
"Sales Representatives" AND "Al-Bayaa Branch" simultaneously, per the
approved spec's own example). Covers the specific scenarios called out
in the 2026-09-01 architecture audit: A-I plus tenant isolation.
"""
import uuid

import pytest

from tests.conftest import BASE_URL, auth_headers, _login


def _unique(prefix):
    return f"TEST_{prefix}_{uuid.uuid4().hex[:8]}"


def _create_location(api_client, owner_headers, lat, lon):
    r = api_client.post(
        f"{BASE_URL}/api/owner/work-locations",
        json={"name": _unique("Loc"), "latitude": lat, "longitude": lon, "radius_meters": 100},
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_group(api_client, owner_headers, *, location_ids, require_qr, require_zone):
    r = api_client.post(
        f"{BASE_URL}/api/owner/attendance-groups",
        json={
            "name": _unique("Group"), "require_zone": require_zone, "require_qr": require_qr,
            "photo_proof_required": False, "grace_period_minutes": 180, "location_ids": location_ids,
        },
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_fresh_employee(api_client, owner_headers):
    unique = uuid.uuid4().hex[:8]
    email = f"TEST_mgroup_{unique}@example.com"
    password = "testpass123"
    r = api_client.post(
        f"{BASE_URL}/api/owner/employees",
        json={"email": email, "phone": f"053{unique[:7]}", "password": password, "name": f"TEST_MGroup_{unique}", "role": "employee"},
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    employee = r.json()
    login = _login(api_client, email, password)
    return employee, auth_headers(login["token"])


def _add_membership(api_client, owner_headers, employee_id, group_id):
    r = api_client.post(f"{BASE_URL}/api/owner/employees/{employee_id}/attendance-groups/{group_id}", headers=owner_headers)
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def other_company_owner_headers(api_client, admin_token):
    admin_hdrs = auth_headers(admin_token)
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_Plan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_hdrs,
    ).json()
    owner_email = f"TEST_mgroupowner_{unique}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_MGroupOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Owner", "owner_password": "pass1234", "owner_phone": f"052{unique[:7]}",
            "subscription_plan_id": plan["id"],
        },
        headers=admin_hdrs,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


class TestSingleAndMultiGroupCheckIn:
    def test_scenario_a_single_group_auto_resolves(self, api_client, owner_headers):
        """A: employee belongs to one group - check-in resolves it with
        zero friction, no group_id needed."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc = _create_location(api_client, owner_headers, 10.0, 10.0)
        group = _create_group(api_client, owner_headers, location_ids=[loc["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group["id"])

        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] in ("present", "late")

    def _setup_two_groups_different_locations(self, api_client, owner_headers):
        """C: employee belongs to two groups with different locations."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc_a = _create_location(api_client, owner_headers, 20.0, 20.0)
        loc_b = _create_location(api_client, owner_headers, 60.0, 60.0)
        group_a = _create_group(api_client, owner_headers, location_ids=[loc_a["id"]], require_qr=True, require_zone=False)
        group_b = _create_group(api_client, owner_headers, location_ids=[loc_b["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])
        return employee_headers, loc_a, loc_b, group_a, group_b

    def test_scenario_d_matches_group_a_qr_resolves_to_group_a(self, api_client, owner_headers):
        employee_headers, loc_a, loc_b, group_a, group_b = self._setup_two_groups_different_locations(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc_a["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_a["id"]

    def test_scenario_e_matches_group_b_qr_resolves_to_group_b(self, api_client, owner_headers):
        # Independent employee (own fresh setup, not reusing D's employee)
        # specifically so this doesn't run into the pre-existing, entirely
        # unrelated "one check-in cycle per calendar day" constraint -
        # this test's only job is proving Group B's own QR resolves to
        # Group B, same as D proves for Group A.
        employee_headers, loc_a, loc_b, group_a, group_b = self._setup_two_groups_different_locations(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc_b["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_b["id"]

    def test_scenario_f_qr_from_unassigned_group_rejected(self, api_client, owner_headers):
        """F: employee attempts attendance using a QR/location belonging
        to a group they are NOT assigned to - must be rejected, not
        silently accepted under whatever group they DO belong to."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        my_loc = _create_location(api_client, owner_headers, 30.0, 30.0)
        other_loc = _create_location(api_client, owner_headers, 70.0, 70.0)
        my_group = _create_group(api_client, owner_headers, location_ids=[my_loc["id"]], require_qr=True, require_zone=False)
        _create_group(api_client, owner_headers, location_ids=[other_loc["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], my_group["id"])

        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": other_loc["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 400, r.text

    def test_ambiguous_multiple_matching_groups_rejected_with_409(self, api_client, owner_headers):
        """Neither group can be distinguished by the presented QR/GPS (both
        require neither) - the system must NEVER silently pick one."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        group_a = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        group_b = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])

        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-in", json={}, headers=employee_headers)
        assert r.status_code == 409, r.text

    def test_explicit_group_id_resolves_ambiguous_case(self, api_client, owner_headers):
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        group_a = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        group_b = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])

        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in", json={"group_id": group_a["id"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_a["id"]

    def test_explicit_group_id_not_a_membership_rejected(self, api_client, owner_headers):
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        member_group = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        not_a_member_group = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], member_group["id"])

        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"group_id": not_a_member_group["id"]}, headers=employee_headers,
        )
        assert r.status_code == 400, r.text

    def test_checkout_uses_sessions_own_group_not_disambiguation(self, api_client, owner_headers):
        """Check-out must validate against the SPECIFIC group snapshotted
        at check-in, never re-run multi-group disambiguation (there's
        nothing to disambiguate - the session is already attributed)."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc_a = _create_location(api_client, owner_headers, 25.0, 25.0)
        loc_b = _create_location(api_client, owner_headers, 65.0, 65.0)
        group_a = _create_group(api_client, owner_headers, location_ids=[loc_a["id"]], require_qr=True, require_zone=False)
        group_b = _create_group(api_client, owner_headers, location_ids=[loc_b["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])

        api_client.post(f"{BASE_URL}/api/employee/attendance/check-in", json={"qr_code": loc_a["qr_token"]}, headers=employee_headers)
        # Check out with Group A's own QR (the session's actual group) -
        # must succeed even though the employee also belongs to Group B.
        r = api_client.post(f"{BASE_URL}/api/employee/attendance/check-out", json={"qr_code": loc_a["qr_token"]}, headers=employee_headers)
        assert r.status_code == 200, r.text

    def test_tenant_isolation_cannot_use_other_companys_location_qr(self, api_client, owner_headers, other_company_owner_headers):
        """A location/QR created under a different company can never
        satisfy an employee's group here, even if guessed exactly."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        my_loc = _create_location(api_client, owner_headers, 35.0, 35.0)
        my_group = _create_group(api_client, owner_headers, location_ids=[my_loc["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], my_group["id"])

        other_loc = _create_location(api_client, other_company_owner_headers, 35.0, 35.0)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": other_loc["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 400, r.text

    def test_scenario_i_historical_records_intact_after_membership_changes(self, api_client, owner_headers):
        """I: past Attendance rows keep whichever group actually applied
        at check-in time, even after the employee's group memberships
        change afterward."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc = _create_location(api_client, owner_headers, 45.0, 45.0)
        group = _create_group(api_client, owner_headers, location_ids=[loc["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group["id"])

        api_client.post(f"{BASE_URL}/api/employee/attendance/check-in", json={"qr_code": loc["qr_token"]}, headers=employee_headers)
        api_client.post(f"{BASE_URL}/api/employee/attendance/check-out", json={"qr_code": loc["qr_token"]}, headers=employee_headers)

        history_before = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history_before[0]["group_id"] == group["id"]

        # Remove the membership entirely.
        r = api_client.delete(f"{BASE_URL}/api/owner/employees/{employee['id']}/attendance-groups/{group['id']}", headers=owner_headers)
        assert r.status_code == 200, r.text

        # The already-recorded attendance row is untouched.
        history_after = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history_after[0]["id"] == history_before[0]["id"]
        assert history_after[0]["group_id"] == group["id"]


class TestExplicitGroupSelectionSecurity:
    """2026-09-01 focused audit: explicit group_id must never let an
    employee choose a less-restrictive policy than the one that actually
    applies. Covers: membership-only selection, context validity
    (QR/zone) still enforced on the SELECTED group, cross-employee and
    cross-company rejection, and that automatic single-match resolution
    never substitutes for a required explicit choice when genuinely
    ambiguous."""

    def _setup_two_qr_groups(self, api_client, owner_headers):
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc_a = _create_location(api_client, owner_headers, 80.0, 80.0)
        loc_b = _create_location(api_client, owner_headers, -80.0, -80.0)
        group_a = _create_group(api_client, owner_headers, location_ids=[loc_a["id"]], require_qr=True, require_zone=False)
        group_b = _create_group(api_client, owner_headers, location_ids=[loc_b["id"]], require_qr=True, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])
        return employee, employee_headers, loc_a, loc_b, group_a, group_b

    def _setup_two_overlapping_zone_groups(self, api_client, owner_headers):
        """Group A and Group B each require ONLY zone, and are linked to
        locations at the exact same coordinates - a single GPS reading can
        genuinely satisfy both at once, the only way one check-in request
        can match two independently-policed groups simultaneously."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        loc_a = _create_location(api_client, owner_headers, 50.0, 50.0)
        loc_b = _create_location(api_client, owner_headers, 50.0, 50.0)
        group_a = _create_group(api_client, owner_headers, location_ids=[loc_a["id"]], require_qr=False, require_zone=True)
        group_b = _create_group(api_client, owner_headers, location_ids=[loc_b["id"]], require_qr=False, require_zone=True)
        _add_membership(api_client, owner_headers, employee["id"], group_a["id"])
        _add_membership(api_client, owner_headers, employee["id"], group_b["id"])
        return employee, employee_headers, group_a, group_b

    def test_only_group_a_context_matches_auto_selects_a(self, api_client, owner_headers):
        employee, employee_headers, loc_a, loc_b, group_a, group_b = self._setup_two_qr_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc_a["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_a["id"]

    def test_only_group_b_context_matches_auto_selects_b(self, api_client, owner_headers):
        # Own fresh setup - avoids the pre-existing, unrelated one-cycle-
        # per-calendar-day constraint (see scenario D/E above).
        employee, employee_headers, loc_a, loc_b, group_a, group_b = self._setup_two_qr_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc_b["qr_token"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_b["id"]

    def test_both_groups_match_same_context_requires_explicit_selection(self, api_client, owner_headers):
        employee, employee_headers, group_a, group_b = self._setup_two_overlapping_zone_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"latitude": 50.0, "longitude": 50.0}, headers=employee_headers,
        )
        assert r.status_code == 409, r.text

    def test_explicit_selection_of_matching_group_a_accepted(self, api_client, owner_headers):
        employee, employee_headers, group_a, group_b = self._setup_two_overlapping_zone_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"latitude": 50.0, "longitude": 50.0, "group_id": group_a["id"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_a["id"]

    def test_explicit_selection_of_matching_group_b_accepted(self, api_client, owner_headers):
        employee, employee_headers, group_a, group_b = self._setup_two_overlapping_zone_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"latitude": 50.0, "longitude": 50.0, "group_id": group_b["id"]}, headers=employee_headers,
        )
        assert r.status_code == 200, r.text
        history = api_client.get(f"{BASE_URL}/api/employee/attendance/history", headers=employee_headers).json()
        assert history[0]["group_id"] == group_b["id"]

    def test_explicit_selection_of_a_membership_that_does_not_match_context_rejected(self, api_client, owner_headers):
        """Employee is a genuine, active member of group_a - but presents
        group_b's QR while explicitly naming group_a. group_a's OWN QR
        requirement is not satisfied, so it must be rejected even though
        the selection itself is a valid membership."""
        employee, employee_headers, loc_a, loc_b, group_a, group_b = self._setup_two_qr_groups(api_client, owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"qr_code": loc_b["qr_token"], "group_id": group_a["id"]}, headers=employee_headers,
        )
        assert r.status_code == 400, r.text

    def test_explicit_selection_cannot_bypass_a_required_zone_policy(self, api_client, owner_headers):
        """The core exploit this audit targets: an employee who belongs to
        BOTH a strict, zone-gated group AND a genuinely unrestricted one
        must not be able to name the strict group explicitly while
        physically outside its zone and have it silently accepted -
        naming a group never substitutes for actually satisfying it."""
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        strict_loc = _create_location(api_client, owner_headers, 10.0, 10.0)
        strict_group = _create_group(api_client, owner_headers, location_ids=[strict_loc["id"]], require_qr=False, require_zone=True)
        open_group = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], strict_group["id"])
        _add_membership(api_client, owner_headers, employee["id"], open_group["id"])

        # Far outside strict_group's zone, but explicitly naming it anyway.
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"latitude": -10.0, "longitude": -10.0, "group_id": strict_group["id"]},
            headers=employee_headers,
        )
        assert r.status_code == 400, r.text

        # Sanity: the same employee's genuinely-unrestricted membership
        # still works normally - proving the rejection above was about
        # the zone check, not a blanket failure.
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"latitude": -10.0, "longitude": -10.0, "group_id": open_group["id"]},
            headers=employee_headers,
        )
        assert r.status_code == 200, r.text

    def test_explicit_selection_of_another_employees_group_rejected(self, api_client, owner_headers):
        employee_1, employee_1_headers = _create_fresh_employee(api_client, owner_headers)
        employee_2, employee_2_headers = _create_fresh_employee(api_client, owner_headers)
        group_1 = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        group_2 = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee_1["id"], group_1["id"])
        _add_membership(api_client, owner_headers, employee_2["id"], group_2["id"])

        # employee_1 has no membership in group_2 (it's employee_2's own,
        # real, active group in the SAME company) - must be rejected.
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"group_id": group_2["id"]}, headers=employee_1_headers,
        )
        assert r.status_code == 400, r.text

    def test_explicit_selection_of_another_companys_group_rejected(self, api_client, owner_headers, other_company_owner_headers):
        employee, employee_headers = _create_fresh_employee(api_client, owner_headers)
        my_group = _create_group(api_client, owner_headers, location_ids=[], require_qr=False, require_zone=False)
        _add_membership(api_client, owner_headers, employee["id"], my_group["id"])

        other_companys_group = _create_group(api_client, other_company_owner_headers, location_ids=[], require_qr=False, require_zone=False)
        r = api_client.post(
            f"{BASE_URL}/api/employee/attendance/check-in",
            json={"group_id": other_companys_group["id"]}, headers=employee_headers,
        )
        assert r.status_code == 400, r.text
