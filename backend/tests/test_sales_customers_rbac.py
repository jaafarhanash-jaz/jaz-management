"""JAZ Sales - Phase 4: RBAC for conversion, customers and onboarding (HTTP integration tier).

Two layers, tested separately and together (the same design as the lead and work-item routes):
  * ACTION permissions - sales.customers.view / .convert, sales.onboarding.view / .manage / .assign (403 otherwise,
    decided BEFORE validation, so a denied caller learns nothing from the error);
  * ROW SCOPE - customers and conversion follow the LEAD scope (a customer is visible exactly when its lead is); onboarding
    has its OWN scope (scope_all / scope_assigned), independent of the lead scope. Holding no scope reaches nothing.
Plus: what each system role gets, deactivated / revoked staff, custom roles, customer (company) users and anonymous callers.
Personas are real accounts created through the real API by the seeded Super Admin.
"""
import pytest

from sales_customer_test_utils import (
    PHANTOM_ID,
    api,
    assign,
    assign_onboarding,
    convert,
    convert_body,
    converted,
    count_customers,
    count_users,
    create_lead,
    create_staff,
    customer,
    listing,
    make_custom_role,
    make_personas,
    onboarding,
    onboarding_id,
    retire_roles,
    set_stage,
    uniq,
    won_lead,
)
from sales_test_utils import (  # noqa: F401  (the token fixtures mint instead of logging in)
    SYSTEM_ROLE_PERMISSIONS,
    admin_token,
    assert_scratch_target,
    auth,
    employee_token,
    employee_user,
    owner_token,
    owner_user,
)

PHASE4_KEYS = {
    "sales.customers.view", "sales.customers.convert", "sales.onboarding.view", "sales.onboarding.manage",
    "sales.onboarding.assign", "sales.onboarding.scope_all", "sales.onboarding.scope_assigned",
}
ROLES = ["admin", "manager", "employee", "data_entry", "onboarding", "no_roles"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "crbac")


def H(p, who):
    return p[who]["headers"]


def headers_for(p, admin_h, who):
    return admin_h if who == "admin" else H(p, who)


@pytest.fixture(scope="module")
def world(p, admin_h):
    """One converted customer whose lead is the Sales Employee's and whose onboarding is assigned to the Onboarding Employee."""
    mgr = H(p, "manager")
    made = converted(mgr, p["employee"]["id"])
    oid = onboarding_id(mgr, made["customer"]["id"])
    assign_onboarding(mgr, oid, p["onboarding"]["id"])
    return {"lead": made["lead"], "customer": made["customer"], "oid": oid, "body": made["body"]}


# =============================================================================
# what the system roles get
# =============================================================================
class TestWhatEachRoleGets:
    def test_the_grants_are_exactly_the_approved_ones(self):
        assert PHASE4_KEYS & SYSTEM_ROLE_PERMISSIONS["sales_manager"] == PHASE4_KEYS - {"sales.onboarding.scope_assigned"}
        assert PHASE4_KEYS & SYSTEM_ROLE_PERMISSIONS["sales_employee"] == {"sales.customers.view"}                # NO conversion for a Sales Employee
        assert PHASE4_KEYS & SYSTEM_ROLE_PERMISSIONS["onboarding_employee"] == {"sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"}
        assert not PHASE4_KEYS & SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]                                       # Lead Data Entry: nothing

    def test_me_reports_them(self, p, admin_h):
        expected = {
            "manager": (PHASE4_KEYS - {"sales.onboarding.scope_assigned"}, {"customers", "onboarding"}),
            "employee": ({"sales.customers.view"}, {"customers"}),
            "onboarding": ({"sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"}, {"onboarding"}),
            "data_entry": (set(), set()),
            "no_roles": (set(), set()),
        }
        for who, (keys, modules) in expected.items():
            me = api("GET", "/api/sales/me", H(p, who)).json()
            assert PHASE4_KEYS & set(me["permissions"]) == keys, who
            assert {"customers", "onboarding"} & set(me["modules"]) == modules, who
        me = api("GET", "/api/sales/me", admin_h).json()
        assert PHASE4_KEYS <= set(me["permissions"]) and {"customers", "onboarding"} <= set(me["modules"])          # Super Admin: everything



# =============================================================================
# the action-permission matrix
# =============================================================================
# name: (method, path template, roles allowed, permission needed)
ENDPOINTS = {
    "conversion.status": ("GET", "/leads/{lead}/conversion", {"admin", "manager", "employee"}, "sales.customers.view"),
    "conversion.preflight": ("POST", "/leads/{lead}/convert/preflight", {"admin", "manager"}, "sales.customers.convert"),
    "conversion.convert": ("POST", "/leads/{lead}/convert", {"admin", "manager"}, "sales.customers.convert"),
    "customers.list": ("GET", "/customers", {"admin", "manager", "employee"}, "sales.customers.view"),
    "customers.counts": ("GET", "/customers/counts", {"admin", "manager", "employee"}, "sales.customers.view"),
    "customers.get": ("GET", "/customers/{customer}", {"admin", "manager", "employee"}, "sales.customers.view"),
    "onboarding.list": ("GET", "/onboarding", {"admin", "manager", "onboarding"}, "sales.onboarding.view"),
    "onboarding.counts": ("GET", "/onboarding/counts", {"admin", "manager", "onboarding"}, "sales.onboarding.view"),
    "onboarding.assignees": ("GET", "/onboarding/assignees", {"admin", "manager"}, "sales.onboarding.assign"),
    "onboarding.get": ("GET", "/onboarding/{ob}", {"admin", "manager", "onboarding"}, "sales.onboarding.view"),
    "onboarding.timeline": ("GET", "/onboarding/{ob}/timeline", {"admin", "manager", "onboarding"}, "sales.onboarding.view"),
    "onboarding.update": ("PATCH", "/onboarding/{ob}", {"admin", "manager", "onboarding"}, "sales.onboarding.manage"),
    "onboarding.assign": ("POST", "/onboarding/{ob}/assign", {"admin", "manager"}, "sales.onboarding.assign"),
    "onboarding.stage": ("POST", "/onboarding/{ob}/stage", {"admin", "manager", "onboarding"}, "sales.onboarding.manage"),
}


def _request_for(p, world, name):
    method, template, _, _ = ENDPOINTS[name]
    path = "/api/sales" + template.format(lead=world["lead"]["id"], customer=world["customer"]["id"], ob=world["oid"])
    body = {
        "conversion.preflight": {},
        "conversion.convert": convert_body(world["lead"]),
        "onboarding.update": {"notes": "rbac"},
        "onboarding.assign": {"assigned_to": p["onboarding"]["id"]},
        "onboarding.stage": {"stage": "contacted"},
    }.get(name)
    return method, path, body


class TestActionPermissionMatrix:
    @pytest.mark.parametrize("name", list(ENDPOINTS))
    @pytest.mark.parametrize("who", ROLES)
    def test_endpoint_by_role(self, p, admin_h, world, who, name):
        method, path, body = _request_for(p, world, name)
        r = api(method, path, headers_for(p, admin_h, who), body)
        if who in ENDPOINTS[name][2]:
            assert r.status_code != 403, f"{who} must be allowed {name}: {r.status_code} {r.text[:200]}"
            assert r.status_code < 500
        else:
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", f"{who} must be denied {name}: {r.status_code} {r.text[:200]}"

    def test_the_matrix_covers_every_phase_4_route(self):
        from server import app
        routes = {(m, r.path.removeprefix("/api/sales")) for r in app.routes if getattr(r, "path", "").startswith("/api/sales/")
                  for m in r.methods if m not in ("HEAD", "OPTIONS")}
        phase4 = {(m, path) for m, path in routes if path.split("/")[1] in ("customers", "onboarding")
                  or path.startswith(("/leads/{lead_id}/convert", "/leads/{lead_id}/conversion"))}
        covered = {(m, t.replace("{lead}", "{lead_id}").replace("{customer}", "{customer_id}").replace("{ob}", "{onboarding_id}")) for m, t, _, _ in ENDPOINTS.values()}
        assert phase4 == covered, f"uncovered: {phase4 - covered}; stale: {covered - phase4}"
        assert len(phase4) == 14

    def test_every_route_needs_exactly_the_documented_permission(self):
        from server import app
        by_route = {(m, r.path.removeprefix("/api/sales")): r for r in app.routes if getattr(r, "path", "").startswith("/api/sales/")
                    for m in r.methods if m not in ("HEAD", "OPTIONS")}
        walk = lambda dep: [dep] + [x for d in dep.dependencies for x in walk(d)]  # noqa: E731
        for name, (method, template, _, needed) in ENDPOINTS.items():
            key = (method, template.replace("{lead}", "{lead_id}").replace("{customer}", "{customer_id}").replace("{ob}", "{onboarding_id}"))
            perms = [d.call.__sales_permissions__ for d in walk(by_route[key].dependant) if getattr(d.call, "__sales_guard__", None) == "permission"]
            assert perms == [(needed,)], (name, perms)

    @pytest.mark.parametrize("who", ["data_entry", "no_roles"])
    def test_403_precedes_validation_for_callers_without_the_permission(self, p, who):
        h = H(p, who)
        for method, path, body in (
            ("POST", f"/api/sales/leads/{PHANTOM_ID}/convert", {"garbage": True}),
            ("POST", f"/api/sales/leads/{PHANTOM_ID}/convert/preflight", {"garbage": True}),
            ("GET", "/api/sales/customers?limit=0", None), ("GET", "/api/sales/customers?status=nonsense", None),
            ("GET", "/api/sales/onboarding?limit=0", None), ("GET", "/api/sales/onboarding?stage=nonsense", None),
            ("POST", f"/api/sales/onboarding/{PHANTOM_ID}/stage", {"garbage": True}),
            ("POST", f"/api/sales/onboarding/{PHANTOM_ID}/assign", {"garbage": True}),
            ("PATCH", f"/api/sales/onboarding/{PHANTOM_ID}", {"garbage": True}),
        ):
            assert api(method, path, h, body).status_code == 403, path                            # no schema leak, no existence leak

    def test_a_sales_employee_cannot_convert_even_their_own_won_lead(self, p, world):
        lead = won_lead(H(p, "manager"), p["employee"]["id"])
        before = count_customers(lead["id"])
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", H(p, "employee"), convert_body(lead))
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", H(p, "employee"), {}).status_code == 403
        assert count_customers(lead["id"]) == before == 0

    def test_a_denied_conversion_creates_nothing(self, p):
        lead = won_lead(H(p, "manager"), p["employee"]["id"])
        body = convert_body(lead)
        for who in ("employee", "employee2", "data_entry", "onboarding", "no_roles"):
            assert api("POST", f"/api/sales/leads/{lead['id']}/convert", H(p, who), body).status_code == 403, who
        assert count_customers(lead["id"]) == 0
        assert count_users(body["owner_email"]) == 0


# =============================================================================
# one permission at a time
# =============================================================================
def _person(admin_h, keys, label):
    role = make_custom_role(keys)
    return create_staff(admin_h, [role], f"crbac-{label}"), role


class TestPermissionGranularity:
    def test_customers_view_alone_reads_customers_and_nothing_else(self, p, admin_h, world):
        person, role = _person(admin_h, ["sales.access", "sales.customers.view", "sales.leads.scope_all"], "cv")
        try:
            h = person["headers"]
            assert api("GET", "/api/sales/customers", h).status_code == 200
            assert api("GET", f"/api/sales/customers/{world['customer']['id']}", h).status_code == 200
            assert api("GET", f"/api/sales/leads/{world['lead']['id']}/conversion", h).json()["can_convert"] is False
            assert api("POST", f"/api/sales/leads/{world['lead']['id']}/convert/preflight", h, {}).status_code == 403
            assert api("GET", "/api/sales/onboarding", h).status_code == 403
            assert customer(h, world["customer"]["id"])["onboarding"] is None                    # no onboarding.view: no onboarding summary
        finally:
            retire_roles([role])

    def test_an_onboarding_scope_without_onboarding_view_hides_the_onboarding_summary(self, p, admin_h, world):
        """Seeing a customer does not reveal its onboarding unless the caller may VIEW onboarding: holding only the onboarding
        SCOPE (and even the manage permission) is not enough - the permission that reads, not the scope, gates the summary."""
        person, role = _person(admin_h, ["sales.access", "sales.customers.view", "sales.leads.scope_all", "sales.onboarding.scope_all", "sales.onboarding.manage"], "cv-scope")
        try:
            c = customer(person["headers"], world["customer"]["id"])
            assert c["onboarding"] is None and c["can"]["view_onboarding"] is False
            assert api("GET", f"/api/sales/onboarding/{world['oid']}", person["headers"]).status_code == 403            # nor the record itself
        finally:
            retire_roles([role])

    def test_customers_convert_alone_converts_but_cannot_view_customers(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.customers.convert", "sales.leads.scope_all"], "cc")
        try:
            h = person["headers"]
            lead = won_lead(H(p, "manager"), p["employee"]["id"])
            assert api("GET", f"/api/sales/leads/{lead['id']}/conversion", h).status_code == 403     # that is a VIEW
            assert api("GET", "/api/sales/customers", h).status_code == 403
            pre = api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", h, {})
            assert pre.status_code == 200 and pre.json()["can_convert"] is True
            r = api("POST", f"/api/sales/leads/{lead['id']}/convert", h, convert_body(lead))
            assert r.status_code == 201                                                              # the response itself is built without checking view
            assert api("GET", f"/api/sales/customers/{r.json()['customer']['id']}", h).status_code == 403
        finally:
            retire_roles([role])

    def test_onboarding_view_alone_reads_but_cannot_change_or_assign(self, p, admin_h, world):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.scope_all"], "ov")
        try:
            h = person["headers"]
            assert api("GET", f"/api/sales/onboarding/{world['oid']}", h).status_code == 200
            o = onboarding(h, world["oid"])
            assert o["can"]["update"] is False and o["can"]["change_stage"] is False and o["can"]["assign"] is False and o["allowed_stages"] == []
            assert api("PATCH", f"/api/sales/onboarding/{world['oid']}", h, {"notes": "x"}).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{world['oid']}/stage", h, {"stage": "training"}).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{world['oid']}/assign", h, {"assigned_to": p["onboarding"]["id"]}).status_code == 403
            assert o["company"] is None and o["lead_id"] is None                                    # no customers.view: no company, no lead link
        finally:
            retire_roles([role])

    def test_onboarding_manage_alone_changes_but_cannot_read(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.manage", "sales.onboarding.scope_all"], "om")
        try:
            h = person["headers"]
            made = converted(H(p, "manager"), p["employee"]["id"])
            oid = onboarding_id(H(p, "manager"), made["customer"]["id"])
            assign_onboarding(H(p, "manager"), oid, p["onboarding"]["id"])
            assert api("GET", f"/api/sales/onboarding/{oid}", h).status_code == 403                 # manage does not imply view ...
            assert api("POST", f"/api/sales/onboarding/{oid}/stage", h, {"stage": "training"}).status_code == 200      # ... and view does not imply manage
        finally:
            retire_roles([role])

    def test_onboarding_assign_alone_assigns_and_lists_assignees_but_cannot_read(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.assign", "sales.onboarding.scope_all"], "oa")
        try:
            h = person["headers"]
            made = converted(H(p, "manager"), p["employee"]["id"])
            oid = onboarding_id(H(p, "manager"), made["customer"]["id"])
            assert api("GET", "/api/sales/onboarding/assignees", h).status_code == 200
            assert api("POST", f"/api/sales/onboarding/{oid}/assign", h, {"assigned_to": p["onboarding"]["id"]}).status_code == 200
            assert api("GET", f"/api/sales/onboarding/{oid}", h).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{oid}/stage", h, {"stage": "training"}).status_code == 403
        finally:
            retire_roles([role])

    def test_the_action_permissions_without_any_scope_reach_nothing(self, p, admin_h, world):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.assign", "sales.customers.view", "sales.customers.convert"], "noscope")
        try:
            h = person["headers"]
            assert listing(h, "/api/sales/onboarding")["total"] == 0 and listing(h, "/api/sales/customers")["total"] == 0     # empty, not an error
            assert api("GET", f"/api/sales/onboarding/{world['oid']}", h).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{world['oid']}/stage", h, {"stage": "training"}).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{world['oid']}/assign", h, {"assigned_to": p["onboarding"]["id"]}).status_code == 403
            assert api("GET", f"/api/sales/customers/{world['customer']['id']}", h).status_code == 403
            lead = won_lead(H(p, "manager"), p["employee"]["id"])
            assert api("POST", f"/api/sales/leads/{lead['id']}/convert", h, convert_body(lead)).status_code == 403
            assert count_customers(lead["id"]) == 0
        finally:
            retire_roles([role])

    def test_the_permissions_of_several_roles_add_up(self, p, admin_h, world):
        a, b = make_custom_role(["sales.access", "sales.onboarding.view", "sales.onboarding.scope_all"]), make_custom_role(["sales.onboarding.manage"])
        try:
            person = create_staff(admin_h, [a, b], "crbac-union")
            assert api("POST", f"/api/sales/onboarding/{world['oid']}/stage", person["headers"], {"stage": "contacted"}).status_code in (200, 409)
            assert onboarding(person["headers"], world["oid"])["can"]["change_stage"] is True
        finally:
            retire_roles([a, b])


# =============================================================================
# row scope
# =============================================================================
@pytest.fixture(scope="module")
def records(p):
    """Two converted customers with UNASSIGNED onboarding records."""
    mgr = H(p, "manager")
    out = []
    for _ in range(2):
        made = converted(mgr, p["employee"]["id"])
        out.append({**made, "oid": onboarding_id(mgr, made["customer"]["id"])})
    return out


class TestOnboardingScope:
    def test_scope_assigned_sees_exactly_the_records_assigned_to_them(self, p, admin_h, records):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"], "oa1")
        try:
            h = person["headers"]
            mgr = H(p, "manager")
            assert listing(h, "/api/sales/onboarding")["total"] == 0
            assign_onboarding(mgr, records[0]["oid"], person["id"])
            assert [i["id"] for i in listing(h, "/api/sales/onboarding")["items"]] == [records[0]["oid"]]
            assert api("GET", f"/api/sales/onboarding/{records[0]['oid']}", h).status_code == 200
            assert api("GET", f"/api/sales/onboarding/{records[1]['oid']}", h).status_code == 403
            assign_onboarding(mgr, records[1]["oid"], person["id"])
            assert {i["id"] for i in listing(h, "/api/sales/onboarding")["items"]} == {records[0]["oid"], records[1]["oid"]}
        finally:
            retire_roles([role])

    def test_scope_all_sees_every_record_and_scope_all_plus_assigned_adds_nothing(self, p, admin_h, records):
        for keys, label in ((["sales.onboarding.scope_all"], "all"), (["sales.onboarding.scope_all", "sales.onboarding.scope_assigned"], "both")):
            person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", *keys], f"os-{label}")
            try:
                ids = {i["id"] for i in listing(person["headers"], "/api/sales/onboarding?limit=100")["items"]}
                assert {r["oid"] for r in records} <= ids
            finally:
                retire_roles([role])

    def test_the_lead_scope_does_not_widen_the_onboarding_scope(self, p, admin_h, records):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.manage", "sales.leads.scope_all", "sales.leads.view"], "os-lead")
        try:
            h = person["headers"]
            assert listing(h, "/api/sales/onboarding")["total"] == 0
            assert api("GET", f"/api/sales/onboarding/{records[0]['oid']}", h).status_code == 403
        finally:
            retire_roles([role])

    def test_the_onboarding_scope_does_not_widen_the_lead_scope(self, p, admin_h, records):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.scope_all", "sales.customers.view", "sales.leads.view"], "os-nolead")
        try:
            h = person["headers"]
            assert listing(h, "/api/sales/customers")["total"] == 0                              # customers follow the LEAD scope, and there is none
            assert api("GET", f"/api/sales/customers/{records[0]['customer']['id']}", h).status_code == 403
            o = onboarding(h, records[0]["oid"])
            assert o["company"] is None and o["lead_id"] is None                                # ... so the company and lead link are hidden here too
        finally:
            retire_roles([role])

    def test_the_company_and_lead_link_need_customer_view_and_lead_scope(self, p, admin_h, records):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.scope_all", "sales.customers.view", "sales.leads.view", "sales.leads.scope_all"], "os-both")
        try:
            o = onboarding(person["headers"], records[0]["oid"])
            assert o["company"]["id"] == records[0]["customer"]["company"]["id"] and o["lead_id"] == records[0]["lead"]["id"]
        finally:
            retire_roles([role])


class TestLeadScopeOnConversion:
    def test_scope_assigned_converts_only_the_leads_assigned_to_that_person(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.customers.convert", "sales.customers.view", "sales.leads.scope_assigned"], "ls1")
        try:
            h = person["headers"]
            mgr = H(p, "manager")
            others = won_lead(mgr, p["employee"]["id"])
            mine = won_lead(mgr, p["employee"]["id"])
            assign(mgr, mine["id"], person["id"])                                                   # this role holds scope_assigned: it can be given a lead
            r = api("POST", f"/api/sales/leads/{others['id']}/convert", h, convert_body(others))
            assert r.status_code == 403 and count_customers(others["id"]) == 0
            assert api("POST", f"/api/sales/leads/{others['id']}/convert/preflight", h, {}).status_code == 403
            assert api("GET", f"/api/sales/leads/{others['id']}/conversion", h).status_code == 403
            assert api("POST", f"/api/sales/leads/{mine['id']}/convert", h, convert_body(mine)).status_code == 201     # the one assigned to them: yes
        finally:
            retire_roles([role])

    def test_a_converted_leads_customer_moves_with_the_lead_scope(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.customers.view", "sales.leads.scope_assigned"], "ls2")
        try:
            mgr = H(p, "manager")
            made = converted(mgr, p["employee"]["id"])
            assert api("GET", f"/api/sales/customers/{made['customer']['id']}", person["headers"]).status_code == 403
            assert listing(person["headers"], f"/api/sales/customers?q={made['lead']['business_name']}")["total"] == 0
        finally:
            retire_roles([role])

    def test_scope_intake_reaches_the_leads_they_created_only_if_they_can_see_the_lead(self, p, admin_h):
        """A Lead Data Entry-style scope (created by me / unassigned) plus the convert permission: only a WON lead can be
        converted, and a won lead is assigned - so only ones they created remain in scope."""
        person, role = _person(admin_h, ["sales.access", "sales.leads.create", "sales.customers.convert", "sales.customers.view", "sales.leads.scope_intake"], "ls3")
        try:
            h = person["headers"]
            mgr = H(p, "manager")
            theirs = create_lead(h)                                                                  # created by this person
            assign(mgr, theirs["id"], p["employee"]["id"])
            set_stage(mgr, theirs["id"], "contacted")
            set_stage(mgr, theirs["id"], "won")
            elses = won_lead(mgr, p["employee"]["id"])
            assert api("POST", f"/api/sales/leads/{elses['id']}/convert", h, convert_body(elses)).status_code == 403
            assert api("POST", f"/api/sales/leads/{theirs['id']}/convert", h, convert_body(theirs)).status_code == 201
        finally:
            retire_roles([role])


# =============================================================================
# staff whose access changed
# =============================================================================
class TestStaffWhoseAccessChanged:
    def _try_everything(self, h, world):
        return {
            "customers": api("GET", "/api/sales/customers", h).status_code,
            "customer": api("GET", f"/api/sales/customers/{world['customer']['id']}", h).status_code,
            "conversion": api("GET", f"/api/sales/leads/{world['lead']['id']}/conversion", h).status_code,
            "onboarding": api("GET", "/api/sales/onboarding", h).status_code,
            "counts": api("GET", "/api/sales/onboarding/counts", h).status_code,
        }

    def test_a_deactivated_manager_is_denied_everywhere_at_once_even_with_a_valid_token(self, admin_h, world):
        boss = create_staff(admin_h, ["sales_manager"], "crbac-deactivated")
        assert set(self._try_everything(boss["headers"], world).values()) == {200}
        assert api("PATCH", f"/api/sales/team/{boss['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert set(self._try_everything(boss["headers"], world).values()) == {403}
        assert api("PATCH", f"/api/sales/team/{boss['id']}", admin_h, {"status": "active"}).status_code == 200
        assert set(self._try_everything(boss["headers"], world).values()) == {200}

    def test_a_deactivated_onboarding_employee_is_denied_on_their_own_records(self, p, admin_h):
        worker = create_staff(admin_h, ["onboarding_employee"], "crbac-obdeact")
        made = converted(H(p, "manager"), p["employee"]["id"])
        oid = onboarding_id(H(p, "manager"), made["customer"]["id"])
        assign_onboarding(H(p, "manager"), oid, worker["id"])
        assert api("GET", f"/api/sales/onboarding/{oid}", worker["headers"]).status_code == 200
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"}).status_code == 200
        for method, path, body in (("GET", "/api/sales/onboarding", None), ("GET", f"/api/sales/onboarding/{oid}", None),
                                   ("POST", f"/api/sales/onboarding/{oid}/stage", {"stage": "contacted"}), ("PATCH", f"/api/sales/onboarding/{oid}", {"notes": "x"})):
            assert api(method, path, worker["headers"], body).status_code == 403, path

    def test_a_revoked_role_loses_access_and_regains_it_when_regranted(self, p, admin_h, world):
        boss = create_staff(admin_h, ["sales_manager"], "crbac-revoked")
        assert self._try_everything(boss["headers"], world)["customer"] == 200
        assert api("DELETE", f"/api/sales/team/{boss['id']}/roles/sales_manager", admin_h).status_code == 200
        assert set(self._try_everything(boss["headers"], world).values()) == {403}
        assert api("POST", f"/api/sales/team/{boss['id']}/roles", admin_h, {"role_key": "sales_manager"}).status_code == 200
        assert self._try_everything(boss["headers"], world)["customer"] == 200

    def test_a_retired_custom_role_stops_granting(self, admin_h, world):
        person, role = _person(admin_h, ["sales.access", "sales.onboarding.view", "sales.onboarding.scope_all"], "crbac-retired")
        assert api("GET", f"/api/sales/onboarding/{world['oid']}", person["headers"]).status_code == 200
        retire_roles([role])
        assert api("GET", f"/api/sales/onboarding/{world['oid']}", person["headers"]).status_code == 403

    def test_permissions_are_read_live_on_every_request(self, p, admin_h):
        person, role = _person(admin_h, ["sales.access", "sales.customers.convert", "sales.leads.scope_all"], "crbac-live")
        lead = won_lead(H(p, "manager"), p["employee"]["id"])
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", person["headers"], {}).status_code == 200
        retire_roles([role])
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", person["headers"], {}).status_code == 403


# =============================================================================
# callers who are not internal staff
# =============================================================================
class TestNotStaff:
    def _paths(self):
        ids = {"lead": PHANTOM_ID, "customer": PHANTOM_ID, "ob": PHANTOM_ID}
        return [(name, method, "/api/sales" + template.format(**ids)) for name, (method, template, _, _) in ENDPOINTS.items()]

    def test_company_owners_and_employees_are_denied_on_every_phase_4_route(self, owner_token, employee_token):
        for token in (owner_token, employee_token):
            h = auth(token)
            for name, method, path in self._paths():
                r = api(method, path, h, {"garbage": True} if method != "GET" else None)
                assert r.status_code == 403 and r.json()["detail"] == "Access denied", (name, r.status_code)

    def test_a_company_created_by_conversion_gives_its_owner_no_sales_access(self, p):
        from sales_test_utils import login
        made = converted(H(p, "manager"), p["employee"]["id"])
        session = login(made["body"]["owner_email"], made["body"]["owner_password"])
        h = auth(session["token"])
        for name, method, path in self._paths():
            assert api(method, path, h, {"garbage": True} if method != "GET" else None).status_code == 403, name
        assert api("GET", "/api/sales/me", h).status_code == 403

    def test_anonymous_callers_get_nothing(self):
        for name, method, path in self._paths():
            r = api(method, path, None, {} if method != "GET" else None)
            assert r.status_code in (401, 403), (name, r.status_code)
        assert api("GET", "/api/sales/customers", {"Authorization": "Bearer not-a-token"}).status_code in (401, 403)
