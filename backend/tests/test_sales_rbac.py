"""JAZ Sales - Phase 1: RBAC + internal staff management (HTTP integration tier).

Runs against a live backend on the scratch database only (see sales_test_utils
.assert_scratch_target). Personas are real accounts created through the real API
by the seeded Super Admin, so this exercises the full stack: router guard ->
permission resolution -> service -> DB constraints.
"""
import concurrent.futures
import uuid

import pytest

from sales_test_utils import (
    ALL_MODULES,
    ALL_PERMISSION_KEYS,
    STAFF_PASSWORD,
    SYSTEM_ROLE_MODULES,
    SYSTEM_ROLE_PERMISSIONS,
    api,
    assert_scratch_target,
    auth,
    create_staff,
    login,
    team_items,
    unique_email,
    unique_phone,
)
# token fixtures that mint instead of logging in (see sales_test_utils) - override conftest's for this module
from sales_test_utils import admin_token, employee_token, employee_user, owner_token, owner_user  # noqa: F401,E402

ALL_PERMS = ALL_PERMISSION_KEYS  # Phase 1-3 catalog (sales_test_utils)
PHANTOM_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def personas(admin_h):
    return {
        "manager": create_staff(admin_h, ["sales_manager"], "manager"),
        "employee": create_staff(admin_h, ["sales_employee"], "employee"),
        "data_entry": create_staff(admin_h, ["lead_data_entry"], "entry"),
        "onboarding": create_staff(admin_h, ["onboarding_employee"], "onboarding"),
        "no_roles": create_staff(admin_h, [], "noroles"),
    }


def _account_exists(admin_h, email) -> bool:
    """True if a staff account with this email exists - via the Super Admin team listing, so it costs
    no login request (the shared login rate limit is precious)."""
    return any(i["email"] == email for i in team_items(admin_h))


def _has_password_key(obj) -> bool:
    if isinstance(obj, dict):
        return any(k in ("password", "password_hash") or _has_password_key(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_password_key(i) for i in obj)
    return False


def _valid_create_body():
    return {"name": "X", "email": unique_email("denied"), "phone": unique_phone(), "password": STAFF_PASSWORD, "role_keys": []}


def _sales_routes(target_id: str):
    """Every Phase-1 Sales endpoint with a syntactically valid request."""
    return [
        ("GET", "/api/sales/me", None),
        ("GET", "/api/sales/roles", None),
        ("GET", "/api/sales/team", None),
        ("POST", "/api/sales/team", _valid_create_body()),
        ("PATCH", f"/api/sales/team/{target_id}", {"name": "Hijacked"}),
        ("POST", f"/api/sales/team/{target_id}/reset-password", {"new_password": "Hijacked#1"}),
        ("POST", f"/api/sales/team/{target_id}/roles", {"role_key": "sales_manager"}),
        ("DELETE", f"/api/sales/team/{target_id}/roles/sales_manager", None),
    ]



# =============================================================================
# Super Admin
# =============================================================================
class TestSuperAdmin:
    def test_me_has_every_permission_implicitly(self, admin_h):
        r = api("GET", "/api/sales/me", admin_h)
        assert r.status_code == 200
        body = r.json()
        assert body["is_super_admin"] is True
        assert body["user"]["role"] == "super_admin"
        assert set(body["permissions"]) == ALL_PERMS
        assert body["modules"] == ALL_MODULES

    def test_can_read_team_and_roles(self, admin_h, personas):
        assert api("GET", "/api/sales/team", admin_h).status_code == 200
        # every persona is in the listing (paged: the scratch DB accumulates staff, oldest first)
        assert {p["id"] for p in personas.values()} <= {i["id"] for i in team_items(admin_h)}
        roles = api("GET", "/api/sales/roles", admin_h)
        assert roles.status_code == 200
        assert {r["key"] for r in roles.json() if r["is_system"]} == {"sales_manager", "sales_employee", "lead_data_entry", "onboarding_employee"}

    def test_system_roles_carry_only_the_approved_grants(self, admin_h):
        by_key = {r["key"]: set(r["permissions"]) for r in api("GET", "/api/sales/roles", admin_h).json() if r["is_system"]}
        assert set(by_key) == {"sales_manager", "sales_employee", "lead_data_entry", "onboarding_employee"}
        assert by_key == SYSTEM_ROLE_PERMISSIONS  # exactly the approved grants (Phase 1 + 2 + 3), nothing extra
        # staff-account management is granted to NO role - Super Admin only
        assert not any(p.startswith("sales.staff.") for perms in by_key.values() for p in perms)


# =============================================================================
# Each staff role: exactly its own permissions, nothing more
# =============================================================================
ROLE_MATRIX = {
    #  persona      -> (role key,             permissions,                       modules,            /team, /roles)
    "manager":      ("sales_manager",       SYSTEM_ROLE_PERMISSIONS["sales_manager"],       SYSTEM_ROLE_MODULES["sales_manager"],       200, 200),
    "employee":     ("sales_employee",      SYSTEM_ROLE_PERMISSIONS["sales_employee"],      SYSTEM_ROLE_MODULES["sales_employee"],      403, 403),
    "data_entry":   ("lead_data_entry",     SYSTEM_ROLE_PERMISSIONS["lead_data_entry"],     SYSTEM_ROLE_MODULES["lead_data_entry"],     403, 403),
    "onboarding":   ("onboarding_employee", SYSTEM_ROLE_PERMISSIONS["onboarding_employee"], SYSTEM_ROLE_MODULES["onboarding_employee"], 403, 403),
}


class TestStaffRoles:
    @pytest.mark.parametrize("persona", list(ROLE_MATRIX))
    def test_me_reflects_exactly_the_assigned_role(self, personas, persona):
        role_key, perms, modules, _, _ = ROLE_MATRIX[persona]
        r = api("GET", "/api/sales/me", personas[persona]["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["is_super_admin"] is False
        assert body["user"]["role"] == "jaz_staff"
        assert [x["key"] for x in body["roles"]] == [role_key]
        assert set(body["permissions"]) == perms
        assert body["modules"] == modules

    @pytest.mark.parametrize("persona", list(ROLE_MATRIX))
    def test_team_and_roles_visibility(self, personas, persona):
        _, _, _, team_status, roles_status = ROLE_MATRIX[persona]
        assert api("GET", "/api/sales/team", personas[persona]["headers"]).status_code == team_status
        assert api("GET", "/api/sales/roles", personas[persona]["headers"]).status_code == roles_status

    @pytest.mark.parametrize("persona", list(ROLE_MATRIX))
    def test_no_staff_role_can_manage_staff_accounts(self, admin_h, personas, persona):
        """Even Sales Manager: creating/editing/deactivating/resetting/role-managing
        staff is denied. Uses a real target so a 403 is proven to be authorization,
        not a 404."""
        target = personas["no_roles"]["id"]
        for method, path, body in _sales_routes(target):
            if method == "GET":
                continue
            r = api(method, path, personas[persona]["headers"], body)
            assert r.status_code == 403, f"{persona} {method} {path} -> {r.status_code} {r.text}"
        # ...and nothing happened to the target
        team = {i["id"]: i for i in team_items(admin_h)}
        assert team[target]["name"] == "Test noroles"
        assert team[target]["roles"] == []

    def test_manager_gains_no_privilege_from_holding_view_permission(self, personas):
        me = api("GET", "/api/sales/me", personas["manager"]["headers"]).json()
        assert not any(p.startswith("sales.staff.") for p in me["permissions"])


class TestZeroRolesStaff:
    def test_authenticated_but_holds_nothing(self, personas):
        r = api("GET", "/api/sales/me", personas["no_roles"]["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["roles"] == [] and body["permissions"] == [] and body["modules"] == []
        assert body["user"]["role"] == "jaz_staff"

    def test_denied_everywhere_else(self, personas):
        h = personas["no_roles"]["headers"]
        assert api("GET", "/api/sales/team", h).status_code == 403
        assert api("GET", "/api/sales/roles", h).status_code == 403


# =============================================================================
# Multi-role: permissions are the UNION of all active roles
# =============================================================================
class TestMultiRoleUnion:
    def test_union_and_immediate_effect_of_grant_and_revoke(self, admin_h):
        u = create_staff(admin_h, ["sales_employee", "onboarding_employee"], "multi")
        me = api("GET", "/api/sales/me", u["headers"]).json()
        assert {r["key"] for r in me["roles"]} == {"sales_employee", "onboarding_employee"}
        employee_perms = SYSTEM_ROLE_PERMISSIONS["sales_employee"] | SYSTEM_ROLE_PERMISSIONS["onboarding_employee"]
        assert set(me["permissions"]) == employee_perms
        assert api("GET", "/api/sales/team", u["headers"]).status_code == 403

        # add the manager role -> team.view (and the manager's lead permissions) join the union, effective on the very next request
        assert api("POST", f"/api/sales/team/{u['id']}/roles", admin_h, {"role_key": "sales_manager"}).status_code == 200
        me = api("GET", "/api/sales/me", u["headers"]).json()
        assert set(me["permissions"]) == employee_perms | SYSTEM_ROLE_PERMISSIONS["sales_manager"]
        assert len(me["roles"]) == 3
        assert api("GET", "/api/sales/team", u["headers"]).status_code == 200

        # revoke it -> privilege disappears, the other two roles are untouched
        assert api("DELETE", f"/api/sales/team/{u['id']}/roles/sales_manager", admin_h).status_code == 200
        me = api("GET", "/api/sales/me", u["headers"]).json()
        assert set(me["permissions"]) == employee_perms
        assert {r["key"] for r in me["roles"]} == {"sales_employee", "onboarding_employee"}
        assert api("GET", "/api/sales/team", u["headers"]).status_code == 403

    def test_permission_survives_revoking_only_one_of_two_roles_that_grant_it(self, admin_h):
        u = create_staff(admin_h, ["sales_manager", "sales_employee"], "overlap")
        # sales.access comes from BOTH roles; revoking one must not remove it
        api("DELETE", f"/api/sales/team/{u['id']}/roles/sales_employee", admin_h)
        me = api("GET", "/api/sales/me", u["headers"]).json()
        assert "sales.access" in me["permissions"] and "sales.team.view" in me["permissions"]


# =============================================================================
# Deactivated staff
# =============================================================================
class TestDeactivatedStaff:
    def test_denied_immediately_and_refresh_token_revoked_then_restorable(self, admin_h):
        u = create_staff(admin_h, ["sales_manager"], "deactivate", real_login=True)
        assert api("GET", "/api/sales/team", u["headers"]).status_code == 200

        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"status": "inactive"})
        assert r.status_code == 200 and r.json()["status"] == "inactive"

        # the already-issued access token is still cryptographically valid - Sales must refuse it anyway
        assert api("GET", "/api/sales/me", u["headers"]).status_code == 403
        assert api("GET", "/api/sales/team", u["headers"]).status_code == 403
        # sessions are ended: the refresh token was revoked
        rr = api("POST", "/api/auth/refresh", None, {"refresh_token": u["refresh_token"]})
        assert rr.status_code == 401

        # reactivation restores access; role grants were preserved (deactivation never deletes history)
        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"status": "active"})
        assert r.status_code == 200 and r.json()["status"] == "active"
        me = api("GET", "/api/sales/me", u["headers"])
        assert me.status_code == 200 and [x["key"] for x in me.json()["roles"]] == ["sales_manager"]


# =============================================================================
# Customer users and anonymous callers: proper 403 on every Sales route
# =============================================================================
class TestCustomerUsersDenied:
    @pytest.mark.parametrize("who", ["owner", "employee"])
    def test_every_sales_endpoint_is_403(self, who, owner_token, employee_token, personas):
        token = owner_token if who == "owner" else employee_token
        for method, path, body in _sales_routes(personas["no_roles"]["id"]):
            r = api(method, path, auth(token), body)
            assert r.status_code == 403, f"{who} {method} {path} -> {r.status_code} {r.text}"
            assert r.json()["detail"] == "Access denied"

    @pytest.mark.parametrize("who", ["owner", "employee"])
    def test_403_precedes_validation_no_information_leak(self, who, owner_token, employee_token, personas):
        """A customer sending a garbage body must get 403, not a 422 that would reveal the schema."""
        token = owner_token if who == "owner" else employee_token
        r = api("POST", "/api/sales/team", auth(token), {"garbage": True})
        assert r.status_code == 403

    def test_denied_attempts_have_no_side_effects(self, admin_h, owner_token, employee_token):
        body = _valid_create_body()
        for token in (owner_token, employee_token):
            assert api("POST", "/api/sales/team", auth(token), body).status_code == 403
        assert not _account_exists(admin_h, body["email"])  # account was never created

    def test_super_admin_still_unaffected_by_customer_denials(self, admin_h):
        assert api("GET", "/api/sales/me", admin_h).status_code == 200


class TestUnauthenticated:
    @pytest.mark.parametrize("method,path,body", _sales_routes(PHANTOM_ID))
    def test_no_token(self, method, path, body):
        r = api(method, path, None, body)
        assert r.status_code in (401, 403)

    def test_garbage_token(self):
        r = api("GET", "/api/sales/me", auth("not-a-real-token"))
        assert r.status_code == 401


# =============================================================================
# Staff account creation
# =============================================================================
class TestStaffCreation:
    def test_created_account_is_a_company_less_jaz_staff(self, admin_h):
        u = create_staff(admin_h, ["sales_employee"], "shape", real_login=True)
        assert u["login"]["role"] == "jaz_staff"
        assert u["login"]["user"]["company_id"] is None
        assert not _has_password_key(u["login"]["user"])

    def test_response_never_leaks_password_material(self, admin_h, personas):
        assert not _has_password_key(api("GET", "/api/sales/team", admin_h).json())
        assert not _has_password_key(api("GET", "/api/sales/me", admin_h).json())
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "role_keys": ["sales_employee"]})
        assert r.status_code == 201 and not _has_password_key(r.json())

    def test_duplicate_role_keys_grant_once(self, admin_h):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "role_keys": ["sales_employee", "sales_employee"]})
        assert r.status_code == 201
        assert [x["key"] for x in r.json()["roles"]] == ["sales_employee"]

    def test_email_collision_with_staff(self, admin_h, personas):
        body = {**_valid_create_body(), "email": personas["manager"]["email"]}
        r = api("POST", "/api/sales/team", admin_h, body)
        assert r.status_code == 400
        assert r.json()["detail"] == {"field": "email", "message": "Email already registered"}

    def test_email_collision_with_a_company_owner(self, admin_h, owner_user):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "email": owner_user["email"]})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "email"

    def test_phone_collision_with_staff(self, admin_h, personas):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "phone": personas["manager"]["phone"]})
        assert r.status_code == 400
        assert r.json()["detail"] == {"field": "phone", "message": "Phone number already registered"}

    def test_phone_collision_with_a_company_owner(self, admin_h, owner_user):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "phone": owner_user["phone"]})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "phone"

    def test_collision_creates_nothing(self, admin_h, personas):
        # Not "the global team total is unchanged": the suite runs in parallel workers, so another module may create staff
        # between two reads of a global count. This request's own artifact - the unique phone it carried - is what must not exist.
        body = {**_valid_create_body(), "email": personas["manager"]["email"]}
        r = api("POST", "/api/sales/team", admin_h, body)
        assert r.status_code == 400 and r.json()["detail"]["field"] == "email"
        assert not any(i["phone"] == body["phone"] for i in team_items(admin_h))

    def test_weak_password(self, admin_h):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "password": "abc"})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "password"

    def test_unknown_role_key_rejected_server_side_and_nothing_created(self, admin_h):
        body = {**_valid_create_body(), "role_keys": ["sales_employee", "definitely_not_a_role"]}
        r = api("POST", "/api/sales/team", admin_h, body)
        assert r.status_code == 400 and r.json()["detail"]["field"] == "role_keys"
        assert not _account_exists(admin_h, body["email"])

    @pytest.mark.parametrize("role_key", ["super_admin", "company_owner", "employee", "jaz_staff"])
    def test_platform_role_names_are_not_valid_staff_role_keys(self, admin_h, role_key):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "role_keys": [role_key]})
        assert r.status_code == 400

    @pytest.mark.parametrize("extra", [{"role": "super_admin"}, {"company_id": PHANTOM_ID}, {"status": "active"}, {"is_super_admin": True}])
    def test_client_cannot_smuggle_platform_role_or_other_fields(self, admin_h, extra):
        r = api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), **extra})
        assert r.status_code == 422

    def test_blank_name_and_invalid_email(self, admin_h):
        assert api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "name": "   "}).status_code == 422
        assert api("POST", "/api/sales/team", admin_h, {**_valid_create_body(), "email": "not-an-email"}).status_code == 422


# =============================================================================
# Staff account update / password reset
# =============================================================================
class TestStaffUpdateAndReset:
    def test_update_allowed_fields(self, admin_h):
        u = create_staff(admin_h, [], "upd", do_login=False)
        new_email, new_phone = unique_email("upd2"), unique_phone()
        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"name": "Renamed", "email": new_email, "phone": new_phone})
        assert r.status_code == 200
        body = r.json()
        assert (body["name"], body["email"], body["phone"]) == ("Renamed", new_email, new_phone)
        assert login(new_email, STAFF_PASSWORD)["role"] == "jaz_staff"  # can log in with the new identifier

    def test_update_email_and_phone_collisions(self, admin_h, personas):
        u = create_staff(admin_h, [], "col", do_login=False)
        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"email": personas["manager"]["email"]})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "email"
        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"phone": personas["manager"]["phone"]})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "phone"

    def test_update_to_own_current_values_is_not_a_collision(self, admin_h):
        u = create_staff(admin_h, [], "same", do_login=False)
        r = api("PATCH", f"/api/sales/team/{u['id']}", admin_h, {"email": u["email"], "phone": u["phone"], "name": "Same"})
        assert r.status_code == 200

    def test_empty_patch(self, admin_h, personas):
        assert api("PATCH", f"/api/sales/team/{personas['no_roles']['id']}", admin_h, {}).status_code == 400

    @pytest.mark.parametrize("bad", [{"role": "employee"}, {"company_id": PHANTOM_ID}, {"password": "abcdef1"}, {"status": "deleted"}, {"id": PHANTOM_ID}])
    def test_patch_whitelist(self, admin_h, personas, bad):
        assert api("PATCH", f"/api/sales/team/{personas['no_roles']['id']}", admin_h, bad).status_code == 422

    def test_reset_password_signs_out_and_swaps_credentials(self, admin_h):
        u = create_staff(admin_h, ["sales_employee"], "reset", real_login=True)
        r = api("POST", f"/api/sales/team/{u['id']}/reset-password", admin_h, {"new_password": "Brand#New99"})
        assert r.status_code == 200
        login(u["email"], STAFF_PASSWORD, expect=401)                       # old password no longer works
        assert login(u["email"], "Brand#New99")["role"] == "jaz_staff"      # new one does
        assert api("POST", "/api/auth/refresh", None, {"refresh_token": u["refresh_token"]}).status_code == 401  # old sessions revoked

    def test_reset_weak_password(self, admin_h, personas):
        r = api("POST", f"/api/sales/team/{personas['no_roles']['id']}/reset-password", admin_h, {"new_password": "abc"})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "new_password"


# =============================================================================
# The team API can NEVER touch a non-staff account
# =============================================================================
@pytest.fixture(scope="module")
def foreign_ids(owner_user, employee_user, admin_h):
    me = api("GET", "/api/sales/me", admin_h).json()["user"]["id"]
    return {"company_owner": owner_user["id"], "employee": employee_user["id"], "super_admin": me}


class TestTeamApiIsPinnedToStaffAccounts:
    @pytest.mark.parametrize("kind", ["company_owner", "employee", "super_admin"])
    def test_every_management_call_is_404_for_non_staff_targets(self, admin_h, foreign_ids, kind):
        target = foreign_ids[kind]
        for method, path, body in _sales_routes(target):
            if method == "GET" or path == "/api/sales/team":
                continue
            r = api(method, path, admin_h, body)
            assert r.status_code == 404, f"{kind} {method} {path} -> {r.status_code} {r.text}"

    def test_customers_are_never_listed_as_staff(self, admin_h, foreign_ids):
        ids = {i["id"] for i in team_items(admin_h)}
        assert not (set(foreign_ids.values()) & ids)

    def test_owner_credentials_untouched_by_failed_reset(self, admin_h, foreign_ids, owner_user):
        api("POST", f"/api/sales/team/{foreign_ids['company_owner']}/reset-password", admin_h, {"new_password": "Hijacked#1"})
        assert login("owner@demo.com", "owner123")["role"] == "company_owner"

    @pytest.mark.parametrize("target", [PHANTOM_ID, "not-a-uuid"])
    def test_unknown_and_malformed_ids_are_404(self, admin_h, target):
        assert api("PATCH", f"/api/sales/team/{target}", admin_h, {"name": "x"}).status_code == 404
        assert api("POST", f"/api/sales/team/{target}/roles", admin_h, {"role_key": "sales_manager"}).status_code == 404


# =============================================================================
# Role grants: idempotency and validation
# =============================================================================
class TestRoleGrants:
    def test_grant_duplicate_grant_revoke_repeated_revoke(self, admin_h):
        u = create_staff(admin_h, [], "grants", do_login=False)
        base = f"/api/sales/team/{u['id']}/roles"

        r = api("POST", base, admin_h, {"role_key": "sales_employee"})
        assert r.status_code == 200 and r.json()["changed"] is True
        assert [x["key"] for x in r.json()["staff"]["roles"]] == ["sales_employee"]

        r = api("POST", base, admin_h, {"role_key": "sales_employee"})          # duplicate grant
        assert r.status_code == 200 and r.json()["changed"] is False
        assert [x["key"] for x in r.json()["staff"]["roles"]] == ["sales_employee"]  # still exactly one

        r = api("DELETE", f"{base}/sales_employee", admin_h)
        assert r.status_code == 200 and r.json()["changed"] is True
        assert r.json()["staff"]["roles"] == []

        r = api("DELETE", f"{base}/sales_employee", admin_h)                     # repeated revoke
        assert r.status_code == 200 and r.json()["changed"] is False

        r = api("POST", base, admin_h, {"role_key": "sales_employee"})          # re-grant after revoke works
        assert r.status_code == 200 and r.json()["changed"] is True

    def test_revoking_a_role_never_held_is_a_noop(self, admin_h):
        u = create_staff(admin_h, [], "neverheld", do_login=False)
        r = api("DELETE", f"/api/sales/team/{u['id']}/roles/sales_manager", admin_h)
        assert r.status_code == 200 and r.json()["changed"] is False

    def test_unknown_role_on_assign_and_revoke(self, admin_h, personas):
        tid = personas["no_roles"]["id"]
        r = api("POST", f"/api/sales/team/{tid}/roles", admin_h, {"role_key": "made_up"})
        assert r.status_code == 400 and r.json()["detail"]["field"] == "role_key"
        assert api("DELETE", f"/api/sales/team/{tid}/roles/made_up", admin_h).status_code == 404

    @pytest.mark.parametrize("key", ["super_admin", "company_owner", "employee", "jaz_staff"])
    def test_platform_roles_cannot_be_assigned_as_staff_roles(self, admin_h, personas, key):
        r = api("POST", f"/api/sales/team/{personas['no_roles']['id']}/roles", admin_h, {"role_key": key})
        assert r.status_code == 400

    def test_assign_body_rejects_unknown_fields(self, admin_h, personas):
        r = api("POST", f"/api/sales/team/{personas['no_roles']['id']}/roles", admin_h, {"role_key": "sales_manager", "role": "super_admin"})
        assert r.status_code == 422


# =============================================================================
# Concurrency: idempotent + race-safe as designed
# =============================================================================
class TestConcurrency:
    N = 8

    def _burst(self, fn):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.N) as pool:
            return list(pool.map(lambda _: fn(), range(self.N)))

    def test_parallel_grants_yield_exactly_one_active_grant(self, admin_h):
        u = create_staff(admin_h, [], "racegrant")
        results = self._burst(lambda: api("POST", f"/api/sales/team/{u['id']}/roles", admin_h, {"role_key": "sales_manager"}))
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        assert sum(1 for r in results if r.json()["changed"]) == 1
        roles = api("GET", "/api/sales/me", u["headers"]).json()["roles"]
        assert [x["key"] for x in roles] == ["sales_manager"]

    def test_parallel_revokes_change_exactly_once(self, admin_h):
        u = create_staff(admin_h, ["sales_manager"], "racerevoke")
        results = self._burst(lambda: api("DELETE", f"/api/sales/team/{u['id']}/roles/sales_manager", admin_h))
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        assert sum(1 for r in results if r.json()["changed"]) == 1
        assert api("GET", "/api/sales/me", u["headers"]).json()["roles"] == []

    def test_parallel_creation_with_same_email_creates_exactly_one_and_never_500s(self, admin_h):
        body = {**_valid_create_body(), "email": unique_email("racecreate")}
        results = self._burst(lambda: api("POST", "/api/sales/team", admin_h, {**body, "phone": unique_phone()}))
        codes = sorted(r.status_code for r in results)
        assert all(c in (201, 400) for c in codes), codes          # never a 500 from the lost race
        assert codes.count(201) == 1, codes
        assert all(r.json()["detail"]["field"] == "email" for r in results if r.status_code == 400)


# =============================================================================
# The "explicit future permission" path: each sales.staff.* permission, granted on
# its own via a throwaway role, unlocks exactly its own endpoints and nothing else.
# (Today no shipped role holds any of them - only Super Admin does implicitly.)
# =============================================================================
def _make_custom_role(permission_keys) -> str:
    """Throwaway staff role holding exactly `permission_keys` (direct DB write; scratch only)."""
    import asyncio
    from database import SessionLocal, engine
    from sales.models import StaffRole, StaffRolePermission

    key = f"test_{uuid.uuid4().hex[:10]}"

    async def _go():
        try:
            async with SessionLocal() as db:
                role = StaffRole(id=uuid.uuid4(), module="sales", key=key, name_en=key, name_ar=key)
                db.add(role)
                await db.flush()
                for p in permission_keys:
                    db.add(StaffRolePermission(role_id=role.id, permission_key=p))
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_go())
    return key


PERM_PERSONAS = {
    # persona            permissions granted (besides sales.access)             endpoints it may call
    "only_create":       (["sales.staff.create"],                              {"create_no_roles"}),
    "only_update":       (["sales.staff.update"],                              {"update", "reset"}),
    "only_assign":       (["sales.staff.assign_roles"],                        {"assign", "revoke"}),
    "create_and_assign": (["sales.staff.create", "sales.staff.assign_roles"],  {"create_no_roles", "create_with_role", "assign", "revoke"}),
}


def _retire_roles(role_keys):
    """Best-effort cleanup: roles that have been assigned can't be deleted (RESTRICT), so retire them."""
    import asyncio
    from database import SessionLocal, engine
    from sqlalchemy import update
    from sales.models import StaffRole

    async def _go():
        try:
            async with SessionLocal() as db:
                await db.execute(update(StaffRole).where(StaffRole.key.in_(role_keys), StaffRole.is_system.is_(False)).values(is_active=False))
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_go())


@pytest.fixture(scope="module")
def perm_personas(admin_h):
    out, created_keys = {}, []
    for name, (perms, _) in PERM_PERSONAS.items():
        role_key = _make_custom_role(["sales.access", *perms])
        created_keys.append(role_key)
        out[name] = create_staff(admin_h, [role_key], name)
    yield out
    _retire_roles(created_keys)


@pytest.fixture(scope="module")
def perm_target(admin_h):
    return create_staff(admin_h, ["sales_employee"], "permtarget", do_login=False)


class TestStaffPermissionsAreIndependent:
    @pytest.mark.parametrize("persona", list(PERM_PERSONAS))
    def test_each_permission_unlocks_exactly_its_own_endpoints(self, perm_personas, perm_target, persona):
        allowed = PERM_PERSONAS[persona][1]
        h = perm_personas[persona]["headers"]
        tid = perm_target["id"]
        calls = {
            "create_no_roles": ("POST", "/api/sales/team", {**_valid_create_body(), "role_keys": []}),
            "create_with_role": ("POST", "/api/sales/team", {**_valid_create_body(), "role_keys": ["sales_employee"]}),
            "update": ("PATCH", f"/api/sales/team/{tid}", {"name": f"Renamed by {persona}"}),
            "reset": ("POST", f"/api/sales/team/{tid}/reset-password", {"new_password": "Perm#Reset99"}),
            "assign": ("POST", f"/api/sales/team/{tid}/roles", {"role_key": "onboarding_employee"}),
            "revoke": ("DELETE", f"/api/sales/team/{tid}/roles/onboarding_employee", None),
        }
        for name, (method, path, body) in calls.items():
            r = api(method, path, h, body)
            if name in allowed:
                assert r.status_code in (200, 201), f"{persona} should be allowed {name}: {r.status_code} {r.text}"
            else:
                assert r.status_code == 403, f"{persona} must be DENIED {name}: {r.status_code} {r.text}"

    def test_create_with_initial_roles_needs_both_create_and_assign(self, admin_h, perm_personas):
        """Creating an account and choosing its roles are separate privileges: a creator without
        assign_roles cannot smuggle roles in through the create call (and creates nothing)."""
        body = {**_valid_create_body(), "role_keys": ["sales_manager"]}
        r = api("POST", "/api/sales/team", perm_personas["only_create"]["headers"], body)
        assert r.status_code == 403
        assert not _account_exists(admin_h, body["email"])
