"""JAZ Sales - Phase 5: who may see which aggregate (HTTP integration tier).

The rule under test: an aggregate obeys the SAME scope as the records it counts, and comparing people is a team privilege.

  * the role matrix: every dashboard / report route x every persona (Sales Manager, Sales Employee, Lead Data Entry, Onboarding
    Employee, Super Admin, a staff account with no role) - and Company Owners / Employees, anonymous callers, deactivated staff,
    revoked and retired roles;
  * data isolation: an employee cannot see (or narrow to) another employee's numbers, not even through a one-person aggregate;
  * permission-driven sections: custom roles built from the same keys get exactly the sections their permissions allow
    (a section they may not see is null, never zero), and only permissions are consulted - never role names.

Behavior (what the numbers are) is in test_sales_reports_dashboard.py / _reports.py / _onboarding.py.
"""
import pytest

from sales_reports_test_utils import (
    api,
    assign,
    backdate_lead,
    build_world,
    create_lead,
    create_staff,
    dash,
    empty_window,
    make_custom_role,
    make_personas,
    person_row,
    report,
    retire_roles,
    window_params,
    wind_down,
)
from sales_customer_test_utils import onboarding_worker
from sales_test_utils import admin_token, assert_scratch_target, auth, employee_token, employee_user, owner_token, owner_user  # noqa: F401

ROUTES = {
    "dashboard": "dashboard",
    "leads": "reports/leads",
    "pipeline": "reports/pipeline",
    "employee-performance": "reports/employee-performance",
    "sources": "reports/sources",
    "campaigns": "reports/campaigns",
    "conversion": "reports/conversion",
    "activities": "reports/activities",
    "onboarding": "reports/onboarding",
}
ALL = set(ROUTES)
# the routes that take `employee_id` (the dashboard and every lead / work-item report; onboarding has its own `assigned_to`)
TAKES_EMPLOYEE = ["dashboard", "leads", "pipeline", "sources", "campaigns", "conversion", "activities", "employee-performance"]
# persona -> the routes it may open (200); every other route is a 403
MATRIX = {
    "admin": ALL,
    "manager": ALL,
    "employee": ALL - {"employee-performance", "onboarding"},          # no team comparison, no onboarding permission
    "employee2": ALL - {"employee-performance", "onboarding"},
    "data_entry": set(),                                               # metrics are opt-in: Lead Data Entry holds neither key
    "data_entry2": set(),
    "onboarding": set(),                                               # the RETIRED Onboarding Employee role reaches nothing
    "worker": {"dashboard", "onboarding"},                             # its keys via a custom role: the two doors, only onboarding
    "no_roles": set(),
}


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def personas(admin_h):
    out = make_personas(admin_h, "rb")
    out["worker"] = onboarding_worker(admin_h, "rb-worker")          # the dormant onboarding architecture
    return out


@pytest.fixture(scope="module")
def heads(admin_h, personas):
    return {"admin": admin_h, **{name: p["headers"] for name, p in personas.items()}}


@pytest.fixture(scope="module")
def world(admin_h, personas):
    made = build_world(admin_h, personas["manager"], personas["employee"], personas["employee2"])
    yield made
    wind_down(personas["manager"]["headers"], made.open_items)          # leave no open work in the shared scratch database


def status_of(headers, route, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return api("GET", f"/api/sales/{ROUTES[route]}" + (f"?{query}" if query else ""), headers).status_code


# =============================================================================
# the role matrix
# =============================================================================
class TestRoleMatrix:
    def test_the_matrix_covers_every_phase_5_route(self):
        from sales.router import sales_router

        found = {r.path for r in sales_router.routes if r.path == "/api/sales/dashboard" or r.path.startswith("/api/sales/reports")}
        assert found == {f"/api/sales/{p}" for p in ROUTES.values()}, found ^ {f"/api/sales/{p}" for p in ROUTES.values()}
        assert all(r.methods == {"GET"} for r in sales_router.routes if r.path in found)          # read-only: nothing to write, nothing to delete

    @pytest.mark.parametrize("persona", list(MATRIX))
    def test_each_persona_reaches_exactly_its_routes(self, heads, persona):
        for route in ROUTES:
            expected = 200 if route in MATRIX[persona] else 403
            assert status_of(heads[persona], route) == expected, f"{persona} -> {route}"

    def test_route_guards_are_the_documented_permission_keys(self):
        from sales.router import sales_router

        def keys(path):
            route = next(r for r in sales_router.routes if r.path == f"/api/sales/{path}")
            found = set()
            stack = list(route.dependant.dependencies)
            while stack:
                dep = stack.pop()
                found |= set(getattr(dep.call, "__sales_permissions__", ()))
                stack += dep.dependencies
            return found

        assert keys("dashboard") == {"sales.dashboard.view"}
        for name in ("leads", "pipeline", "sources", "conversion"):
            assert keys(f"reports/{name}") == {"sales.reports.view", "sales.leads.view"}, name
        assert keys("reports/employee-performance") == {"sales.reports.view", "sales.leads.view", "sales.leads.scope_all"}
        assert keys("reports/campaigns") == {"sales.reports.view", "sales.leads.view", "sales.campaigns.view"}
        assert keys("reports/activities") == {"sales.reports.view"}                                   # + the view permission of >= 1 kind, in the service
        assert keys("reports/onboarding") == {"sales.reports.view", "sales.onboarding.view"}


class TestNonStaffAndAnonymous:
    def test_company_owners_and_employees_get_403_everywhere(self, owner_token, employee_token):
        for headers in (auth(owner_token), auth(employee_token)):
            for route in ROUTES:
                assert status_of(headers, route) == 403, route

    def test_anonymous_and_garbage_tokens_get_nothing(self):
        for headers in (None, {}, {"Authorization": "Bearer not-a-token"}):
            for route in ROUTES:
                assert status_of(headers, route) in (401, 403), route

    def test_a_forbidden_caller_learns_nothing_from_the_error(self, heads):
        r = api("GET", "/api/sales/dashboard?employee_id=whatever&range=nope", heads["data_entry"])
        assert r.status_code == 403 and r.json() == {"detail": "Access denied"}        # not a 400 that would reveal the parameter rules


class TestAccountState:
    def test_a_deactivated_account_is_refused_immediately_and_regains_access_when_reactivated(self, admin_h):
        staff = create_staff(admin_h, ["sales_manager"], "rb-deact")
        assert status_of(staff["headers"], "dashboard") == 200
        assert api("PATCH", f"/api/sales/team/{staff['id']}", admin_h, {"status": "inactive"}).status_code == 200
        for route in ROUTES:
            assert status_of(staff["headers"], route) == 403, route                   # the token is still valid; the account is not
        assert api("PATCH", f"/api/sales/team/{staff['id']}", admin_h, {"status": "active"}).status_code == 200
        assert status_of(staff["headers"], "dashboard") == 200

    def test_revoking_the_role_closes_every_door(self, admin_h):
        staff = create_staff(admin_h, ["sales_manager"], "rb-revoke")
        assert status_of(staff["headers"], "leads") == 200
        assert api("DELETE", f"/api/sales/team/{staff['id']}/roles/sales_manager", admin_h).status_code == 200
        for route in ROUTES:
            assert status_of(staff["headers"], route) == 403, route

    def test_a_retired_role_stops_granting(self, admin_h):
        role = make_custom_role({"sales.access", "sales.dashboard.view", "sales.reports.view", "sales.leads.view", "sales.leads.scope_all"})
        try:
            staff = create_staff(admin_h, [role], "rb-retired")
            assert status_of(staff["headers"], "leads") == 200
            retire_roles([role])
            assert status_of(staff["headers"], "leads") == 403 and status_of(staff["headers"], "dashboard") == 403
        finally:
            retire_roles([role])


# =============================================================================
# data isolation: one employee cannot see another's numbers
# =============================================================================
class TestDataIsolation:
    @pytest.mark.parametrize("route", TAKES_EMPLOYEE)
    def test_an_employee_cannot_narrow_any_aggregate_to_a_colleague(self, world, personas, route):
        e1, e2 = personas["employee"], personas["employee2"]
        w = window_params(world.window)
        # employee-performance is closed to employees outright (403 either way); the rest refuse the colleague's id specifically
        assert status_of(e2["headers"], route, employee_id=e1["id"], **w) == 403, route
        assert status_of(e1["headers"], route, employee_id=e2["id"], **w) == 403, route
        assert status_of(e2["headers"], route, employee_id="00000000-0000-4000-8000-000000000000", **w) == 403, route     # an id that is nobody: same answer
        if route != "employee-performance":
            assert status_of(e2["headers"], route, employee_id="me", **w) == 200
            assert status_of(e2["headers"], route, employee_id=e2["id"], **w) == 200

    def test_an_employee_gets_a_403_not_a_quiet_zero_for_a_colleague(self, world, personas):
        r = api("GET", f"/api/sales/dashboard?employee_id={personas['employee']['id']}", personas["employee2"]["headers"])
        assert r.status_code == 403 and r.json() == {"detail": "Access denied"}       # a zero would tell them "your colleague has nothing"

    def test_a_manager_may_narrow_to_anybody_and_the_narrowing_is_reported(self, world, personas):
        body = dash(personas["manager"]["headers"], employee_id=personas["employee"]["id"], **window_params(world.window))
        assert body["scope"] == "team" and body["employee"]["id"] == personas["employee"]["id"]

    def test_every_lead_aggregate_of_an_employee_is_only_their_own(self, world, personas):
        e2 = personas["employee2"]["headers"]
        w = window_params(world.window)
        mine = world.cohort(owner="e2")                                                 # L6 L7 L8
        assert len(mine) == 3
        page = report(e2, "leads", limit=100, **w)
        assert {i["id"] for i in page["items"]} == {s.id for s in mine} and page["total"] == 3 and page["summary"]["total"] == 3
        assert all(i["assigned_to"]["id"] == personas["employee2"]["id"] for i in page["items"])
        assert report(e2, "pipeline", **w)["total_leads"] == 3
        assert report(e2, "pipeline", basis="all")["total_leads"] == 3                  # even the "every lead" basis is only the ones in scope
        assert sum(r["leads"] for r in report(e2, "sources", **w)["items"]) == 3
        assert sum(r["leads"] for r in report(e2, "campaigns", **w)["items"]) + report(e2, "campaigns", **w)["no_campaign"]["leads"] == 3
        c1_only = report(e2, "campaigns", campaign_id=world.campaigns["C1"], **w)
        assert [(r["campaign_id"], r["leads"]) for r in c1_only["items"]] == [(world.campaigns["C1"], 1)]        # C1 holds 4 leads in all; this employee's is 1
        assert report(e2, "conversion", **w)["cohort"]["leads"] == 3

    def test_the_employees_activity_is_only_their_own_work(self, world, personas):
        e2 = personas["employee2"]["headers"]
        body = report(e2, "activities", **window_params(world.window))
        assert body["activities"] == 5 and [r["employee"]["id"] for r in body["items"]] == [personas["employee2"]["id"]]
        # E1's seven activities are nowhere in it
        assert dash(e2, **window_params(world.window))["activity"]["total"] == 5

    def test_an_employees_dashboard_has_no_team_section_and_no_onboarding(self, world, personas):
        for who in ("employee", "employee2"):
            body = dash(personas[who]["headers"], **window_params(world.window))
            assert body["scope"] == "personal" and body["performance"] is None and body["onboarding"] is None

    def test_the_super_admin_and_the_manager_see_every_section(self, world, heads):
        for who in ("admin", "manager"):
            body = dash(heads[who], **window_params(world.window))
            for section in ("pipeline", "performance", "sources", "campaigns", "activity", "onboarding"):
                assert body[section] is not None, (who, section)
            assert all(v is not None for v in body["kpis"].values())                   # 11 leads entered the window, so even the rate exists


# =============================================================================
# sections follow the permissions of the records behind them
# =============================================================================
BASE = {"sales.access"}


def custom_persona(admin_h, label, keys):
    role = make_custom_role(BASE | set(keys))
    return {**create_staff(admin_h, [role], label), "role": role}


@pytest.fixture(scope="module")
def custom(admin_h):
    people = {
        "dashboard_only": custom_persona(admin_h, "rb-dashonly", {"sales.dashboard.view"}),
        "intake": custom_persona(admin_h, "rb-intake", {
            "sales.dashboard.view", "sales.reports.view", "sales.leads.view", "sales.leads.create", "sales.leads.scope_intake", "sales.campaigns.view",
        }),
        "team_no_work": custom_persona(admin_h, "rb-teamnowork", {"sales.dashboard.view", "sales.reports.view", "sales.leads.view", "sales.leads.scope_all"}),
        "no_scope": custom_persona(admin_h, "rb-noscope", {"sales.dashboard.view", "sales.reports.view", "sales.leads.view", "sales.campaigns.view"}),
        "followups_only": custom_persona(admin_h, "rb-fuonly", {"sales.reports.view", "sales.followups.view", "sales.leads.scope_all"}),
        "onboarding_no_scope": custom_persona(admin_h, "rb-onbnoscope", {"sales.dashboard.view", "sales.reports.view", "sales.onboarding.view"}),
        "onboarding_all": custom_persona(admin_h, "rb-onball", {"sales.dashboard.view", "sales.reports.view", "sales.onboarding.view", "sales.onboarding.scope_all"}),
    }
    yield people
    retire_roles([p["role"] for p in people.values()])


class TestPermissionDrivenSections:
    def test_a_dashboard_permission_alone_shows_nothing_but_the_frame(self, world, custom):
        h = custom["dashboard_only"]["headers"]
        body = dash(h, **window_params(world.window))
        assert all(v is None for v in body["kpis"].values())
        for section in ("pipeline", "performance", "sources", "campaigns", "activity", "onboarding"):
            assert body[section] is None, section                                       # null - never an empty object that would pass for zero
        for route in ROUTES:
            if route != "dashboard":
                assert status_of(h, route) == 403, route

    def test_a_data_entry_role_that_is_explicitly_given_metrics_sees_only_its_intake_scope(self, admin_h, personas, custom):
        """Intake scope = the leads the caller created + every unassigned lead. Four leads in an empty window: A (theirs,
        unassigned), C (theirs, later assigned away), D (somebody else's, unassigned) are in scope; B (somebody else's,
        assigned to an employee) is not."""
        de, mgr, emp = custom["intake"], personas["manager"]["headers"], personas["employee"]
        window = empty_window(admin_h)
        made = {
            "A": create_lead(de["headers"]),
            "C": create_lead(de["headers"]),
            "B": create_lead(mgr),
            "D": create_lead(mgr),
        }
        assign(mgr, made["C"]["id"], emp["id"])
        assign(mgr, made["B"]["id"], emp["id"])
        for lead in made.values():
            backdate_lead(lead["id"], window.lo + (window.hi - window.lo) / 2)
        w = window_params(window)

        assert dash(mgr, **w)["kpis"]["total_leads"] == 4                               # the team sees all four
        body = dash(de["headers"], **w)
        assert body["scope"] == "personal" and body["kpis"]["total_leads"] == 3
        assert sum(r["leads"] for r in body["pipeline"]) == 3 and sum(r["leads"] for r in body["sources"]) == 3
        assert body["performance"] is None                                              # not the team
        assert body["activity"] is None and body["kpis"]["overdue_followups"] is None    # no work-item permission
        page = report(de["headers"], "leads", limit=100, **w)
        assert {i["id"] for i in page["items"]} == {made["A"]["id"], made["C"]["id"], made["D"]["id"]}
        assert report(de["headers"], "pipeline", **w)["total_leads"] == 3
        # ... and cannot ask about the employee B was given to
        assert status_of(de["headers"], "leads", employee_id=emp["id"], **w) == 403
        # what they were not given stays closed
        assert status_of(de["headers"], "employee-performance", **w) == 403 and status_of(de["headers"], "onboarding") == 403
        assert status_of(de["headers"], "activities", **w) == 403                       # no kind of work may be viewed

    def test_a_team_caller_without_work_permissions_gets_the_lead_numbers_and_null_work(self, world, custom):
        h = custom["team_no_work"]["headers"]
        w = window_params(world.window)
        body = dash(h, **w)
        assert body["scope"] == "team" and body["kpis"]["total_leads"] == 11
        assert body["activity"] is None and body["campaigns"] is None and body["onboarding"] is None
        assert body["kpis"]["overdue_followups"] is None and body["kpis"]["active_trials"] is None and body["kpis"]["upcoming_demos"] is None
        row = person_row(body["performance"]["items"], world.owner_id("e1"))
        assert body["performance"]["total"] == 3                                        # E1, E2, the unassigned leads: no assignment privilege, so no idle staff listed
        assert row["leads"] == 7 and (row["calls"], row["followups"], row["demos"], row["trials"]) == (None, None, None, None)
        assert row["activities"] == 0                                                   # unknown is null, and it adds nothing
        assert status_of(h, "campaigns", **w) == 403 and status_of(h, "activities", **w) == 403        # no campaigns.view / no kind view
        assert status_of(h, "sources", **w) == 200 and status_of(h, "employee-performance", **w) == 200
        perf = report(h, "employee-performance", **w)
        assert perf["totals"]["leads"] == 11 and perf["totals"]["calls"] is None and perf["totals"]["activities"] == 0
        assert report(h, "conversion", **w)["customers"] is None                        # sales.customers.view is not held

    def test_an_action_permission_without_a_lead_scope_reaches_nothing(self, world, custom):
        h = custom["no_scope"]["headers"]
        w = window_params(world.window)
        body = dash(h, **w)
        assert body["kpis"]["total_leads"] == 0 and body["kpis"]["conversion_rate"] is None
        assert report(h, "leads", **w)["total"] == 0 and report(h, "pipeline", basis="all")["total_leads"] == 0     # fail closed, even for the live pipeline

    def test_only_the_kinds_of_work_the_caller_may_view_are_reported(self, world, custom):
        h = custom["followups_only"]["headers"]
        body = report(h, "activities", **window_params(world.window))
        assert body["kinds"]["followups"]["total"] == 3
        assert body["kinds"]["calls"] is None and body["kinds"]["demos"] is None and body["kinds"]["trials"] is None
        assert body["activities"] == 3
        row = person_row(body["items"], world.owner_id("e1"))
        assert row["followups"] == 2 and row["calls"] is None and row["activities"] == 2
        assert status_of(h, "leads") == 403 and status_of(h, "dashboard") == 403       # no lead view, no dashboard key

    def test_onboarding_needs_an_onboarding_scope_not_just_the_view_permission(self, custom):
        h = custom["onboarding_no_scope"]["headers"]
        assert status_of(h, "onboarding") == 403
        assert dash(h)["onboarding"] is None

    def test_a_caller_who_sees_every_onboarding_record_sees_the_unowned_ones_counted(self, custom):
        h = custom["onboarding_all"]["headers"]
        assert status_of(h, "onboarding") == 200
        section = dash(h)["onboarding"]
        assert isinstance(section["unassigned"], int) and section["total"] == sum(section["by_stage"].values())
        assert all(v is None for v in dash(h)["kpis"].values())                          # and no lead numbers at all
