"""JAZ Sales - Phase 2: RBAC for leads, campaigns and assignment (HTTP integration tier).

Two layers, tested separately and together:
  * ACTION permissions - which endpoints a role may call at all (403 otherwise, decided before validation);
  * LEAD SCOPE - which leads that role may see and act on (a list is silently narrowed; one lead outside the scope
    answers 403 and is never touched).
Plus: customer users and anonymous callers, deactivated / revoked staff, custom roles, and permission unions.
Personas are real accounts created through the real API by the seeded Super Admin.
"""
import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    assign,
    create_campaign,
    create_lead,
    get_lead,
    lead_body,
    make_custom_role,
    make_personas,
    retire_roles,
    uniq,
)
from sales_test_utils import (  # noqa: F401  (the token fixtures mint instead of logging in)
    SYSTEM_ROLE_MODULES,
    admin_token,
    api,
    assert_scratch_target,
    auth,
    create_staff,
    employee_token,
    employee_user,
    owner_token,
    owner_user,
)


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "rbac")


def H(p, who):
    return p[who]["headers"]


@pytest.fixture(scope="module")
def world(p, admin_h):
    """One campaign holding six leads that between them exercise every visibility rule."""
    manager, data_entry, data_entry2 = H(p, "manager"), H(p, "data_entry"), H(p, "data_entry2")
    campaign = create_campaign(manager)
    make = lambda h, **kw: create_lead(h, campaign_id=campaign["id"], **kw)  # noqa: E731
    leads = {
        "L1": make(manager),                                                   # manager-created, unassigned
        "L2": make(manager),                                                   # -> employee
        "L3": make(manager),                                                   # -> employee2
        "L4": make(data_entry),                                                # data_entry-created, unassigned
        "L5": make(data_entry2),                                               # data_entry2-created, unassigned
        "L6": make(data_entry2),                                               # data_entry2-created, then -> employee
    }
    assign(manager, leads["L2"]["id"], p["employee"]["id"])
    assign(manager, leads["L3"]["id"], p["employee2"]["id"])
    assign(manager, leads["L6"]["id"], p["employee"]["id"])
    return {"campaign": campaign, "leads": {k: v["id"] for k, v in leads.items()}}


# who sees which leads (by name) - the whole specification of Phase-2 visibility in one table
VISIBLE = {
    "admin":       {"L1", "L2", "L3", "L4", "L5", "L6"},
    "manager":     {"L1", "L2", "L3", "L4", "L5", "L6"},
    "employee":    {"L2", "L6"},                      # only what is assigned to them
    "employee2":   {"L3"},
    "data_entry":  {"L1", "L4", "L5"},                # what they created + everything unassigned
    "data_entry2": {"L1", "L4", "L5", "L6"},          # ...and their own creation stays visible after it is assigned
}
NO_LEAD_ACCESS = ["onboarding", "no_roles"]


def headers_for(p, admin_h, who):
    return admin_h if who == "admin" else H(p, who)


# =============================================================================
class TestLeadScope:
    @pytest.mark.parametrize("who", list(VISIBLE))
    def test_a_list_contains_exactly_the_leads_in_scope(self, p, admin_h, world, who):
        h = headers_for(p, admin_h, who)
        page = api("GET", f"/api/sales/leads?campaign_id={world['campaign']['id']}&limit=100", h).json()
        got = {name for name, lid in world["leads"].items() if lid in {i["id"] for i in page["items"]}}
        assert got == VISIBLE[who]
        assert page["total"] == len(VISIBLE[who])

    @pytest.mark.parametrize("who", list(VISIBLE))
    def test_a_single_lead_is_readable_only_inside_the_scope(self, p, admin_h, world, who):
        h = headers_for(p, admin_h, who)
        for name, lid in world["leads"].items():
            r = api("GET", f"/api/sales/leads/{lid}", h)
            if name in VISIBLE[who]:
                assert r.status_code == 200, (who, name)
            else:
                assert r.status_code == 403 and r.json()["detail"] == "Access denied", (who, name)
            r = api("GET", f"/api/sales/leads/{lid}/activities", h)
            assert r.status_code == (200 if name in VISIBLE[who] else 403), (who, name)

    @pytest.mark.parametrize("who", list(VISIBLE))
    def test_stage_counts_only_count_what_the_caller_may_see(self, p, admin_h, world, who):
        body = api("GET", f"/api/sales/leads/stage-counts?campaign_id={world['campaign']['id']}", headers_for(p, admin_h, who)).json()
        assert body["total"] == len(VISIBLE[who]) == sum(body["counts"].values())

    @pytest.mark.parametrize("who", ["employee", "employee2", "data_entry", "data_entry2"])
    def test_filters_cannot_widen_the_scope(self, p, world, who):
        """Asking for another person's leads, or everything, still returns only what the caller may see."""
        h = H(p, who)
        base = f"/api/sales/leads?campaign_id={world['campaign']['id']}&limit=100"
        for extra in (f"&assigned_to={p['employee2']['id']}", f"&assigned_to={p['employee']['id']}", "&assigned_to=unassigned",
                      "&archived=all", "&pipeline_stage=assigned,new"):
            ids = {i["id"] for i in api("GET", base + extra, h).json()["items"]}
            assert ids <= {world["leads"][n] for n in VISIBLE[who]}, (who, extra)

    def test_searching_by_a_visible_leads_details_does_not_reveal_others(self, p, world):
        outsider_lead = get_lead(H(p, "manager"), world["leads"]["L3"])            # assigned to employee2
        r = api("GET", "/api/sales/leads", H(p, "employee"), None, params={"q": outsider_lead["phone"]}).json()
        assert r["total"] == 0 and r["items"] == []


class TestMutationsStayInsideTheScope:
    """A caller who holds the ACTION permission but not the lead's scope is stopped, and the lead is never touched."""

    def test_employee_cannot_edit_move_or_release_another_employees_lead(self, p, world):
        target = world["leads"]["L3"]                                              # employee2's lead
        h = H(p, "employee")
        before = get_lead(H(p, "manager"), target)
        for method, path, body in [
            ("PATCH", f"/api/sales/leads/{target}", {"priority": "high"}),
            ("POST", f"/api/sales/leads/{target}/stage", {"stage": "contacted"}),
        ]:
            assert api(method, path, h, body).status_code == 403, path
        assert get_lead(H(p, "manager"), target) == before

    def test_data_entry_cannot_touch_a_lead_assigned_to_an_employee_unless_they_created_it(self, p, world):
        # L2 was created by the manager and assigned to employee: outside data_entry's scope
        assert api("PATCH", f"/api/sales/leads/{world['leads']['L2']}", H(p, "data_entry"), {"priority": "high"}).status_code == 403
        # L6 was created by data_entry2 and is assigned to employee: still theirs to correct
        r = api("PATCH", f"/api/sales/leads/{world['leads']['L6']}", H(p, "data_entry2"), {"notes": "typo fixed"})
        assert r.status_code == 200 and r.json()["notes"] == "typo fixed"
        # ...but data_entry (not the creator) cannot
        assert api("PATCH", f"/api/sales/leads/{world['leads']['L6']}", H(p, "data_entry"), {"notes": "hijack"}).status_code == 403

    def test_data_entry_can_manage_unassigned_leads_even_when_created_by_someone_else(self, p, world):
        r = api("PATCH", f"/api/sales/leads/{world['leads']['L1']}", H(p, "data_entry"), {"contact_name": "Corrected by intake"})
        assert r.status_code == 200 and r.json()["contact_name"] == "Corrected by intake"

    def test_once_assigned_the_lead_leaves_an_intake_users_scope_unless_they_created_it(self, p, admin_h):
        manager = H(p, "manager")
        lead = create_lead(manager)                                                # manager-created, unassigned: intake can work it
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", H(p, "data_entry"), {"notes": "before"}).status_code == 200
        assign(manager, lead["id"], p["employee"]["id"])
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", H(p, "data_entry"), {"notes": "after"}).status_code == 403
        assert api("GET", f"/api/sales/leads/{lead['id']}", H(p, "data_entry")).status_code == 403

    def test_a_reassignment_moves_the_lead_out_of_the_old_owners_scope(self, p, admin_h):
        manager = H(p, "manager")
        lead = create_lead(manager)
        assign(manager, lead["id"], p["employee"]["id"])
        assert api("GET", f"/api/sales/leads/{lead['id']}", H(p, "employee")).status_code == 200
        assign(manager, lead["id"], p["employee2"]["id"])
        assert api("GET", f"/api/sales/leads/{lead['id']}", H(p, "employee")).status_code == 403
        assert api("GET", f"/api/sales/leads/{lead['id']}", H(p, "employee2")).status_code == 200
        api("POST", f"/api/sales/leads/{lead['id']}/unassign", manager)
        assert api("GET", f"/api/sales/leads/{lead['id']}", H(p, "employee2")).status_code == 403

    def test_a_custom_manager_like_role_limited_to_intake_cannot_reassign_other_peoples_leads(self, admin_h, p, world):
        role = make_custom_role([
            "sales.access", "sales.leads.view", "sales.leads.update", "sales.leads.assign", "sales.leads.scope_intake",
        ])
        try:
            person = create_staff(admin_h, [role], "intake-assigner")
            h = person["headers"]
            outside = world["leads"]["L3"]                                          # assigned to employee2, created by the manager
            assert api("POST", f"/api/sales/leads/{outside}/assign", h, {"assigned_to": p["employee"]["id"]}).status_code == 403
            assert api("POST", f"/api/sales/leads/{outside}/unassign", h).status_code == 403
            assert api("POST", "/api/sales/leads/bulk-assign", h, {"lead_ids": [outside], "assigned_to": p["employee"]["id"]}).status_code == 403
            assert get_lead(H(p, "manager"), outside)["assigned_to"]["id"] == p["employee2"]["id"]   # untouched
            inside = create_lead(H(p, "manager"))["id"]                             # unassigned: inside the intake scope
            assert api("POST", f"/api/sales/leads/{inside}/assign", h, {"assigned_to": p["employee"]["id"]}).status_code == 200
        finally:
            retire_roles([role])


# =============================================================================
# the action-permission matrix: which role may call which endpoint (403 decided before anything else)
# =============================================================================
ROLES = ["admin", "manager", "employee", "data_entry", "onboarding", "no_roles"]
ENDPOINTS = {
    # name:                      (method, path, allowed roles)
    "sources":                   ("GET", "/sources", {"admin", "manager", "employee", "data_entry"}),
    "assignees":                 ("GET", "/assignees", {"admin", "manager"}),
    "campaigns.list":            ("GET", "/campaigns", {"admin", "manager", "employee", "data_entry"}),
    "campaigns.get":             ("GET", "/campaigns/{campaign}", {"admin", "manager", "employee", "data_entry"}),
    "campaigns.create":          ("POST", "/campaigns", {"admin", "manager"}),
    "campaigns.update":          ("PATCH", "/campaigns/{campaign}", {"admin", "manager"}),
    "campaigns.delete":          ("DELETE", "/campaigns/{campaign}", {"admin", "manager"}),
    "leads.list":                ("GET", "/leads", {"admin", "manager", "employee", "data_entry"}),
    "leads.stage_counts":        ("GET", "/leads/stage-counts", {"admin", "manager", "employee", "data_entry"}),
    "leads.duplicate_check":     ("POST", "/leads/duplicate-check", {"admin", "manager", "employee", "data_entry"}),
    "leads.create":              ("POST", "/leads", {"admin", "manager", "data_entry"}),
    "leads.get":                 ("GET", "/leads/{lead}", {"admin", "manager", "employee", "data_entry"}),
    "leads.activities":          ("GET", "/leads/{lead}/activities", {"admin", "manager", "employee", "data_entry"}),
    "leads.update":              ("PATCH", "/leads/{lead}", {"admin", "manager", "employee", "data_entry"}),
    "leads.stage":               ("POST", "/leads/{lead}/stage", {"admin", "manager", "employee"}),
    "leads.assign":              ("POST", "/leads/{lead}/assign", {"admin", "manager"}),
    "leads.unassign":            ("POST", "/leads/{lead}/unassign", {"admin", "manager"}),
    "leads.bulk_assign":         ("POST", "/leads/bulk-assign", {"admin", "manager"}),
    "leads.archive":             ("DELETE", "/leads/{lead}", {"admin", "manager"}),
    "leads.restore":             ("POST", "/leads/{lead}/restore", {"admin", "manager"}),
}


def _lead_in_scope_for(p, who):
    """A fresh lead the role could legitimately act on, so a 403 can only mean 'not permitted'."""
    manager = H(p, "manager")
    if who == "employee":
        lead = create_lead(manager)
        assign(manager, lead["id"], p["employee"]["id"])
        return lead["id"]
    if who == "data_entry":
        return create_lead(H(p, "data_entry"))["id"]
    return create_lead(manager)["id"]


def _request_for(p, admin_h, campaign_id, who, name):
    method, template, _ = ENDPOINTS[name]
    lead = _lead_in_scope_for(p, who)
    path = "/api/sales" + template.format(lead=lead, campaign=campaign_id)
    body = {
        "campaigns.create": {"name": f"Matrix {uniq()}"},
        "campaigns.update": {"description": "matrix"},
        "leads.create": lead_body(),
        "leads.duplicate_check": {"phone": "+15550001111"},
        "leads.update": {"notes": "matrix"},
        "leads.stage": {"stage": "lost", "lost_reason": "other"},
        "leads.assign": {"assigned_to": p["employee"]["id"]},
        "leads.bulk_assign": {"lead_ids": [lead], "assigned_to": p["employee"]["id"]},
    }.get(name)
    return method, path, body


class TestActionPermissionMatrix:
    @pytest.mark.parametrize("name", list(ENDPOINTS))
    @pytest.mark.parametrize("who", ROLES)
    def test_endpoint_by_role(self, p, admin_h, world, who, name):
        # a throwaway campaign so campaigns.delete never removes the shared one
        campaign = create_campaign(H(p, "manager"))
        method, path, body = _request_for(p, admin_h, campaign["id"], who, name)
        r = api(method, path, headers_for(p, admin_h, who), body)
        allowed = who in ENDPOINTS[name][2]
        if allowed:
            assert r.status_code != 403, f"{who} must be allowed {name}: {r.status_code} {r.text[:200]}"
            assert r.status_code < 500
        else:
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", f"{who} must be denied {name}: {r.status_code} {r.text[:200]}"

    def test_the_matrix_covers_every_phase_2_route(self):
        from server import app
        routes = {(m, r.path.removeprefix("/api/sales")) for r in app.routes if getattr(r, "path", "").startswith("/api/sales/")
                  for m in r.methods if m not in ("HEAD", "OPTIONS")}
        phase2 = {(m, path) for m, path in routes if path.split("/")[1] in ("sources", "assignees", "campaigns", "leads")
                  and not path.startswith(("/leads/{lead_id}/convert", "/leads/{lead_id}/conversion"))}   # those are Phase 4's (test_sales_customers_rbac)
        covered = {(m, t.replace("{lead}", "{lead_id}").replace("{campaign}", "{campaign_id}")) for m, t, _ in ENDPOINTS.values()}
        assert phase2 == covered, f"uncovered: {phase2 - covered}; stale: {covered - phase2}"

    @pytest.mark.parametrize("who", ["onboarding", "no_roles"])
    def test_staff_without_lead_permissions_are_denied_everywhere_even_with_garbage_bodies(self, p, who):
        h = H(p, who)
        assert api("POST", "/api/sales/leads", h, {"garbage": True}).status_code == 403     # 403 precedes validation: no schema leak
        assert api("GET", "/api/sales/leads", h).status_code == 403
        assert api("GET", "/api/sales/campaigns", h).status_code == 403

    @pytest.mark.parametrize("who", ["employee", "data_entry"])
    def test_403_precedes_validation_for_missing_permissions(self, p, who):
        assert api("POST", "/api/sales/campaigns", H(p, who), {"garbage": True}).status_code == 403
        assert api("POST", "/api/sales/leads/bulk-assign", H(p, who), {"lead_ids": "nope"}).status_code == 403

    def test_workspace_modules_follow_each_roles_permissions(self, p):
        for who, expect_modules in (("manager", SYSTEM_ROLE_MODULES["sales_manager"]), ("employee", SYSTEM_ROLE_MODULES["sales_employee"]),
                                    ("data_entry", SYSTEM_ROLE_MODULES["lead_data_entry"]), ("onboarding", SYSTEM_ROLE_MODULES["onboarding_employee"]), ("no_roles", [])):
            assert api("GET", "/api/sales/me", H(p, who)).json()["modules"] == expect_modules, who


# =============================================================================
# callers who are not internal staff
# =============================================================================
def _every_phase_2_call(lead_id, campaign_id):
    return [
        ("GET", "/api/sales/sources", None), ("GET", "/api/sales/assignees", None),
        ("GET", "/api/sales/campaigns", None), ("POST", "/api/sales/campaigns", {"name": "x"}),
        ("GET", f"/api/sales/campaigns/{campaign_id}", None), ("PATCH", f"/api/sales/campaigns/{campaign_id}", {"description": "x"}),
        ("DELETE", f"/api/sales/campaigns/{campaign_id}", None),
        ("GET", "/api/sales/leads", None), ("POST", "/api/sales/leads", lead_body()),
        ("POST", "/api/sales/leads/duplicate-check", {"phone": "+15550001111"}), ("GET", "/api/sales/leads/stage-counts", None),
        ("POST", "/api/sales/leads/bulk-assign", {"lead_ids": [lead_id], "assigned_to": PHANTOM_ID}),
        ("GET", f"/api/sales/leads/{lead_id}", None), ("PATCH", f"/api/sales/leads/{lead_id}", {"notes": "x"}),
        ("DELETE", f"/api/sales/leads/{lead_id}", None), ("POST", f"/api/sales/leads/{lead_id}/restore", None),
        ("POST", f"/api/sales/leads/{lead_id}/assign", {"assigned_to": PHANTOM_ID}), ("POST", f"/api/sales/leads/{lead_id}/unassign", None),
        ("POST", f"/api/sales/leads/{lead_id}/stage", {"stage": "lost", "lost_reason": "other"}),
        ("GET", f"/api/sales/leads/{lead_id}/activities", None),
    ]


class TestCustomerUsersAndAnonymousCallers:
    @pytest.mark.parametrize("who", ["owner", "employee"])
    def test_company_owners_and_employees_get_403_on_every_lead_route(self, who, owner_token, employee_token, p, world):
        token = owner_token if who == "owner" else employee_token
        for method, path, body in _every_phase_2_call(world["leads"]["L1"], world["campaign"]["id"]):
            r = api(method, path, auth(token), body)
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", f"{who} {method} {path} -> {r.status_code} {r.text[:120]}"

    @pytest.mark.parametrize("who", ["owner", "employee"])
    def test_customers_cannot_even_probe_with_garbage_bodies_and_leave_no_trace(self, who, owner_token, employee_token, p, world):
        token = owner_token if who == "owner" else employee_token
        body = lead_body()
        assert api("POST", "/api/sales/leads", auth(token), {"garbage": True}).status_code == 403
        assert api("POST", "/api/sales/leads", auth(token), body).status_code == 403
        assert api("GET", "/api/sales/leads", H(p, "manager"), None, params={"q": body["business_name"]}).json()["total"] == 0
        before = get_lead(H(p, "manager"), world["leads"]["L1"])
        api("PATCH", f"/api/sales/leads/{world['leads']['L1']}", auth(token), {"notes": "pwned"})
        api("DELETE", f"/api/sales/leads/{world['leads']['L1']}", auth(token))
        assert get_lead(H(p, "manager"), world["leads"]["L1"]) == before

    def test_anonymous_callers_are_refused(self, world):
        for method, path, body in _every_phase_2_call(world["leads"]["L1"], world["campaign"]["id"]):
            assert api(method, path, None, body).status_code in (401, 403), (method, path)

    def test_a_garbage_token_is_401(self):
        assert api("GET", "/api/sales/leads", auth("not-a-real-token")).status_code == 401


# =============================================================================
class TestDeactivatedAndRevokedStaff:
    def test_a_deactivated_employee_loses_access_to_everything_immediately(self, admin_h, p):
        manager = H(p, "manager")
        worker = create_staff(admin_h, ["sales_employee"], "to-deactivate")
        lead = create_lead(manager)
        assign(manager, lead["id"], worker["id"])
        h = worker["headers"]
        assert api("GET", f"/api/sales/leads/{lead['id']}", h).status_code == 200

        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"}).status_code == 200
        for method, path, body in [
            ("GET", "/api/sales/leads", None), ("GET", f"/api/sales/leads/{lead['id']}", None),
            ("PATCH", f"/api/sales/leads/{lead['id']}", {"notes": "x"}), ("POST", f"/api/sales/leads/{lead['id']}/stage", {"stage": "contacted"}),
            ("GET", "/api/sales/campaigns", None), ("GET", "/api/sales/me", None),
        ]:
            assert api(method, path, h, body).status_code == 403, (method, path)
        # the lead itself is untouched and still assigned to them (a manager reassigns it; nothing is silently moved)
        assert get_lead(manager, lead["id"])["assigned_to"]["id"] == worker["id"]
        assert get_lead(manager, lead["id"])["assigned_to"]["status"] == "inactive"

        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "active"}).status_code == 200
        assert api("GET", f"/api/sales/leads/{lead['id']}", h).status_code == 200      # reactivation restores it

    def test_a_revoked_role_removes_the_lead_permissions_at_once(self, admin_h, p):
        manager = H(p, "manager")
        worker = create_staff(admin_h, ["sales_employee"], "to-revoke")
        lead = create_lead(manager)
        assign(manager, lead["id"], worker["id"])
        h = worker["headers"]
        assert api("GET", "/api/sales/leads", h).status_code == 200

        assert api("DELETE", f"/api/sales/team/{worker['id']}/roles/sales_employee", admin_h).status_code == 200
        assert api("GET", "/api/sales/leads", h).status_code == 403
        assert api("GET", f"/api/sales/leads/{lead['id']}", h).status_code == 403
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "x"}).status_code == 403
        assert api("GET", "/api/sales/me", h).json()["permissions"] == []              # authenticated, holds nothing

        assert api("POST", f"/api/sales/team/{worker['id']}/roles", admin_h, {"role_key": "sales_employee"}).status_code == 200
        assert api("GET", f"/api/sales/leads/{lead['id']}", h).status_code == 200      # the lead was never moved away

    def test_a_revoked_manager_can_no_longer_assign_or_archive(self, admin_h, p):
        boss = create_staff(admin_h, ["sales_manager"], "boss")
        lead = create_lead(boss["headers"])
        assert api("DELETE", f"/api/sales/team/{boss['id']}/roles/sales_manager", admin_h).status_code == 200
        assert api("POST", f"/api/sales/leads/{lead['id']}/assign", boss["headers"], {"assigned_to": p["employee"]["id"]}).status_code == 403
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", boss["headers"]).status_code == 403
        assert get_lead(H(p, "manager"), lead["id"])["assigned_to"] is None and get_lead(H(p, "manager"), lead["id"])["archived_at"] is None


# =============================================================================
class TestCustomRolesAndUnions:
    def test_action_permissions_without_any_scope_reach_nothing(self, admin_h, p, world):
        role = make_custom_role(["sales.access", "sales.leads.view", "sales.leads.update", "sales.leads.change_stage", "sales.leads.create"])
        try:
            person = create_staff(admin_h, [role], "scopeless")
            h = person["headers"]
            page = api("GET", f"/api/sales/leads?campaign_id={world['campaign']['id']}", h)
            assert page.status_code == 200 and page.json()["total"] == 0                 # fail closed
            lid = world["leads"]["L1"]
            assert api("GET", f"/api/sales/leads/{lid}", h).status_code == 403
            assert api("PATCH", f"/api/sales/leads/{lid}", h, {"notes": "x"}).status_code == 403
            assert api("POST", f"/api/sales/leads/{lid}/stage", h, {"stage": "lost", "lost_reason": "other"}).status_code == 403
            created = api("POST", "/api/sales/leads", h, lead_body())                    # may create...
            assert created.status_code == 201
            assert api("GET", f"/api/sales/leads/{created.json()['id']}", h).status_code == 403   # ...but cannot see what it created
        finally:
            retire_roles([role])

    def test_a_scope_permission_alone_is_not_a_view_permission(self, admin_h):
        role = make_custom_role(["sales.access", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], "scope-only")
            assert api("GET", "/api/sales/leads", person["headers"]).status_code == 403
        finally:
            retire_roles([role])

    def test_holding_two_roles_unions_their_scopes_and_actions(self, admin_h, p, world):
        both = create_staff(admin_h, ["sales_employee", "lead_data_entry"], "employee-and-intake")
        h = both["headers"]
        mine = create_lead(H(p, "manager"))
        assign(H(p, "manager"), mine["id"], both["id"])
        page = api("GET", f"/api/sales/leads?campaign_id={world['campaign']['id']}&limit=100", h).json()
        seen = {n for n, lid in world["leads"].items() if lid in {i["id"] for i in page["items"]}}
        assert seen == {"L1", "L4", "L5"}                       # the intake scope (the unassigned leads); nothing else in this campaign is theirs
        assert api("GET", f"/api/sales/leads/{mine['id']}", h).status_code == 200            # the assigned scope
        assert api("POST", "/api/sales/leads", h, lead_body()).status_code == 201             # data entry's create
        assert api("POST", f"/api/sales/leads/{mine['id']}/stage", h, {"stage": "contacted"}).status_code == 200   # employee's stage change
        assert api("POST", f"/api/sales/leads/{mine['id']}/assign", h, {"assigned_to": p["employee"]["id"]}).status_code == 403   # still no assign

    def test_super_admin_is_never_limited_by_scope(self, admin_h, world):
        for lid in world["leads"].values():
            assert api("GET", f"/api/sales/leads/{lid}", admin_h).status_code == 200
        body = api("GET", "/api/sales/me", admin_h).json()
        assert {"sales.leads.scope_all", "sales.leads.override_duplicates", "sales.campaigns.manage"} <= set(body["permissions"])
