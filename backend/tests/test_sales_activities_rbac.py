"""JAZ Sales - Phase 3: RBAC for calls, follow-ups, demos and trials (HTTP integration tier).

Two layers, tested separately and together (the same design as the lead routes):
  * ACTION permissions - sales.<kind>.view to read, sales.<kind>.manage to change, per kind of work item (403 otherwise,
    decided BEFORE validation, so a denied caller learns nothing from the error);
  * LEAD SCOPE - a work item is visible and actionable exactly when its lead is (a list is silently narrowed, one item
    on a lead outside the scope answers 403 and is never touched; holding no scope reaches nothing).
Plus: what each system role gets (Lead Data Entry and Onboarding Employee get NOTHING), who can read which events on a
lead's timeline, deactivated / revoked staff, custom roles, customer users and anonymous callers.
Personas are real accounts created through the real API by the seeded Super Admin.
"""
import pytest

from sales_activity_test_utils import (
    PHANTOM_ID,
    act,
    activities,
    assign,
    create_call,
    create_demo,
    create_followup,
    create_lead,
    create_staff,
    create_trial,
    fetch,
    get_lead,
    ids_of,
    later,
    listing,
    make_custom_role,
    make_personas,
    retire_roles,
    timeline_of,
    uniq,
    worked_lead,
)
from sales_test_utils import (  # noqa: F401  (the token fixtures mint instead of logging in)
    SYSTEM_ROLE_PERMISSIONS,
    admin_token,
    api,
    assert_scratch_target,
    auth,
    employee_token,
    employee_user,
    owner_token,
    owner_user,
)

KINDS = ("calls", "followups", "demos", "trials")
PHASE3_KEYS = {f"sales.{k}.{a}" for k in KINDS for a in ("view", "manage")}
ROLES = ["admin", "manager", "employee", "data_entry", "onboarding", "no_roles"]
ALLOWED = {"admin", "manager", "employee"}                       # hold every Phase-3 permission (Super Admin implicitly)


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "arbac")


def H(p, who):
    return p[who]["headers"]


def headers_for(p, admin_h, who):
    return admin_h if who == "admin" else H(p, who)


# =============================================================================
# what the system roles get
# =============================================================================
class TestWhatEachRoleGets:
    def test_the_grants_are_exactly_the_approved_ones(self):
        assert PHASE3_KEYS <= SYSTEM_ROLE_PERMISSIONS["sales_manager"] and PHASE3_KEYS <= SYSTEM_ROLE_PERMISSIONS["sales_employee"]
        for role in ("lead_data_entry", "onboarding_employee"):
            assert not PHASE3_KEYS & SYSTEM_ROLE_PERMISSIONS[role], role

    def test_me_reports_them(self, p, admin_h):
        for who, has in (("manager", True), ("employee", True), ("data_entry", False), ("onboarding", False), ("no_roles", False)):
            me = api("GET", "/api/sales/me", H(p, who)).json()
            granted = PHASE3_KEYS & set(me["permissions"])
            assert granted == (PHASE3_KEYS if has else set()), who                          # all of them, or none - never a partial grant
            assert (set(me["modules"]) >= {"followups", "calls", "demos", "trials"}) is has, who
            assert not ({"followups", "calls", "demos", "trials"} & set(me["modules"]) and not has), who
        me = api("GET", "/api/sales/me", admin_h).json()
        assert PHASE3_KEYS <= set(me["permissions"]) and {"followups", "calls", "demos", "trials"} <= set(me["modules"])


# =============================================================================
# the action-permission matrix
# =============================================================================
# name: (method, path template, body factory key)
ENDPOINTS = {
    "calls.list": ("GET", "/calls"),
    "calls.result_counts": ("GET", "/calls/result-counts"),
    "calls.get": ("GET", "/calls/{item}"),
    "calls.create": ("POST", "/calls"),
    "calls.update": ("PATCH", "/calls/{item}"),
    "followups.list": ("GET", "/followups"),
    "followups.counts": ("GET", "/followups/counts"),
    "followups.get": ("GET", "/followups/{item}"),
    "followups.create": ("POST", "/followups"),
    "followups.update": ("PATCH", "/followups/{item}"),
    "followups.complete": ("POST", "/followups/{item}/complete"),
    "followups.cancel": ("POST", "/followups/{item}/cancel"),
    "demos.list": ("GET", "/demos"),
    "demos.counts": ("GET", "/demos/counts"),
    "demos.get": ("GET", "/demos/{item}"),
    "demos.create": ("POST", "/demos"),
    "demos.update": ("PATCH", "/demos/{item}"),
    "demos.reschedule": ("POST", "/demos/{item}/reschedule"),
    "demos.complete": ("POST", "/demos/{item}/complete"),
    "demos.cancel": ("POST", "/demos/{item}/cancel"),
    "demos.no_show": ("POST", "/demos/{item}/no-show"),
    "trials.list": ("GET", "/trials"),
    "trials.counts": ("GET", "/trials/counts"),
    "trials.get": ("GET", "/trials/{item}"),
    "trials.create": ("POST", "/trials"),
    "trials.update": ("PATCH", "/trials/{item}"),
    "trials.complete": ("POST", "/trials/{item}/complete"),
    "trials.cancel": ("POST", "/trials/{item}/cancel"),
}


def needed_permission(name):
    kind = name.split(".")[0]
    return f"sales.{kind}.{'view' if ENDPOINTS[name][0] == 'GET' else 'manage'}"


def _new_item(kind, lead_id, manager_h, employee_id):
    if kind == "calls":
        return create_call(manager_h, lead_id)["id"]
    if kind == "followups":
        return create_followup(manager_h, lead_id, assigned_to=employee_id)["id"]
    if kind == "demos":
        return create_demo(manager_h, lead_id, assigned_to=employee_id)["id"]
    return create_trial(manager_h, lead_id)["id"]


def _request(p, name, assignee="employee"):
    """A fresh lead (assigned to the employee, so it is in the scope of Super Admin, manager and employee alike) plus - for
    routes on an existing item - a fresh item, so a 403 can only mean 'not permitted'. Follow-ups and demos are given to
    the employee (`assignee`), or - assignee=None - to the caller, which is all a role without sales.leads.assign may do."""
    method, template = ENDPOINTS[name]
    kind = name.split(".")[0]
    manager_h = H(p, "manager")
    lead = worked_lead(manager_h, p["employee"]["id"])
    item = _new_item(kind, lead["id"], manager_h, p["employee"]["id"]) if "{item}" in template else None
    named = {"assigned_to": p[assignee]["id"]} if assignee else {}
    body = {
        "calls.create": {"lead_id": lead["id"], "result": "answered"},
        "calls.update": {"notes": "matrix"},
        "followups.create": {"lead_id": lead["id"], "due_at": later(days=1), **named},
        "followups.update": {"notes": "matrix"},
        "demos.create": {"lead_id": lead["id"], "scheduled_at": later(days=2), **named},
        "demos.update": {"notes": "matrix"},
        "demos.reschedule": {"scheduled_at": later(days=3)},
        "trials.create": {"lead_id": lead["id"], "expected_end_at": later(days=14)},
        "trials.update": {"notes": "matrix"},
    }.get(name)
    return method, "/api/sales" + template.format(item=item), body


class TestActionPermissionMatrix:
    @pytest.mark.parametrize("name", list(ENDPOINTS))
    @pytest.mark.parametrize("who", ROLES)
    def test_endpoint_by_role(self, p, admin_h, who, name):
        method, path, body = _request(p, name)
        r = api(method, path, headers_for(p, admin_h, who), body)
        if who in ALLOWED:
            assert r.status_code in (200, 201), f"{who} must be allowed {name}: {r.status_code} {r.text[:200]}"
        else:
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", f"{who} must be denied {name}: {r.status_code} {r.text[:200]}"

    def test_the_matrix_covers_every_phase_3_route(self):
        from server import app
        routes = {(m, r.path.removeprefix("/api/sales")) for r in app.routes if getattr(r, "path", "").startswith("/api/sales/")
                  for m in r.methods if m not in ("HEAD", "OPTIONS")}
        phase3 = {(m, path) for m, path in routes if path.split("/")[1] in KINDS}
        covered = {(m, t.replace("{item}", "{" + t.split("/")[1].rstrip("s") + "_id}")) for m, t in ENDPOINTS.values()}
        # e.g. /calls/{item} <-> /calls/{call_id}, /followups/{item} <-> /followups/{followup_id}
        assert phase3 == covered, f"uncovered: {phase3 - covered}; stale: {covered - phase3}"

    def test_every_route_needs_exactly_the_documented_permission(self):
        from server import app
        by_route = {(m, r.path.removeprefix("/api/sales")): r for r in app.routes if getattr(r, "path", "").startswith("/api/sales/")
                    for m in r.methods if m not in ("HEAD", "OPTIONS")}
        walk = lambda dep: [dep] + [x for d in dep.dependencies for x in walk(d)]  # noqa: E731
        for name, (method, template) in ENDPOINTS.items():
            kind = name.split(".")[0]
            route = by_route[(method, template.replace("{item}", "{" + kind.rstrip("s") + "_id}"))]
            perms = [d.call.__sales_permissions__ for d in walk(route.dependant) if getattr(d.call, "__sales_guard__", None) == "permission"]
            assert perms == [(needed_permission(name),)], (name, perms)

    @pytest.mark.parametrize("who", ["data_entry", "onboarding", "no_roles"])
    def test_403_precedes_validation_for_callers_without_the_permission(self, p, who):
        h = H(p, who)
        for path in ("/calls", "/followups", "/demos", "/trials"):
            assert api("POST", "/api/sales" + path, h, {"garbage": True}).status_code == 403     # no schema leak
            assert api("GET", "/api/sales" + path + "?limit=0", h).status_code == 403
            assert api("GET", "/api/sales" + path + "?status=nonsense", h).status_code == 403
        for path in ("/followups/{i}/complete", "/demos/{i}/reschedule", "/trials/{i}/complete"):
            assert api("POST", "/api/sales" + path.format(i=PHANTOM_ID), h, {"garbage": True}).status_code == 403


# =============================================================================
# the finer grain: each kind, each of view / manage
# =============================================================================
class TestPermissionGranularity:
    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize("held", ["view", "manage"])
    def test_a_role_holding_one_permission_of_one_kind_gets_exactly_that(self, admin_h, p, kind, held):
        role = make_custom_role([f"sales.{kind}.{held}", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], f"gran-{kind}-{held}")
            for name in ENDPOINTS:
                method, path, body = _request(p, name, assignee=None)
                r = api(method, path, person["headers"], body)
                should_pass = name.startswith(kind + ".") and needed_permission(name).endswith("." + held)
                if should_pass:
                    assert r.status_code in (200, 201), f"{kind}.{held} must allow {name}: {r.status_code} {r.text[:200]}"
                else:
                    assert r.status_code == 403, f"{kind}.{held} must NOT allow {name}: {r.status_code} {r.text[:200]}"
        finally:
            retire_roles([role])

    def test_manage_does_not_imply_view_and_view_does_not_imply_manage(self, admin_h, p):
        role = make_custom_role(["sales.calls.manage", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], "gran-manageonly")
            lead = worked_lead(H(p, "manager"), p["employee"]["id"])
            call = create_call(person["headers"], lead["id"])                                    # may log a call ...
            assert api("PATCH", f"/api/sales/calls/{call['id']}", person["headers"], {"notes": "x"}).status_code == 200
            assert api("GET", f"/api/sales/calls/{call['id']}", person["headers"]).status_code == 403     # ... but not read calls back
            assert api("GET", "/api/sales/calls", person["headers"]).status_code == 403
        finally:
            retire_roles([role])

    def test_permissions_of_several_roles_add_up(self, admin_h, p):
        calls_role = make_custom_role(["sales.calls.view", "sales.leads.scope_all"])
        demos_role = make_custom_role(["sales.demos.manage"])
        try:
            person = create_staff(admin_h, [calls_role, demos_role], "gran-union")
            lead = worked_lead(H(p, "manager"), p["employee"]["id"])
            assert api("GET", "/api/sales/calls", person["headers"]).status_code == 200
            # demos.manage comes from one role, the scope from the other: the union makes them eligible for their own demo
            assert api("POST", "/api/sales/demos", person["headers"], {"lead_id": lead["id"], "scheduled_at": later(days=1)}).status_code == 201
        finally:
            retire_roles([calls_role, demos_role])


# =============================================================================
# lead scope
# =============================================================================
@pytest.fixture(scope="module")
def world(p):
    """Four leads, each holding one item of every kind (made by the manager), that between them exercise every scope."""
    manager, token = H(p, "manager"), uniq(12)       # the token isolates this world's items from every other test's, in cross-lead lists
    named = lambda name: {"business_name": f"Scoped {token} {name}"}  # noqa: E731
    leads = {
        "L1": create_lead(manager, **named("L1")),                                 # manager-created, unassigned
        "L2": create_lead(manager, **named("L2")),                                 # -> employee
        "L3": create_lead(manager, **named("L3")),                                 # -> employee2
        "L4": create_lead(H(p, "data_entry"), **named("L4")),                      # data_entry-created, unassigned
    }
    assign(manager, leads["L2"]["id"], p["employee"]["id"])
    assign(manager, leads["L3"]["id"], p["employee2"]["id"])
    items = {}
    for name, lead in leads.items():
        holder = {"L2": p["employee"]["id"], "L3": p["employee2"]["id"]}.get(name)
        items[name] = {
            "calls": create_call(manager, lead["id"])["id"],
            "followups": create_followup(manager, lead["id"], assigned_to=holder)["id"] if holder else create_followup(manager, lead["id"])["id"],
            "demos": create_demo(manager, lead["id"], assigned_to=holder)["id"] if holder else create_demo(manager, lead["id"])["id"],
            "trials": create_trial(manager, lead["id"])["id"],
        }
    return {"token": token, "leads": {k: v["id"] for k, v in leads.items()}, "items": items}


# who sees which leads' work items - the whole specification of Phase-3 visibility in one table
VISIBLE = {
    "admin": {"L1", "L2", "L3", "L4"},
    "manager": {"L1", "L2", "L3", "L4"},
    "employee": {"L2"},                       # only what is on the leads assigned to them
    "employee2": {"L3"},
}
NO_ACCESS = ["data_entry", "data_entry2", "onboarding", "no_roles"]
LISTS = {"calls": "/api/sales/calls", "followups": "/api/sales/followups", "demos": "/api/sales/demos", "trials": "/api/sales/trials"}


class TestLeadScope:
    @pytest.mark.parametrize("who", list(VISIBLE))
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_list_contains_exactly_the_items_in_scope(self, p, admin_h, world, who, kind):
        h = headers_for(p, admin_h, who)
        mine = {world["items"][name][kind]: name for name in world["items"]}
        for lead_name, lead_id in world["leads"].items():
            page = listing(h, f"{LISTS[kind]}?lead_id={lead_id}")                            # an out-of-scope lead: silently empty, never an error
            assert (page["total"] == 1) == (lead_name in VISIBLE[who]), (who, kind, lead_name)
            assert ids_of(page) == ([world["items"][lead_name][kind]] if lead_name in VISIBLE[who] else [])
        everything = listing(h, f"{LISTS[kind]}?q={world['token']}&limit=100")            # the cross-lead list agrees
        assert {mine[i] for i in ids_of(everything)} == VISIBLE[who] and everything["total"] == len(VISIBLE[who])

    @pytest.mark.parametrize("who", list(VISIBLE))
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_single_item_is_readable_only_inside_the_scope(self, p, admin_h, world, who, kind):
        h = headers_for(p, admin_h, who)
        for lead_name, items in world["items"].items():
            r = api("GET", f"/api/sales/{kind}/{items[kind]}", h)
            if lead_name in VISIBLE[who]:
                assert r.status_code == 200, (who, kind, lead_name)
            else:
                assert r.status_code == 403 and r.json()["detail"] == "Access denied", (who, kind, lead_name)

    @pytest.mark.parametrize("who", list(VISIBLE))
    def test_counts_only_count_what_the_caller_may_see(self, p, admin_h, world, who):
        h = headers_for(p, admin_h, who)
        for lead_name, lead_id in world["leads"].items():
            inside = lead_name in VISIBLE[who]
            assert listing(h, f"/api/sales/followups/counts?lead_id={lead_id}")["pending"] == (1 if inside else 0)
            assert listing(h, f"/api/sales/demos/counts?lead_id={lead_id}")["scheduled"] == (1 if inside else 0)
            assert listing(h, f"/api/sales/trials/counts?lead_id={lead_id}")["active"] == (1 if inside else 0)
            assert listing(h, f"/api/sales/calls/result-counts?lead_id={lead_id}")["total"] == (1 if inside else 0)

    @pytest.mark.parametrize("who", ["employee", "employee2"])
    def test_filters_cannot_widen_the_scope(self, p, world, who):
        """Asking for somebody else's work, or for everything, still returns only what the caller may see."""
        h = H(p, who)
        for person in ("manager", "employee", "employee2"):
            queries = {
                "followups": f"assigned_to={p[person]['id']}", "demos": f"assigned_to={p[person]['id']}",
                "calls": f"employee_id={p[person]['id']}", "trials": "ending_within_days=30",
            }
            for kind, query in queries.items():
                ids = set(ids_of(listing(h, f"{LISTS[kind]}?q={world['token']}&limit=100&{query}")))
                assert ids <= {world["items"][n][kind] for n in VISIBLE[who]}, (who, kind, query)
        for kind in KINDS:
            everything = set(ids_of(listing(h, f"{LISTS[kind]}?q={world['token']}&limit=100")))
            assert everything == {world["items"][n][kind] for n in VISIBLE[who]}

    @pytest.mark.parametrize("who", ["employee", "employee2"])
    def test_searching_by_an_outsiders_lead_details_reveals_nothing(self, p, world, who):
        other = "L3" if who == "employee" else "L2"
        lead = get_lead(H(p, "manager"), world["leads"][other])
        for kind in KINDS:
            assert listing(H(p, who), f"{LISTS[kind]}?q={lead['business_name'].split()[-1]}")["total"] == 0, kind

    def test_an_employee_cannot_act_on_another_employees_items_and_nothing_changes(self, p, world):
        h = H(p, "employee")
        theirs = world["items"]["L3"]                                                     # employee2's lead
        attempts = [
            ("PATCH", f"/api/sales/calls/{theirs['calls']}", {"notes": "hijack"}),
            ("PATCH", f"/api/sales/followups/{theirs['followups']}", {"notes": "hijack"}),
            ("POST", f"/api/sales/followups/{theirs['followups']}/complete", None),
            ("POST", f"/api/sales/followups/{theirs['followups']}/cancel", None),
            ("PATCH", f"/api/sales/demos/{theirs['demos']}", {"notes": "hijack"}),
            ("POST", f"/api/sales/demos/{theirs['demos']}/reschedule", {"scheduled_at": later(days=9)}),
            ("POST", f"/api/sales/demos/{theirs['demos']}/complete", None),
            ("POST", f"/api/sales/demos/{theirs['demos']}/cancel", None),
            ("POST", f"/api/sales/demos/{theirs['demos']}/no-show", None),
            ("PATCH", f"/api/sales/trials/{theirs['trials']}", {"notes": "hijack"}),
            ("POST", f"/api/sales/trials/{theirs['trials']}/complete", None),
            ("POST", f"/api/sales/trials/{theirs['trials']}/cancel", None),
        ]
        for method, path, body in attempts:
            r = api(method, path, h, body)
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", (method, path)
        manager = H(p, "manager")
        assert fetch(manager, "calls", theirs["calls"])["notes"] is None
        assert fetch(manager, "followups", theirs["followups"])["status"] == "pending"
        assert fetch(manager, "demos", theirs["demos"])["status"] == "scheduled"
        assert fetch(manager, "trials", theirs["trials"])["status"] == "active"

    def test_creating_work_on_a_lead_outside_the_scope_is_refused_and_creates_nothing(self, p, world):
        h = H(p, "employee")
        lead = world["leads"]["L3"]
        for path, body in (
            ("/api/sales/calls", {"lead_id": lead, "result": "busy"}),
            ("/api/sales/followups", {"lead_id": lead, "due_at": later(days=1)}),
            ("/api/sales/demos", {"lead_id": lead, "scheduled_at": later(days=1)}),
            ("/api/sales/trials", {"lead_id": lead, "expected_end_at": later(days=5)}),
        ):
            assert api("POST", path, h, body).status_code == 403, path
        for kind in KINDS:
            assert listing(H(p, "manager"), f"{LISTS[kind]}?lead_id={lead}")["total"] == 1                      # still only the manager's one

    @pytest.mark.parametrize("who", NO_ACCESS)
    def test_callers_without_the_phase_3_permissions_reach_nothing(self, p, world, who):
        h = H(p, who)
        for kind in KINDS:
            assert api("GET", LISTS[kind], h).status_code == 403
            for items in world["items"].values():
                assert api("GET", f"/api/sales/{kind}/{items[kind]}", h).status_code == 403


class TestCustomScopes:
    """The same scope predicate as the leads (services/lead_access), whichever role it comes from."""

    def _person(self, admin_h, scopes, label):
        role = make_custom_role([*(f"sales.{k}.{a}" for k in KINDS for a in ("view", "manage")), *scopes])
        return create_staff(admin_h, [role], label), role

    def test_scope_all_sees_everything_scope_assigned_only_the_owned_scope_intake_the_intake_pool(self, p, admin_h, world):
        expectations = {
            ("sales.leads.scope_all",): {"L1", "L2", "L3", "L4"},
            ("sales.leads.scope_intake",): {"L1", "L4"},                                 # unassigned leads (+ ones they created: none here)
            ("sales.leads.scope_assigned", "sales.leads.scope_intake"): {"L1", "L4"},    # nothing is assigned to this person, so the union adds nothing
            (): set(),                                                                    # no scope = fail closed
        }
        roles = []
        try:
            for scopes, visible in expectations.items():
                person, role = self._person(admin_h, scopes, "scope-" + "-".join(s.rsplit("_", 1)[-1] for s in scopes) if scopes else "scope-none")
                roles.append(role)
                for kind in KINDS:
                    got = {name for name, lead_id in world["leads"].items() if listing(person["headers"], f"{LISTS[kind]}?lead_id={lead_id}")["total"] == 1}
                    assert got == visible, (scopes, kind)
                    for name, items in world["items"].items():
                        status = api("GET", f"/api/sales/{kind}/{items[kind]}", person["headers"]).status_code
                        assert status == (200 if name in visible else 403), (scopes, kind, name)
        finally:
            retire_roles(roles)

    def test_a_role_with_scope_assigned_sees_the_leads_assigned_to_that_person(self, p, admin_h):
        person, role = self._person(admin_h, ("sales.leads.scope_assigned",), "scope-assigned-owner")
        try:
            lead = create_lead(H(p, "manager"))
            call = create_call(H(p, "manager"), lead["id"])
            assert api("GET", f"/api/sales/calls/{call['id']}", person["headers"]).status_code == 403
            assign(H(p, "manager"), lead["id"], person["id"])
            assert api("GET", f"/api/sales/calls/{call['id']}", person["headers"]).status_code == 200      # the lead moved into their scope, and the call with it
        finally:
            retire_roles([role])


# =============================================================================
# the timeline: who may read which events
# =============================================================================
@pytest.fixture(scope="module")
def busy_lead(p):
    """A lead a Lead Data Entry user created (so they can still open it after it is assigned), then worked by an employee."""
    manager, entry, employee = H(p, "manager"), H(p, "data_entry"), H(p, "employee")
    lead = create_lead(entry)
    assign(manager, lead["id"], p["employee"]["id"])
    create_call(employee, lead["id"])
    f = create_followup(employee, lead["id"])
    act(employee, "followups", f["id"], "complete")
    d = create_demo(employee, lead["id"])
    act(employee, "demos", d["id"], "cancel")
    create_trial(employee, lead["id"])
    return lead


class TestTimelineVisibility:
    LEAD_EVENTS = ["lead_created", "lead_assigned", "stage_changed"]
    WORK_EVENTS = ["call_created", "followup_created", "followup_completed", "demo_scheduled", "demo_cancelled", "trial_started"]

    def _types(self, h, lead_id, limit=200):
        return [e["event_type"] for e in reversed(listing(h, f"/api/sales/leads/{lead_id}/activities?limit={limit}")["items"])]

    def test_everyone_with_the_permissions_sees_the_whole_timeline(self, p, admin_h, busy_lead):
        for who in ("manager", "employee"):
            assert self._types(H(p, who), busy_lead["id"]) == self.LEAD_EVENTS + self.WORK_EVENTS, who
        assert self._types(admin_h, busy_lead["id"]) == self.LEAD_EVENTS + self.WORK_EVENTS

    def test_a_lead_data_entry_user_sees_the_leads_own_events_but_none_of_the_work_done_on_it(self, p, busy_lead):
        entry = H(p, "data_entry")
        assert api("GET", f"/api/sales/leads/{busy_lead['id']}", entry).status_code == 200            # they may open the lead they created ...
        page = listing(entry, f"/api/sales/leads/{busy_lead['id']}/activities?limit=200")
        assert [e["event_type"] for e in reversed(page["items"])] == self.LEAD_EVENTS               # ... and read its own history ...
        assert page["total"] == 3                                                                    # ... and the total counts only what they may see
        assert api("GET", f"/api/sales/calls?lead_id={busy_lead['id']}", entry).status_code == 403   # ... not the calls behind it
        assert not any(e["event_type"].split("_")[0] in ("call", "followup", "demo", "trial") for e in page["items"])

    def test_pagination_of_a_filtered_timeline_is_consistent(self, p, busy_lead):
        entry = H(p, "data_entry")
        first = listing(entry, f"/api/sales/leads/{busy_lead['id']}/activities?limit=2&offset=0")
        second = listing(entry, f"/api/sales/leads/{busy_lead['id']}/activities?limit=2&offset=2")
        assert first["total"] == second["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
        assert not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]}
        employee = H(p, "employee")
        pages = [listing(employee, f"/api/sales/leads/{busy_lead['id']}/activities?limit=4&offset={o}") for o in (0, 4, 8)]
        assert [len(pg["items"]) for pg in pages] == [4, 4, 1] and {pg["total"] for pg in pages} == {9}

    @pytest.mark.parametrize("kind,event_prefix", [("calls", "call_"), ("followups", "followup_"), ("demos", "demo_"), ("trials", "trial_")])
    def test_each_kind_of_event_needs_its_own_view_permission(self, admin_h, busy_lead, kind, event_prefix):
        role = make_custom_role([f"sales.{kind}.view", "sales.leads.view", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], f"tl-{kind}")
            types = self._types(person["headers"], busy_lead["id"])
            assert [t for t in types if t.split("_")[0] in ("call", "followup", "demo", "trial")] == [t for t in self.WORK_EVENTS if t.startswith(event_prefix)]
            assert [t for t in types if t in self.LEAD_EVENTS] == self.LEAD_EVENTS                # the lead's own events: always
        finally:
            retire_roles([role])

    def test_a_reader_who_may_see_the_lead_but_no_work_sees_only_the_leads_events(self, admin_h, busy_lead):
        role = make_custom_role(["sales.leads.view", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], "tl-none")
            assert self._types(person["headers"], busy_lead["id"]) == self.LEAD_EVENTS
        finally:
            retire_roles([role])

    def test_the_work_events_are_immutable_like_every_other_event(self, p, busy_lead):
        """Nothing in the API can edit or delete an event, and the database refuses too (see test_sales_activities_db)."""
        for method in ("PATCH", "PUT", "DELETE", "POST"):
            r = api(method, f"/api/sales/leads/{busy_lead['id']}/activities", H(p, "manager"), {})
            assert r.status_code == 405


# =============================================================================
# staff whose access changed
# =============================================================================
class TestStaffWhoseAccessChanged:
    def _try_everything(self, h, lead):
        return {
            "calls.list": api("GET", "/api/sales/calls", h).status_code,
            "calls.create": api("POST", "/api/sales/calls", h, {"lead_id": lead, "result": "busy"}).status_code,
            "followups.list": api("GET", "/api/sales/followups", h).status_code,
            "demos.create": api("POST", "/api/sales/demos", h, {"lead_id": lead, "scheduled_at": later(days=1)}).status_code,
            "trials.create": api("POST", "/api/sales/trials", h, {"lead_id": lead, "expected_end_at": later(days=5)}).status_code,
        }

    def test_a_deactivated_employee_is_denied_everywhere_at_once_even_with_a_valid_token(self, admin_h, p):
        worker = create_staff(admin_h, ["sales_employee"], "arbac-deactivated")
        lead = create_lead(H(p, "manager"))
        assign(H(p, "manager"), lead["id"], worker["id"])
        assert self._try_everything(worker["headers"], lead["id"]) == {"calls.list": 200, "calls.create": 201, "followups.list": 200, "demos.create": 201, "trials.create": 201}
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert set(self._try_everything(worker["headers"], lead["id"]).values()) == {403}
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "active"}).status_code == 200
        assert self._try_everything(worker["headers"], lead["id"])["calls.list"] == 200

    def test_an_employee_whose_role_was_revoked_loses_access_and_regains_it_when_regranted(self, admin_h, p):
        worker = create_staff(admin_h, ["sales_employee"], "arbac-revoked")
        lead = create_lead(H(p, "manager"))
        assign(H(p, "manager"), lead["id"], worker["id"])
        call = create_call(worker["headers"], lead["id"])
        assert api("DELETE", f"/api/sales/team/{worker['id']}/roles/sales_employee", admin_h).status_code == 200
        for method, path, body in (("GET", "/api/sales/calls", None), ("GET", f"/api/sales/calls/{call['id']}", None),
                                   ("PATCH", f"/api/sales/calls/{call['id']}", {"notes": "x"}), ("POST", "/api/sales/trials", {"lead_id": lead["id"], "expected_end_at": later(days=5)})):
            assert api(method, path, worker["headers"], body).status_code == 403, path
        assert api("POST", f"/api/sales/team/{worker['id']}/roles", admin_h, {"role_key": "sales_employee"}).status_code == 200
        assert api("GET", f"/api/sales/calls/{call['id']}", worker["headers"]).status_code == 200

    def test_a_retired_custom_role_stops_granting(self, admin_h, p):
        role = make_custom_role(["sales.calls.view", "sales.leads.scope_all"])
        person = create_staff(admin_h, [role], "arbac-retired")
        assert api("GET", "/api/sales/calls", person["headers"]).status_code == 200
        retire_roles([role])
        assert api("GET", "/api/sales/calls", person["headers"]).status_code == 403

    def test_permissions_are_read_live_on_every_request(self, admin_h, p):
        """Permissions are read from the database on every request - nothing is cached in the token."""
        role = make_custom_role(["sales.followups.manage", "sales.followups.view", "sales.leads.scope_all"])
        person = create_staff(admin_h, [role], "arbac-live")
        lead = worked_lead(H(p, "manager"), p["employee"]["id"])
        assert api("POST", "/api/sales/followups", person["headers"], {"lead_id": lead["id"], "due_at": later(days=1)}).status_code == 201
        retire_roles([role])
        assert api("POST", "/api/sales/followups", person["headers"], {"lead_id": lead["id"], "due_at": later(days=1)}).status_code == 403


# =============================================================================
# callers who are not internal staff
# =============================================================================
class TestNotStaff:
    def test_company_owners_and_employees_are_denied_on_every_phase_3_route(self, p, owner_token, employee_token):
        ids = {"item": PHANTOM_ID}
        for token in (owner_token, employee_token):
            h = auth(token)
            for name, (method, template) in ENDPOINTS.items():
                r = api(method, "/api/sales" + template.format(**ids), h, {"garbage": True} if method != "GET" else None)
                assert r.status_code == 403 and r.json()["detail"] == "Access denied", (name, r.status_code)

    def test_anonymous_callers_get_nothing(self):
        for name, (method, template) in ENDPOINTS.items():
            r = api(method, "/api/sales" + template.format(item=PHANTOM_ID), None, {} if method != "GET" else None)
            assert r.status_code in (401, 403), (name, r.status_code)
        r = api("GET", "/api/sales/calls", {"Authorization": "Bearer not-a-token"})
        assert r.status_code in (401, 403)
