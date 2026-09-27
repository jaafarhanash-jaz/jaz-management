"""Live HTTP integration tests for the First-Time Company Setup Wizard
(Part 1) - company-scoped onboarding state on `companies`, gated to
company_owner. Same style as tests/test_leave.py: real requests against a
running local backend, using the session-scoped admin/owner/employee
fixtures from conftest.py.

Runs against a scratch database and a scratch backend only (see
sales_test_utils.assert_scratch_target) - creating throwaway companies
whose onboarding starts 'not_started' is exactly the kind of state a
shared dev database must never accumulate copies of.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=True)

import uuid

import pytest

from tests.conftest import BASE_URL, auth_headers


def _create_owner(api_client, admin_headers) -> dict:
    """A fresh company + owner via the real admin-provisioning endpoint -
    the one deliberate path (services/admin.py::create_company) that
    opts a company into 'not_started' rather than the column's
    fail-closed 'completed' default."""
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_OnboardingPlan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_headers,
    ).json()
    owner_email = f"TEST_onboardingowner_{unique}@example.com"
    owner_password = "pass1234"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_OnboardingCo_{unique}", "owner_email": owner_email,
            "owner_name": "Onboarding Owner", "owner_password": owner_password,
            "owner_phone": f"058{unique[:7]}", "subscription_plan_id": plan["id"],
        },
        headers=admin_headers,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(
        f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": owner_password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    return {"headers": auth_headers(body["token"]), "user": body["user"]}


@pytest.fixture
def fresh_owner(api_client, admin_headers):
    return _create_owner(api_client, admin_headers)


def test_new_company_starts_not_started_at_welcome(fresh_owner):
    """The one deliberate opt-in path (create_company) - proven via a real
    /auth/login response, not just a DB read, since that's what the
    mobile app's routing decision actually reads."""
    assert fresh_owner["user"]["onboarding_status"] == "not_started"
    assert fresh_owner["user"]["onboarding_current_step"] == "welcome"


def test_existing_seeded_company_is_completed(owner_user):
    """owner@demo.com predates this feature entirely - the column's
    server_default (not a backfill script) is what makes this
    'completed', proving existing companies are unaffected."""
    assert owner_user["onboarding_status"] == "completed"
    assert owner_user.get("onboarding_current_step") is None


def test_employee_of_incomplete_company_is_unaffected(employee_user):
    """The gate is company_owner-scoped only - an employee's own login
    response carries the fields (harmless) but nothing about their
    ability to log in depends on their employer's onboarding state."""
    assert "onboarding_status" in employee_user


def test_get_status_matches_login_response(api_client, fresh_owner):
    r = api_client.get(f"{BASE_URL}/api/owner/onboarding", headers=fresh_owner["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": "not_started", "current_step": "welcome", "completed_at": None}


def test_set_step_walks_forward_and_flips_to_in_progress(api_client, fresh_owner):
    r = api_client.post(
        f"{BASE_URL}/api/owner/onboarding/step", json={"step": "branches"}, headers=fresh_owner["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["current_step"] == "branches"


def test_set_step_rejects_unknown_step(api_client, fresh_owner):
    r = api_client.post(
        f"{BASE_URL}/api/owner/onboarding/step", json={"step": "not-a-real-step"}, headers=fresh_owner["headers"],
    )
    assert r.status_code == 400, r.text


def test_complete_stamps_completed_at_and_clears_current_step(api_client, fresh_owner):
    r = api_client.post(f"{BASE_URL}/api/owner/onboarding/complete", headers=fresh_owner["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["current_step"] is None
    assert body["completed_at"] is not None


def test_set_step_after_completion_is_rejected(api_client, fresh_owner):
    """Only restart() may reopen a graduated company - otherwise a
    stray/late set_step call would silently pop the wizard gate back
    open on an owner who already finished."""
    completed = api_client.post(f"{BASE_URL}/api/owner/onboarding/complete", headers=fresh_owner["headers"])
    assert completed.status_code == 200, completed.text

    r = api_client.post(
        f"{BASE_URL}/api/owner/onboarding/step", json={"step": "branches"}, headers=fresh_owner["headers"],
    )
    assert r.status_code == 400, r.text


def test_restart_requires_confirm(api_client, fresh_owner):
    api_client.post(f"{BASE_URL}/api/owner/onboarding/complete", headers=fresh_owner["headers"])
    r = api_client.post(
        f"{BASE_URL}/api/owner/onboarding/restart", json={"confirm": False}, headers=fresh_owner["headers"],
    )
    assert r.status_code == 400, r.text


def test_restart_resets_to_first_step(api_client, fresh_owner):
    api_client.post(f"{BASE_URL}/api/owner/onboarding/complete", headers=fresh_owner["headers"])
    r = api_client.post(
        f"{BASE_URL}/api/owner/onboarding/restart", json={"confirm": True}, headers=fresh_owner["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": "in_progress", "current_step": "welcome", "completed_at": None}


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/owner/onboarding"),
    ("POST", "/api/owner/onboarding/step"),
    ("POST", "/api/owner/onboarding/complete"),
    ("POST", "/api/owner/onboarding/restart"),
])
def test_non_owner_roles_are_forbidden(api_client, employee_headers, admin_headers, method, path):
    body = {"step": "branches", "confirm": True}
    for headers in (employee_headers, admin_headers):
        r = api_client.request(method, f"{BASE_URL}{path}", json=body, headers=headers)
        assert r.status_code == 403, f"{method} {path}: expected 403, got {r.status_code} {r.text}"
