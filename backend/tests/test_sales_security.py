"""JAZ Sales - Phase 6: security review (HTTP integration tier).

What the final review found and fixed, and what it verified across the WHOLE route table instead of phase by phase:

  * an aggregate hidden inside a non-lead payload: a campaign's `lead_count` counted every lead of the campaign for anyone who
    may view campaigns - it is now the number of leads the CALLER may see;
  * validation errors used to echo the request body: a request that forgot one field came back carrying the rest of its body -
    the Owner password of a conversion, the password of a staff account. Sales routes now answer with type / location / message
    only;
  * mass assignment: every request model of every route refuses fields it does not declare (checked over the route table), and
    smuggling `created_by`, `role`, `password_hash`, `company_id`, ... into real requests changes nothing;
  * IDOR: every endpoint that takes an id, called with an object that is OUTSIDE the caller's scope, answers 403 - with the
    same answer for callers who lack the permission - never 200, never a 4xx that confirms details, never a 500; and nothing
    was changed by the attempts. Unknown ids are 404, malformed ones 404 / 422;
  * core users (Company Owner / Employee) and anonymous callers are refused on every one of the Sales routes.
"""
import typing
import uuid

import pytest
from pydantic import BaseModel

from sales_customer_test_utils import active_plan_id, assign_onboarding, converted, onboarding_id, onboarding_worker
from sales_reports_test_utils import (
    PHANTOM_ID,
    act,
    api,
    assign,
    create_call,
    create_campaign,
    create_demo,
    create_followup,
    create_lead,
    create_staff,
    create_trial,
    errors_of,
    later,
    make_custom_role,
    make_personas,
    retire_roles,
    set_stage,
    uniq,
)
from sales_test_utils import admin_token, assert_scratch_target, auth, employee_token, employee_user, owner_token, owner_user  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def personas(admin_h):
    return make_personas(admin_h, "sec")


# =============================================================================
# a campaign's lead count is an aggregate of leads: it obeys the caller's lead scope
# =============================================================================
class TestCampaignLeadCountIsScoped:
    @pytest.fixture(scope="class")
    def campaign(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        c = create_campaign(mgr)
        for owner in ("employee", "employee", "employee2"):
            lead = create_lead(mgr, campaign_id=c["id"])
            assign(mgr, lead["id"], personas[owner]["id"])
        create_lead(mgr, campaign_id=c["id"])                                    # unassigned, made by the manager
        create_lead(personas["data_entry"]["headers"], campaign_id=c["id"])       # unassigned, made by the data-entry user
        return c

    def counts(self, headers, campaign):
        """(the detail's lead_count, the list's lead_count for the same campaign - found by searching its unique name)."""
        detail = api("GET", f"/api/sales/campaigns/{campaign['id']}", headers)
        listed = api("GET", f"/api/sales/campaigns?q={campaign['name'].replace(' ', '%20')}&limit=200", headers)
        assert detail.status_code == 200 and listed.status_code == 200
        in_list = next(i for i in listed.json()["items"] if i["id"] == campaign["id"])
        return detail.json()["lead_count"], in_list["lead_count"]

    def test_a_manager_and_the_super_admin_see_every_lead(self, campaign, admin_h, personas):
        assert self.counts(personas["manager"]["headers"], campaign) == (5, 5)
        assert self.counts(admin_h, campaign) == (5, 5)

    def test_an_employee_counts_only_the_leads_assigned_to_them(self, campaign, personas):
        assert self.counts(personas["employee"]["headers"], campaign) == (2, 2)          # the detail and the list agree
        assert self.counts(personas["employee2"]["headers"], campaign) == (1, 1)

    def test_lead_data_entry_counts_the_leads_they_made_and_the_unassigned_ones(self, campaign, personas):
        assert self.counts(personas["data_entry"]["headers"], campaign) == (2, 2)        # their own + the manager's unassigned one
        assert self.counts(personas["data_entry2"]["headers"], campaign) == (2, 2)       # the two unassigned ones (nothing of their own)

    def test_a_campaign_with_leads_the_caller_cannot_see_shows_zero_not_the_teams_number(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        c = create_campaign(mgr)
        for _ in range(3):
            lead = create_lead(mgr, campaign_id=c["id"])
            assign(mgr, lead["id"], personas["employee2"]["id"])
        assert self.counts(personas["employee"]["headers"], c) == (0, 0)                     # E1 owns none of them
        assert self.counts(mgr, c) == (3, 3)

    def test_the_delete_guard_still_protects_every_lead_but_only_says_how_many_to_someone_who_can_see_them_all(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        c = create_campaign(mgr)
        for owner in ("employee", "employee2"):
            assign(mgr, create_lead(mgr, campaign_id=c["id"])["id"], personas[owner]["id"])
        # the manager sees both leads: the message says how many
        r = api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr)
        assert r.status_code == 409 and errors_of(r)["code"] == "campaign_has_leads" and errors_of(r)["lead_count"] == 2
        # a caller who may manage campaigns but sees only their own leads: still refused - and told nothing about the total
        role = make_custom_role({"sales.access", "sales.campaigns.view", "sales.campaigns.manage", "sales.leads.view", "sales.leads.scope_assigned"})
        try:
            limited = create_staff(admin_h, [role], "sec-limited")
            r = api("DELETE", f"/api/sales/campaigns/{c['id']}", limited["headers"])
            assert r.status_code == 409
            detail = errors_of(r)
            assert detail["code"] == "campaign_has_leads" and "lead_count" not in detail and not any(ch.isdigit() for ch in detail["message"])
        finally:
            retire_roles([role])
        assert api("GET", f"/api/sales/campaigns/{c['id']}", mgr).status_code == 200            # nothing was deleted


# =============================================================================
# validation errors never echo what was sent
# =============================================================================
SECRET = "Sup3r#Secret-Pass-9"


class TestValidationErrorsDoNotEchoTheRequest:
    def test_a_missing_field_does_not_bring_the_password_back(self, admin_h):
        # a Customer Setup that forgets the plan, an account that forgets its name: the requests that carry a password
        setup = api("POST", f"/api/sales/leads/{PHANTOM_ID}/setup", admin_h, {
            "business_name": "X", "owner_name": "Y", "owner_email": "a@b.co", "owner_phone": "+9647701234567", "owner_password": SECRET})
        create = api("POST", "/api/sales/team", admin_h, {"email": "z@z.co", "phone": "+15551234567", "password": SECRET, "role_keys": []})
        reset = api("POST", f"/api/sales/team/{PHANTOM_ID}/reset-password", admin_h, {"new_password": SECRET, "surprise": 1})
        for name, r in (("setup", setup), ("create", create), ("reset", reset)):
            assert r.status_code == 422, name
            assert SECRET not in r.text, f"{name}: the password came back in the error"
        # the RETIRED conversion (which used to be one of them) reads no body at all: a 410 that says nothing about what was sent
        retired = api("POST", f"/api/sales/leads/{PHANTOM_ID}/convert", admin_h, {
            "business_name": "X", "owner_name": "Y", "owner_email": "a@b.co", "owner_phone": "+9647701234567", "owner_password": SECRET})
        assert retired.status_code == 410 and SECRET not in retired.text

    def test_every_error_carries_only_type_location_and_message(self, admin_h):
        r = api("POST", "/api/sales/team", admin_h, {"email": "not-an-email", "password": SECRET, "role_keys": "x", "unknown": SECRET})
        assert r.status_code == 422
        for error in r.json()["detail"]:
            assert set(error) <= {"type", "loc", "msg"}, error                  # no `input`, no `ctx`, no `url`
        assert SECRET not in r.text

    def test_no_body_route_of_the_whole_api_echoes_a_marker(self, admin_h):
        """Every route that takes a body, sent a body of marker strings (which is wrong in a dozen ways at once: missing fields,
        wrong types, bad formats): the marker must not come back in ANY of the 422 responses."""
        from sales.router import sales_router

        marker = f"MARKER-{uuid.uuid4().hex}"
        probed = 0
        for route in sales_router.routes:
            if not route.dependant.body_params or route.methods == {"GET"}:
                continue
            path = route.path.replace("/api/sales", "")
            for name in route.param_convertors if hasattr(route, "param_convertors") else ():
                path = path.replace("{" + name + "}", PHANTOM_ID)
            model_fields = []
            for f in route.dependant.body_params:
                t = f.type_
                model_fields += [a for a in (typing.get_args(t) or (t,)) if isinstance(a, type) and issubclass(a, BaseModel)]
            body = {name: marker for m in model_fields for name in m.model_fields}
            body["not_a_field"] = marker
            r = api(sorted(route.methods)[0], f"/api/sales{path}", admin_h, body)
            probed += 1
            assert r.status_code != 500, (route.path, r.text[:200])
            assert marker not in r.text, f"{route.path} echoed the request in its response"
        assert probed >= 25, probed


# =============================================================================
# mass assignment
# =============================================================================
def _request_models():
    from sales.router import sales_router

    found = {}
    for route in sales_router.routes:
        for f in route.dependant.body_params:
            t = f.type_
            for a in (typing.get_args(t) or (t,)):
                if isinstance(a, type) and issubclass(a, BaseModel):
                    found[a.__name__] = a
    return found


class TestMassAssignment:
    def test_every_request_model_refuses_fields_it_does_not_declare(self):
        models = _request_models()
        assert len(models) >= 25, sorted(models)
        lax = sorted(name for name, m in models.items() if m.model_config.get("extra") != "forbid")
        assert lax == [], f"request models that would silently accept extra fields: {lax}"

    SMUGGLED = {
        "id": PHANTOM_ID, "role": "super_admin", "company_id": PHANTOM_ID, "created_by": PHANTOM_ID, "password_hash": "x", "status": "active",
        "is_super_admin": True, "archived_at": "2020-01-01T00:00:00Z", "closed_at": "2020-01-01T00:00:00Z", "lead_id": PHANTOM_ID,
        "pipeline_stage": "won", "converted_by": PHANTOM_ID, "completed_at": "2020-01-01T00:00:00Z",
    }

    def test_smuggling_server_controlled_fields_into_real_requests_is_refused_and_changes_nothing(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        lead = create_lead(mgr)
        for extra in ({"created_by": PHANTOM_ID}, {"pipeline_stage": "won"}, {"archived_at": "2020-01-01T00:00:00Z"}, {"id": PHANTOM_ID}):
            assert api("POST", "/api/sales/leads", mgr, {"business_name": f"Smuggler {uniq()}", **extra}).status_code == 422, extra
            assert api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"notes": "n", **extra}).status_code == 422, extra
        for extra in ({"role": "super_admin"}, {"status": "active"}, {"password_hash": "x"}, {"is_super_admin": True}, {"company_id": PHANTOM_ID}):
            assert api("POST", "/api/sales/team", admin_h, {"name": "n", "email": f"{uniq()}@example.com", "phone": "+15551230000",
                                                                       "password": "Staff#12345", "role_keys": [], **extra}).status_code == 422, extra
        assert api("PATCH", f"/api/sales/campaigns/{create_campaign(mgr)['id']}", mgr, {"name": f"n{uniq()}", "created_by": PHANTOM_ID}).status_code == 422
        assert api("POST", "/api/sales/followups", mgr, {"lead_id": lead["id"], "due_at": later(days=1), "created_by": PHANTOM_ID}).status_code == 422
        assert api("POST", "/api/sales/calls", mgr, {"lead_id": lead["id"], "result": "answered", "employee_id": PHANTOM_ID}).status_code == 422
        # the lead is exactly as it was created: unassigned, `new`, not archived
        after = api("GET", f"/api/sales/leads/{lead['id']}", mgr).json()
        assert after["pipeline_stage"] == "new" and after["assigned_to"] is None and after["archived_at"] is None


# =============================================================================
# IDOR: every id-taking endpoint, an object outside the caller's scope
# =============================================================================
@pytest.fixture(scope="module")
def family(admin_h, personas):
    """A lead of Employee 2 with one of each kind of work, and a converted customer whose onboarding belongs to a worker: none
    of it inside the scope of Employee 1, Lead Data Entry, the Onboarding Employee or a staff account with no role."""
    mgr, e2 = personas["manager"]["headers"], personas["employee2"]
    lead = create_lead(mgr)
    assign(mgr, lead["id"], e2["id"])
    set_stage(mgr, lead["id"], "contacted")
    call = create_call(e2["headers"], lead["id"])
    followup = create_followup(e2["headers"], lead["id"])
    demo = create_demo(e2["headers"], lead["id"])
    trial = create_trial(e2["headers"], lead["id"])
    made = converted(mgr, e2["id"])
    worker = onboarding_worker(admin_h, "sec-owner")
    oid = onboarding_id(mgr, made["customer"]["id"])
    assign_onboarding(mgr, oid, worker["id"])
    spare = create_lead(mgr)                                                      # a second out-of-scope lead: the target of assignments etc.
    assign(mgr, spare["id"], e2["id"])
    return {
        "lead": lead["id"], "call": call["id"], "followup": followup["id"], "demo": demo["id"], "trial": trial["id"],
        "customer": made["customer"]["id"], "onboarding": oid, "won_lead": made["lead"]["id"], "spare": spare["id"], "worker": worker["id"],
    }


def _calls(f, personas):
    """(name, method, path, body) - every id-taking endpoint of Sales, with a syntactically valid request."""
    e1 = personas["employee"]["id"]
    return [
        ("lead get", "GET", f"/leads/{f['lead']}", None),
        ("lead timeline", "GET", f"/leads/{f['lead']}/activities", None),
        ("lead conversion status", "GET", f"/leads/{f['won_lead']}/conversion", None),
        ("lead update", "PATCH", f"/leads/{f['lead']}", {"notes": "hijacked"}),
        ("lead archive", "DELETE", f"/leads/{f['lead']}", None),
        ("lead restore", "POST", f"/leads/{f['lead']}/restore", None),
        ("lead assign", "POST", f"/leads/{f['lead']}/assign", {"assigned_to": e1}),
        ("lead unassign", "POST", f"/leads/{f['lead']}/unassign", None),
        ("lead stage", "POST", f"/leads/{f['lead']}/stage", {"stage": "lost", "lost_reason": "other"}),
        ("lead wait list", "POST", f"/leads/{f['lead']}/wait-list", None),                 # the OWNER's tool (f2b6d8a1c4e9)
        ("lead convert", "POST", f"/leads/{f['won_lead']}/convert", {
            "business_name": "Hijack Co", "owner_name": "H", "owner_email": f"h-{uniq()}@example.com", "owner_phone": "+15559990000",
            "owner_password": "Owner#Pass-2026", "subscription_plan_id": active_plan_id()}),
        ("lead convert preflight", "POST", f"/leads/{f['won_lead']}/convert/preflight", {}),
        # simplified workflow: the Customer Setup (an OPEN lead of somebody else's - it would mark it won and create a company)
        ("lead setup", "POST", f"/leads/{f['lead']}/setup", {
            "business_name": "Hijack Setup Co", "owner_name": "H", "owner_email": f"hs-{uniq()}@example.com", "owner_phone": "+15559990001",
            "owner_password": "Owner#Pass-2026", "subscription_plan_id": active_plan_id(), "subscription_type": "trial",
            "employees": [{"name": "E", "email": f"he-{uniq()}@example.com", "phone": "+15559990002", "password": "Emp#Pass-2026"}],
            "tasks": [{"title": "T", "assignee": 0, "due_date": "2030-01-01"}]}),
        ("lead setup preflight", "POST", f"/leads/{f['lead']}/setup/preflight", {}),
        ("bulk assign", "POST", "/leads/bulk-assign", {"lead_ids": [f["lead"], f["spare"]], "assigned_to": e1}),
        ("call get", "GET", f"/calls/{f['call']}", None),
        ("call update", "PATCH", f"/calls/{f['call']}", {"notes": "hijacked"}),
        ("call create on the lead", "POST", "/calls", {"lead_id": f["lead"], "result": "answered"}),
        ("followup get", "GET", f"/followups/{f['followup']}", None),
        ("followup update", "PATCH", f"/followups/{f['followup']}", {"notes": "hijacked"}),
        ("followup complete", "POST", f"/followups/{f['followup']}/complete", None),
        ("followup cancel", "POST", f"/followups/{f['followup']}/cancel", None),
        ("followup create on the lead", "POST", "/followups", {"lead_id": f["lead"], "due_at": later(days=1)}),
        ("demo get", "GET", f"/demos/{f['demo']}", None),
        ("demo update", "PATCH", f"/demos/{f['demo']}", {"notes": "hijacked"}),
        ("demo reschedule", "POST", f"/demos/{f['demo']}/reschedule", {"scheduled_at": later(days=5)}),
        ("demo complete", "POST", f"/demos/{f['demo']}/complete", None),
        ("demo cancel", "POST", f"/demos/{f['demo']}/cancel", None),
        ("demo no-show", "POST", f"/demos/{f['demo']}/no-show", None),
        ("demo create on the lead", "POST", "/demos", {"lead_id": f["lead"], "scheduled_at": later(days=2)}),
        ("trial get", "GET", f"/trials/{f['trial']}", None),
        ("trial update", "PATCH", f"/trials/{f['trial']}", {"notes": "hijacked"}),
        ("trial complete", "POST", f"/trials/{f['trial']}/complete", None),
        ("trial cancel", "POST", f"/trials/{f['trial']}/cancel", None),
        ("trial create on the lead", "POST", "/trials", {"lead_id": f["lead"], "expected_end_at": later(days=9)}),
        ("customer get", "GET", f"/customers/{f['customer']}", None),
        ("onboarding get", "GET", f"/onboarding/{f['onboarding']}", None),
        ("onboarding timeline", "GET", f"/onboarding/{f['onboarding']}/timeline", None),
        ("onboarding update", "PATCH", f"/onboarding/{f['onboarding']}", {"notes": "hijacked"}),
        ("onboarding assign", "POST", f"/onboarding/{f['onboarding']}/assign", {"assigned_to": f["worker"]}),
        ("onboarding stage", "POST", f"/onboarding/{f['onboarding']}/stage", {"stage": "contacted"}),
    ]


def _snapshot(headers, f):
    """The state of every object of the family, as the manager sees it."""
    out = {}
    for name, path in (("lead", f"/leads/{f['lead']}"), ("call", f"/calls/{f['call']}"), ("followup", f"/followups/{f['followup']}"),
                       ("demo", f"/demos/{f['demo']}"), ("trial", f"/trials/{f['trial']}"), ("customer", f"/customers/{f['customer']}"),
                       ("onboarding", f"/onboarding/{f['onboarding']}"), ("spare", f"/leads/{f['spare']}"), ("won", f"/leads/{f['won_lead']}")):
        r = api("GET", f"/api/sales{path}", headers)
        assert r.status_code == 200, (name, r.status_code)
        out[name] = r.json()
    return out


class TestObjectsOutsideTheCallersScope:
    OUTSIDERS = ("employee", "data_entry", "onboarding", "no_roles")

    def test_the_sweep_covers_every_id_taking_route(self, family, personas):
        """Every per-record route (a path parameter, or a lead in the body) is in the sweep - a new endpoint that is not added
        here fails this test. (Staff, campaigns, reports, batches and performance are not scoped per record: they have their own tests.)"""
        import re
        from sales.router import sales_router

        norm = lambda path: re.sub(r"\{[a-z_]+\}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "{id}", path.replace("/api/sales", ""))
        swept = {(m, norm(p)) for _, m, p, _ in _calls(family, personas)}
        body_lead_routes = {("POST", "/calls"), ("POST", "/followups"), ("POST", "/demos"), ("POST", "/trials"), ("POST", "/leads/bulk-assign")}
        wanted = set()
        for route in sales_router.routes:
            path = route.path.replace("/api/sales", "")
            if path.startswith(("/team", "/campaigns", "/reports", "/batches", "/performance")):
                continue          # (the batch / performance routes are role-gated, not lead-scoped: test_sales_batches has their sweep)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                if "{" in path or (method, path) in body_lead_routes:
                    wanted.add((method, norm(path)))
        missing = sorted(wanted - swept)
        assert missing == [], f"per-record routes not covered by the IDOR sweep: {missing}"

    @pytest.mark.parametrize("caller", OUTSIDERS)
    def test_an_object_outside_my_scope_is_403_on_every_endpoint_and_nothing_changes(self, family, personas, caller):
        mgr = personas["manager"]["headers"]
        before = _snapshot(mgr, family)
        headers = personas[caller]["headers"]
        bad = []
        for name, method, path, body in _calls(family, personas):
            r = api(method, f"/api/sales{path}", headers, body)
            if r.status_code != 403 or r.json() != {"detail": "Access denied"}:
                bad.append((name, r.status_code, r.text[:120]))
        assert bad == [], f"{caller}: {bad}"
        assert _snapshot(mgr, family) == before                                   # not one attempt changed anything

    def test_the_owner_of_the_objects_can_still_read_them(self, family, personas):
        """The negative results above are about scope, not about the fixtures being broken."""
        e2 = personas["employee2"]["headers"]
        for path in (f"/leads/{family['lead']}", f"/calls/{family['call']}", f"/followups/{family['followup']}", f"/demos/{family['demo']}",
                     f"/trials/{family['trial']}", f"/customers/{family['customer']}"):
            assert api("GET", f"/api/sales{path}", e2).status_code == 200, path

    @pytest.mark.parametrize("caller", ("employee", "manager"))
    def test_unknown_ids_are_404_and_malformed_ones_are_never_a_500(self, family, personas, caller):
        headers = personas[caller]["headers"]
        phantom = {**family, **{k: PHANTOM_ID for k in ("lead", "call", "followup", "demo", "trial", "customer", "onboarding", "won_lead", "spare")}}
        for name, method, path, body in _calls(phantom, personas):
            if "create" in name or name == "bulk assign":
                continue                                                          # (a phantom lead in a BODY is checked below)
            r = api(method, f"/api/sales{path}", headers, body)
            # 403: this caller lacks the permission; 404: it exists nowhere; 410: the RETIRED conversion endpoints (a caller who holds
            # their permission gets the same answer for every id - they look no lead up)
            allowed = (403, 404, 410) if name in ("lead convert", "lead convert preflight") else (403, 404)
            assert r.status_code in allowed, (caller, name, r.status_code)
        for bad in ("not-a-uuid", "1", "%00", "' OR 1=1 --", "0" * 200):
            for path in (f"/leads/{bad}", f"/calls/{bad}", f"/followups/{bad}", f"/demos/{bad}", f"/trials/{bad}", f"/customers/{bad}", f"/onboarding/{bad}"):
                r = api("GET", f"/api/sales{path}", headers)
                assert r.status_code in (403, 404, 422), (caller, path, r.status_code)

    def test_a_phantom_lead_in_a_request_body_is_a_404_or_403_never_a_success(self, family, personas):
        for caller in ("employee", "manager"):
            for path, body in (("/calls", {"lead_id": PHANTOM_ID, "result": "answered"}), ("/followups", {"lead_id": PHANTOM_ID, "due_at": later(days=1)}),
                               ("/demos", {"lead_id": PHANTOM_ID, "scheduled_at": later(days=2)}), ("/trials", {"lead_id": PHANTOM_ID, "expected_end_at": later(days=9)})):
                r = api("POST", f"/api/sales{path}", personas[caller]["headers"], body)
                assert r.status_code in (403, 404), (caller, path, r.status_code, r.text[:120])


# =============================================================================
# core users and anonymous callers on every Sales route
# =============================================================================
class TestNoWayInFromOutsideTheStaff:
    def _routes(self):
        from sales.router import sales_router

        out = []
        for route in sales_router.routes:
            path = route.path.replace("/api/sales", "")
            for name in ("lead_id", "call_id", "followup_id", "demo_id", "trial_id", "customer_id", "onboarding_id", "campaign_id", "user_id", "role_key"):
                path = path.replace("{" + name + "}", PHANTOM_ID)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                out.append((method, path))
        return out

    def test_company_owners_and_employees_are_refused_on_every_route(self, owner_token, employee_token):
        routes = self._routes()
        assert len(routes) >= 79
        for headers in (auth(owner_token), auth(employee_token)):
            for method, path in routes:
                r = api(method, f"/api/sales{path}", headers, {} if method in ("POST", "PATCH", "PUT") else None)
                assert r.status_code == 403 and r.json() == {"detail": "Access denied"}, (method, path, r.status_code)

    def test_anonymous_and_garbage_tokens_are_refused_on_every_route(self):
        for headers in (None, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic Zm9vOmJhcg=="}):
            for method, path in self._routes():
                r = api(method, f"/api/sales{path}", headers, {} if method in ("POST", "PATCH", "PUT") else None)
                assert r.status_code in (401, 403), (method, path, r.status_code)

    def test_a_staff_account_with_no_role_reaches_nothing_but_its_own_context(self, personas):
        headers = personas["no_roles"]["headers"]
        for method, path in self._routes():
            r = api(method, f"/api/sales{path}", headers, {} if method in ("POST", "PATCH", "PUT") else None)
            assert r.status_code == (200 if path == "/me" else 403), (method, path, r.status_code)
