"""JAZ Sales - batches, wait list, global search and employee performance (migration f2b6d8a1c4e9), HTTP integration tier.

  * ROLE ISOLATION: every batch / search / performance route is the Sales Manager's alone - Lead Data Entry, Sales Employees, the
    retired Onboarding role and staff with no roles get 403 everywhere; a custom role handed a batch permission but not the
    all-leads scope still gets 403; ids that are unknown or malformed are 404; and no lead / queue / timeline response a Data Entry
    or Sales Employee can read carries a batch, a batch number or a statistic;
  * DATA BATCHES: every lead Data Entry creates (by any of them) takes a slot of the one open batch, is available to Sales at once,
    and permanently records who entered it and when;
  * THE WAIT LIST: the owner's own tool - a timestamp and a pending status - and how it ends;
  * SALES WORK BATCHES: 100 worked leads form one, open while any lead waits, closed when all have a result, with the
    accepted / rejected / wait-list split; a manager reassigns a completed one to another salesperson over the SAME leads with
    every earlier attempt kept;
  * GLOBAL SEARCH and the trace of a lead through Data Batch -> Master Batch -> Work Batches -> results;
  * EMPLOYEE PERFORMANCE and its persistent history.

The data batch is ONE global row set and the scratch database accumulates leads across runs, so nothing here asserts an absolute
batch number or count: every check is relative (a person's own leads, before / after differences) or about the leads this module
made. The exact-100 / exact-10 boundaries are proven at the database tier (test_sales_batches_db.py).
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target()

import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from sales.models import StaffAuditEvent
from sales_customer_test_utils import (
    OWNER_PASSWORD,
    PHANTOM_ID,
    agree,
    api,
    assign,
    audit_events,
    create_lead,
    create_staff,
    get_lead,
    make_custom_role,
    make_personas,
    make_plan,
    retire_roles,
    set_stage,
    uniq,
    unique_lead_email,
    unique_lead_phone,
    worked_lead,
)
from sales_test_utils import admin_token, auth, mint_token  # noqa: F401  (the token fixture mints instead of logging in)

MANAGER_ONLY_GETS = [
    "/api/sales/batches/overview",
    "/api/sales/batches/data",
    "/api/sales/batches/data/{id}",
    "/api/sales/batches/master",
    "/api/sales/batches/master/{id}",
    "/api/sales/batches/work",
    "/api/sales/batches/work/{id}",
    "/api/sales/batches/search?q=acme",
    "/api/sales/batches/trace/{id}",
    "/api/sales/performance/data-entry",
    "/api/sales/performance/sales",
    "/api/sales/performance/employees/{id}",
    "/api/sales/performance/employees/{id}/history",
]
ID_ROUTES = [p for p in MANAGER_ONLY_GETS if "{id}" in p]


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "bt")


def H(p, who):
    return p[who]["headers"]


@pytest.fixture(scope="module")
def mgr(p):
    return H(p, "manager")


def keys_of(value, found=None):
    """Every dict key anywhere in a JSON value."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            keys_of(v, found)
    elif isinstance(value, list):
        for v in value:
            keys_of(v, found)
    return found


def named_staff(admin_h, roles, label):
    """A staff account whose name is unique to this run, so the performance lists can be narrowed to exactly it (`q`)."""
    label = f"{label}-{uniq(6)}"
    person = create_staff(admin_h, roles, label)
    person["name"] = f"Test {label}"
    return person


def perf_row(mgr, kind, person):
    """The person's row of GET /performance/<kind> (the list, narrowed by name - it is paginated)."""
    r = api("GET", f"/api/sales/performance/{kind}", mgr, params={"q": person["name"], "limit": 100})
    assert r.status_code == 200, r.text[:200]
    rows = [i for i in r.json()["items"] if i["user"]["id"] == person["id"]]
    assert len(rows) == 1, (person["name"], r.json()["total"])
    return rows[0]


def trace(mgr, lead_id):
    r = api("GET", f"/api/sales/batches/trace/{lead_id}", mgr)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def paths_of(mgr, lead_id):
    return [a["path"] for a in trace(mgr, lead_id)["attempts"]]


def win(owner_headers, lead_id):
    """The lead's owner says "Agreed": the REAL Customer Setup (a company, its owner and a dated Trial are created and the lead is won in
    one transaction). It is the only way a lead is accepted - no Sales role can move a lead to `won` by hand - and the accepted decision
    belongs to the owner who pressed it, exactly as the statistics below count it."""
    agree(owner_headers, {"id": lead_id, "business_name": f"Batch Co {uniq()}"})


# =============================================================================
# role isolation
# =============================================================================
class TestOnlyTheSalesManagerReachesBatchesAndPerformance:
    def _fill(self, path, p):
        return path.replace("{id}", p["employee"]["id"] if "/performance/" in path else PHANTOM_ID)

    @pytest.mark.parametrize("who", ["data_entry", "data_entry2", "employee", "employee2", "onboarding", "no_roles"])
    @pytest.mark.parametrize("path", MANAGER_ONLY_GETS)
    def test_everybody_else_is_403(self, p, who, path):
        r = api("GET", self._fill(path, p), H(p, who))
        assert r.status_code == 403 and r.json()["detail"] == "Access denied", f"{who} {path}: {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("who", ["data_entry", "employee", "onboarding", "no_roles"])
    def test_reassigning_is_403_for_everybody_else_before_anything_is_read(self, p, who):
        r = api("POST", f"/api/sales/batches/work/{PHANTOM_ID}/reassign", H(p, who), {"employee_id": p["employee2"]["id"]})
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"

    def test_no_token_is_401(self):
        for path in MANAGER_ONLY_GETS:
            assert api("GET", path.replace("{id}", PHANTOM_ID)).status_code in (401, 403)

    @pytest.mark.parametrize("path", MANAGER_ONLY_GETS)
    def test_the_manager_and_the_super_admin_are_allowed(self, p, mgr, admin_h, path):
        for headers in (mgr, admin_h):
            r = api("GET", self._fill(path, p) if "/performance/" in path else path.replace("{id}", PHANTOM_ID), headers)
            assert r.status_code in (200, 404), f"{path}: {r.status_code} {r.text[:200]}"      # 404 = a well-formed unknown id
            assert r.status_code != 403

    def test_the_manager_alone_holds_the_three_permissions(self, p, mgr):
        me = api("GET", "/api/sales/me", mgr).json()
        assert {"sales.batches.view", "sales.batches.reassign", "sales.performance.view"} <= set(me["permissions"])
        assert {"batches", "performance"} <= set(me["modules"])
        for who in ("data_entry", "employee", "onboarding", "no_roles"):
            me = api("GET", "/api/sales/me", H(p, who)).json()
            assert not {"sales.batches.view", "sales.batches.reassign", "sales.performance.view"} & set(me["permissions"]), who
            assert not {"batches", "performance"} & set(me["modules"]), who

    def test_a_custom_role_with_a_batch_permission_but_not_the_all_leads_scope_is_still_refused(self, admin_h):
        """The permissions are the manager's, but each route ALSO needs the all-leads scope: a role built from only the batch keys
        must not read every lead through the trace or the search."""
        role = make_custom_role(["sales.access", "sales.batches.view", "sales.batches.reassign", "sales.performance.view", "sales.leads.view"])
        try:
            person = create_staff(admin_h, [role], "bt-batchonly")
            for path in MANAGER_ONLY_GETS:
                r = api("GET", path.replace("{id}", person["id"]), person["headers"])
                assert r.status_code == 403, path
            r = api("POST", f"/api/sales/batches/work/{PHANTOM_ID}/reassign", person["headers"], {"employee_id": person["id"]})
            assert r.status_code == 403
        finally:
            retire_roles([role])

    def test_the_view_permission_alone_does_not_reassign_and_the_scope_alone_does_not_view(self, admin_h):
        view_role = make_custom_role(["sales.access", "sales.batches.view", "sales.leads.view", "sales.leads.scope_all"])
        scope_role = make_custom_role(["sales.access", "sales.leads.view", "sales.leads.scope_all"])
        try:
            viewer = create_staff(admin_h, [view_role], "bt-viewer")
            assert api("GET", "/api/sales/batches/overview", viewer["headers"]).status_code == 200
            assert api("GET", "/api/sales/performance/sales", viewer["headers"]).status_code == 403           # a different permission
            r = api("POST", f"/api/sales/batches/work/{PHANTOM_ID}/reassign", viewer["headers"], {"employee_id": viewer["id"]})
            assert r.status_code == 403
            scoped = create_staff(admin_h, [scope_role], "bt-scoped")
            assert api("GET", "/api/sales/batches/overview", scoped["headers"]).status_code == 403
        finally:
            retire_roles([view_role, scope_role])

    @pytest.mark.parametrize("path", ID_ROUTES)
    def test_unknown_and_malformed_ids_are_404_for_the_manager(self, p, mgr, path):
        for bad in (PHANTOM_ID, "not-a-uuid", "0" * 8):
            r = api("GET", path.replace("{id}", bad), mgr)
            assert r.status_code == 404, f"{path} with {bad!r}: {r.status_code} {r.text[:120]}"

    def test_the_performance_routes_only_know_staff(self, p, mgr, admin_h):
        """A company owner or any non-staff account id is not an employee to report on - 404, not a 200 with somebody's name."""
        from sqlalchemy import select
        from models import User
        from sales_customer_test_utils import run_db

        async def owner_id(db):
            return str((await db.execute(select(User.id).where(User.role == "company_owner").limit(1))).scalar_one())

        stranger = run_db(owner_id)
        for path in (f"/api/sales/performance/employees/{stranger}", f"/api/sales/performance/employees/{stranger}/history"):
            assert api("GET", path, mgr).status_code == 404

    def test_the_performance_routes_report_only_on_data_entry_and_sales_people(self, p, mgr, admin_h):
        """A peer Sales Manager, a staff account holding no Sales role and the retired Onboarding role are not employees this view
        reports on (their work sessions would be a minute-level activity log): 404 - the same answer as an unknown id."""
        for who in ("manager", "no_roles", "onboarding"):
            for tail in ("", "/history"):
                r = api("GET", f"/api/sales/performance/employees/{p[who]['id']}{tail}", mgr)
                assert r.status_code == 404, (who, tail, r.status_code)
        assert api("GET", f"/api/sales/performance/employees/{p['employee']['id']}", mgr).status_code == 200
        assert api("GET", f"/api/sales/performance/employees/{p['data_entry']['id']}/history", mgr).status_code == 200

    def test_request_bodies_reject_unknown_fields(self, p, mgr):
        r = api("POST", f"/api/sales/batches/work/{PHANTOM_ID}/reassign", mgr, {"employee_id": p["employee2"]["id"], "status": "closed"})
        assert r.status_code == 422
        r = api("POST", f"/api/sales/batches/work/{PHANTOM_ID}/reassign", mgr, {})
        assert r.status_code == 422

    def test_search_input_is_validated(self, mgr):
        assert api("GET", "/api/sales/batches/search?q=a", mgr).status_code == 422                 # at least 2 characters
        assert api("GET", "/api/sales/batches/search?q=" + "x" * 101, mgr).status_code == 422
        assert api("GET", "/api/sales/batches/search?q=ab&field=password", mgr).status_code == 422
        assert api("GET", "/api/sales/batches/search?q=ab&limit=101", mgr).status_code == 422
        assert api("GET", "/api/sales/batches/data?limit=0", mgr).status_code == 422
        assert api("GET", "/api/sales/batches/data?status=weird", mgr).status_code == 422
        assert api("GET", "/api/sales/batches/work?status=weird", mgr).status_code == 422


# =============================================================================
# nothing leaks into the Data Entry / Sales Employee experience
# =============================================================================
class TestBatchesAreInvisibleToEverybodyElse:
    @pytest.fixture(scope="class")
    def world(self, p, mgr):
        entry = create_lead(H(p, "data_entry"))
        owned = worked_lead(mgr, p["employee"]["id"], business_name=f"Invisible {uniq()}")
        set_stage(H(p, "employee"), owned["id"], "contacted")
        return {"entry": entry, "owned": owned}

    def test_no_lead_response_carries_a_batch_field(self, p, mgr, world):
        for who, lead_id in (("data_entry", world["entry"]["id"]), ("employee", world["owned"]["id"])):
            h = H(p, who)
            single = api("GET", f"/api/sales/leads/{lead_id}", h).json()
            listing = api("GET", "/api/sales/leads?limit=100", h).json()
            queue = api("GET", "/api/sales/queue", h).json()
            created = api("POST", "/api/sales/leads/duplicate-check", h, {"business_name": "x"}) if who == "data_entry" else None
            for name, payload in (("lead", single), ("list", listing), ("queue", queue), ("dup", created.json() if created else {})):
                leaked = [k for k in keys_of(payload) if any(w in k.lower() for w in ("batch", "master", "attempt", "session"))]
                assert leaked == [], f"{who} {name}: leaked {leaked}"

    def test_the_create_response_of_data_entry_carries_none_either(self, p):
        created = api("POST", "/api/sales/leads", H(p, "data_entry"), {"business_name": f"Quiet {uniq()}", "phone": unique_lead_phone()})
        assert created.status_code == 201
        assert not any("batch" in k.lower() for k in keys_of(created.json()))

    def test_the_wait_list_state_is_not_shown_to_data_entry(self, p, mgr, world):
        h = H(p, "employee")
        owned = world["owned"]
        assert api("POST", f"/api/sales/leads/{owned['id']}/wait-list", h).status_code == 200
        events = api("GET", f"/api/sales/leads/{owned['id']}/activities", h).json()["items"]
        assert "lead_wait_listed" in [e["event_type"] for e in events]                        # the owner sees it
        assert "lead_wait_listed" in [e["event_type"] for e in api("GET", f"/api/sales/leads/{owned['id']}/activities", mgr).json()["items"]]

    def test_a_data_entry_person_never_reads_a_wait_list_event_on_a_lead_they_entered(self, p, mgr):
        entered = create_lead(H(p, "data_entry"))
        assign(mgr, entered["id"], p["employee"]["id"])
        assert api("POST", f"/api/sales/leads/{entered['id']}/wait-list", H(p, "employee")).status_code == 200
        r = api("GET", f"/api/sales/leads/{entered['id']}/activities", H(p, "data_entry"))
        assert r.status_code == 200                                                             # they entered it: they may read its timeline ...
        assert "lead_wait_listed" not in [e["event_type"] for e in r.json()["items"]]           # ... but not the salesperson's wait list
        seen = get_lead(H(p, "data_entry"), entered["id"])
        assert seen["wait_listed_at"] is None and seen["can"]["wait_list"] is False              # nor the state in the lead itself

    def test_the_modules_list_of_everybody_else_has_no_batches(self, p):
        for who in ("data_entry", "employee", "onboarding", "no_roles"):
            modules = api("GET", "/api/sales/me", H(p, who)).json()["modules"]
            assert "batches" not in modules and "performance" not in modules


# =============================================================================
# data batches
# =============================================================================
class TestDataBatches:
    @pytest.fixture(scope="class")
    def entered(self, p):
        """Ten leads entered by the two Data Entry people, alternating."""
        leads = []
        for i in range(10):
            who = "data_entry" if i % 2 == 0 else "data_entry2"
            leads.append((who, create_lead(H(p, who))))
        return leads

    def test_every_lead_data_entry_creates_belongs_to_a_batch_and_records_who_entered_it(self, p, mgr, entered):
        for who, lead in entered:
            t = trace(mgr, lead["id"])
            assert t["data_batch"] is not None and t["data_batch"]["seq"] >= 1
            assert t["entered_by"]["id"] == p[who]["id"] and t["entered_at"]

    def test_a_lead_a_manager_creates_is_in_no_data_batch(self, p, mgr):
        lead = create_lead(mgr)
        assert trace(mgr, lead["id"])["data_batch"] is None

    def test_a_lead_a_data_entry_person_creates_with_an_owner_chosen_by_nobody_is_still_unassigned(self, p, entered):
        for _, lead in entered:
            assert lead["assigned_to"] is None and lead["pipeline_stage"] == "new"          # batches never delay or hold a lead

    def test_the_batch_holds_the_leads_of_both_data_entry_people_together(self, p, mgr, entered):
        by_batch = {}
        for who, lead in entered:
            by_batch.setdefault(trace(mgr, lead["id"])["data_batch"]["id"], set()).add(who)
        detail_batch = next(iter(by_batch))
        detail = api("GET", f"/api/sales/batches/data/{detail_batch}", mgr).json()
        assert detail["lead_count"] == len(detail["leads"]) and detail["lead_count"] <= detail["capacity"] == 100
        assert sum(e["leads"] for e in detail["entrants"]) == detail["lead_count"]
        assert {p[w]["id"] for w in by_batch[detail_batch]} <= {e["id"] for e in detail["entrants"]}
        assert all(l["entered_by"]["name"] and l["entered_at"] for l in detail["leads"])
        assert {l["entered_by"]["id"] for l in detail["leads"]} >= {p[w]["id"] for w in by_batch[detail_batch]}

    def test_every_batch_count_equals_the_leads_that_carry_its_id(self, mgr, entered):
        """The invariant behind the slot lock: after any amount of (concurrent) lead creation, lead_count == the leads in the batch."""
        from sqlalchemy import text
        from sales_customer_test_utils import run_db

        async def broken(db):
            return (await db.execute(text(
                "SELECT b.seq FROM sales_data_batches b WHERE b.lead_count <> (SELECT count(*) FROM sales_leads l WHERE l.data_batch_id = b.id)"
            ))).all()

        assert run_db(broken) == []

    def test_concurrent_creation_never_skips_or_doubles_a_slot(self, p, mgr):
        before = api("GET", "/api/sales/batches/overview", mgr).json()
        def one(i):
            return api("POST", "/api/sales/leads", H(p, "data_entry" if i % 2 else "data_entry2"),
                       {"business_name": f"Race {uniq()}", "phone": unique_lead_phone()}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(one, range(16)))
        assert codes == [201] * 16
        from sqlalchemy import text
        from sales_customer_test_utils import run_db

        async def broken(db):
            return (await db.execute(text(
                "SELECT b.seq FROM sales_data_batches b WHERE b.lead_count <> (SELECT count(*) FROM sales_leads l WHERE l.data_batch_id = b.id)"
            ))).all()
        async def open_batches(db):
            return (await db.execute(text("SELECT count(*) FROM sales_data_batches WHERE status = 'open'"))).scalar_one()

        assert run_db(broken) == [] and run_db(open_batches) <= 1
        after = api("GET", "/api/sales/batches/overview", mgr).json()
        # (no open batch at all is legitimate: when the 100th slot was just taken the next batch opens with the next lead)
        assert after["full_data_batches"] >= before["full_data_batches"]
        slots = lambda ov: ov["full_data_batches"] * 100 + (ov["open_data_batch"] or {"lead_count": 0})["lead_count"]
        assert slots(after) >= slots(before) + 16                                               # every one of the 16 took a slot

    def test_the_lists_and_the_overview(self, mgr):
        ov = api("GET", "/api/sales/batches/overview", mgr).json()
        assert ov["data_batch_size"] == 100 and ov["master_batch_size"] == 10
        listing = api("GET", "/api/sales/batches/data?limit=5", mgr).json()
        assert listing["total"] >= 1 and len(listing["items"]) <= 5
        seqs = [b["seq"] for b in listing["items"]]
        assert seqs == sorted(seqs, reverse=True)                                              # newest first
        for status in ("open", "full"):
            for b in api("GET", f"/api/sales/batches/data?status={status}&limit=50", mgr).json()["items"]:
                assert b["status"] == status and (b["status"] == "full") == (b["filled_at"] is not None)
                assert b["lead_count"] <= b["capacity"] and (b["status"] != "full" or b["lead_count"] == 100)

    def test_a_master_batch_lists_exactly_ten_data_batches_when_any_exist(self, mgr):
        masters = api("GET", "/api/sales/batches/master?limit=50", mgr).json()
        for m in masters["items"]:
            detail = api("GET", f"/api/sales/batches/master/{m['id']}", mgr).json()
            assert m["data_batch_count"] == len(detail["data_batches"]) == 10 and m["lead_count"] == 1000
            assert all(b["status"] == "full" and b["master_batch"]["id"] == m["id"] for b in detail["data_batches"])
            filtered = api("GET", f"/api/sales/batches/data?master_batch_id={m['id']}&limit=50", mgr).json()
            assert filtered["total"] == 10
        assert api("GET", "/api/sales/batches/data?master_batch_id=nonsense", mgr).status_code == 404

    def test_nobody_can_move_or_rewrite_who_entered_a_lead(self, p, entered):
        """Not through the API (no field), and not by a stray UPDATE (the trigger)."""
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError
        from sales_customer_test_utils import run_db

        _, lead = entered[0]
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", H(p, "data_entry"), {"created_by": p["data_entry2"]["id"], "notes": "x"})
        assert r.status_code == 422                                                              # unknown field: refused outright

        async def rewrite(db):
            await db.execute(text("UPDATE sales_leads SET created_by = :u WHERE id = :l"), {"u": p["data_entry2"]["id"], "l": lead["id"]})

        with pytest.raises(DBAPIError):
            run_db(rewrite)


# =============================================================================
# the wait list
# =============================================================================
class TestWaitList:
    def test_it_is_the_owners_own_tool(self, p, mgr, admin_h):
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Mine {uniq()}")
        url = f"/api/sales/leads/{lead['id']}/wait-list"
        assert api("POST", url, H(p, "employee2")).status_code == 403                # another salesperson: it is not theirs
        assert api("POST", url, mgr).status_code == 403                              # nor a manager's, though they see every lead
        assert api("POST", url, admin_h).status_code == 403
        for who in ("data_entry", "onboarding", "no_roles"):
            assert api("POST", url, H(p, who)).status_code == 403
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/wait-list", H(p, "employee")).status_code == 404
        assert api("POST", "/api/sales/leads/not-a-uuid/wait-list", H(p, "employee")).status_code == 404
        r = api("POST", url, H(p, "employee"))
        assert r.status_code == 200 and r.json()["changed"] is True
        assert paths_of(mgr, lead["id"]) == ["wait_pending"]

    def test_it_is_a_timestamp_and_a_pending_status_and_nothing_else(self, p, mgr):
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Simple {uniq()}")
        before = get_lead(H(p, "employee"), lead["id"])
        assert before["can"]["wait_list"] is True and before["wait_listed_at"] is None
        r = api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee")).json()
        after = r["lead"]
        assert after["wait_listed_at"] and after["can"]["wait_list"] is False
        for field in ("pipeline_stage", "assigned_to", "priority", "business_name", "lost_reason", "closed_at", "archived_at"):
            assert after[field] == before[field], field                                   # the lead itself is untouched
        assert after["assigned_to"]["id"] == p["employee"]["id"]
        again = api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee")).json()
        assert again["changed"] is False and again["lead"]["wait_listed_at"] == after["wait_listed_at"]      # idempotent: the first stamp stands
        assert paths_of(mgr, lead["id"]) == ["wait_pending"]                               # still ONE attempt

    def test_the_waited_lead_goes_to_the_end_of_the_queue(self, p, mgr):
        a = worked_lead(mgr, p["employee2"]["id"], business_name=f"QA {uniq()}")
        b = worked_lead(mgr, p["employee2"]["id"], business_name=f"QB {uniq()}")
        h = H(p, "employee2")

        def order():
            ids, position = [], 0
            while True:
                q = api("GET", f"/api/sales/queue?position={position}", h).json()
                if q["lead"] is None:
                    return ids
                ids.append(q["lead"]["id"])
                position += 1
                if position >= q["total"]:
                    return ids
        mine = [x for x in order() if x in (a["id"], b["id"])]
        assert mine == [a["id"], b["id"]]
        assert api("POST", f"/api/sales/leads/{a['id']}/wait-list", h).status_code == 200
        mine = [x for x in order() if x in (a["id"], b["id"])]
        assert mine == [b["id"], a["id"]]                                                  # a waited: b is worked first

    def test_a_lead_that_cannot_wait(self, p, mgr):
        e = H(p, "employee")
        unassigned = create_lead(mgr)
        assert api("POST", f"/api/sales/leads/{unassigned['id']}/wait-list", mgr).status_code == 403      # nobody owns it
        won = worked_lead(mgr, p["employee"]["id"])
        agree(e, won)                                                                       # the salesperson's own win: the Customer Setup
        r = api("POST", f"/api/sales/leads/{won['id']}/wait-list", e)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lead_closed"
        lost = worked_lead(mgr, p["employee"]["id"])
        set_stage(e, lost["id"], "lost", lost_reason="not_interested")
        assert api("POST", f"/api/sales/leads/{lost['id']}/wait-list", e).status_code == 409
        archived = worked_lead(mgr, p["employee"]["id"])
        assert api("DELETE", f"/api/sales/leads/{archived['id']}", mgr).status_code == 200
        r = api("POST", f"/api/sales/leads/{archived['id']}/wait-list", e)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lead_archived"

    def test_it_ends_in_accepted_or_rejected_and_the_split_shows_it(self, p, mgr):
        e = H(p, "employee")
        won = worked_lead(mgr, p["employee"]["id"])
        lost = worked_lead(mgr, p["employee"]["id"])
        direct_won = worked_lead(mgr, p["employee"]["id"])
        direct_lost = worked_lead(mgr, p["employee"]["id"])
        for lead in (won, lost):
            assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", e).status_code == 200
        agree(e, won)                                                                       # the wait-listed lead is agreed (the Customer Setup)
        set_stage(e, lost["id"], "lost", lost_reason="too_expensive")
        agree(e, direct_won)
        set_stage(e, direct_lost["id"], "lost", lost_reason="other")
        assert paths_of(mgr, won["id"]) == ["wait_accepted"]
        assert paths_of(mgr, lost["id"]) == ["wait_rejected"]
        assert paths_of(mgr, direct_won["id"]) == ["direct_accepted"]
        assert paths_of(mgr, direct_lost["id"]) == ["direct_rejected"]
        t = trace(mgr, won["id"])["attempts"][0]
        assert t["first_outcome"] == "wait_list" and t["wait_listed_at"] and t["result"] == "accepted" and t["result_at"] >= t["wait_listed_at"]
        assert t["employee"]["id"] == p["employee"]["id"] and t["recorded_by"]["id"] == p["employee"]["id"]

    def test_the_customer_setup_is_the_accepted_result(self, p, mgr):
        e = H(p, "employee")
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Agreed Co {uniq()}")
        assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", e).status_code == 200
        body = {
            "business_name": lead["business_name"], "owner_name": "Owner Person", "owner_email": unique_lead_email("owner"),
            "owner_phone": unique_lead_phone(), "owner_password": OWNER_PASSWORD, "subscription_plan_id": make_plan(), "subscription_type": "trial",
        }
        r = api("POST", f"/api/sales/leads/{lead['id']}/setup", e, body)
        assert r.status_code == 201, r.text[:300]
        assert paths_of(mgr, lead["id"]) == ["wait_accepted"]
        again = worked_lead(mgr, p["employee"]["id"], business_name=f"Agreed Direct {uniq()}")
        body2 = {**body, "business_name": again["business_name"], "owner_email": unique_lead_email("owner"), "owner_phone": unique_lead_phone()}
        assert api("POST", f"/api/sales/leads/{again['id']}/setup", e, body2).status_code == 201
        assert paths_of(mgr, again["id"]) == ["direct_accepted"]

    def test_a_manager_deciding_a_leads_fate_counts_for_its_owner(self, p, mgr):
        """The attempt belongs to the lead's owner (their result) and remembers who pressed the button."""
        lead = worked_lead(mgr, p["employee2"]["id"])
        set_stage(mgr, lead["id"], "lost", lost_reason="other")
        attempt = trace(mgr, lead["id"])["attempts"][0]
        assert attempt["employee"]["id"] == p["employee2"]["id"] and attempt["recorded_by"]["id"] == p["manager"]["id"]
        assert attempt["path"] == "direct_rejected"

    def test_leaving_the_owner_releases_a_pending_wait(self, p, mgr):
        e = H(p, "employee")
        for how in ("reassign", "unassign", "archive"):
            lead = worked_lead(mgr, p["employee"]["id"])
            assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", e).status_code == 200
            if how == "reassign":
                assign(mgr, lead["id"], p["employee2"]["id"])
            elif how == "unassign":
                assert api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr).status_code == 200
            else:
                assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200
            assert paths_of(mgr, lead["id"]) == ["released"], how
        # the new owner's own decision is a NEW attempt, the old one stays
        lead = worked_lead(mgr, p["employee"]["id"])
        api("POST", f"/api/sales/leads/{lead['id']}/wait-list", e)
        assign(mgr, lead["id"], p["employee2"]["id"])
        set_stage(H(p, "employee2"), lead["id"], "lost", lost_reason="other")
        attempts = trace(mgr, lead["id"])["attempts"]
        assert [(a["attempt_no"], a["path"], a["employee"]["id"]) for a in attempts] == [
            (1, "released", p["employee"]["id"]), (2, "direct_rejected", p["employee2"]["id"])]

    def test_a_lead_on_the_wait_list_is_out_of_data_entrys_reach(self, p, mgr):
        """A wait-listed lead is a salesperson's (the wait list is the OWNER's tool), so it is assigned - and Data Entry, who entered it
        and still sees it, can no longer change, archive, delete or restore it, however recently they entered it. Nothing they try
        touches the pending wait; the lead comes back to them only if a manager unassigns it (which also releases the wait)."""
        de = H(p, "data_entry")
        lead = create_lead(de)
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", de, {"notes": "while unassigned"}).status_code == 200      # theirs, unassigned
        assign(mgr, lead["id"], p["employee"]["id"])
        assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee")).status_code == 200
        seen = api("GET", f"/api/sales/leads/{lead['id']}", de).json()
        assert seen["can"]["update"] is False and seen["can"]["archive"] is False and seen["can"]["restore"] is False
        assert seen["can"]["edit_locked"] is True and seen["can"]["wait_list"] is False
        assert seen["wait_listed_at"] is None                                             # the wait list is not Data Entry's to see either
        before = get_lead(mgr, lead["id"])
        for method, path, body in (("PATCH", f"/api/sales/leads/{lead['id']}", {"notes": "x"}), ("DELETE", f"/api/sales/leads/{lead['id']}", None),
                                   ("POST", f"/api/sales/leads/{lead['id']}/restore", None)):
            r = api(method, path, de, body)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "lead_edit_window", (method, r.text[:200])
        assert get_lead(mgr, lead["id"]) == before and before["archived_at"] is None
        assert paths_of(mgr, lead["id"]) == ["wait_pending"]                              # still waiting: nothing they tried reached it
        # the manager takes it back to the pool: the wait is released, and it is Data Entry's again (unassigned, inside their latest ten)
        assert api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr).status_code == 200
        assert paths_of(mgr, lead["id"]) == ["released"]
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", de, {"notes": "back in the pool"}).status_code == 200

    def test_a_wait_listed_lead_a_manager_archives_stays_out_of_data_entrys_reach(self, p, mgr):
        de = H(p, "data_entry")
        lead = create_lead(de)
        assign(mgr, lead["id"], p["employee"]["id"])
        assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee")).status_code == 200
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200    # archiving releases the pending wait ...
        assert paths_of(mgr, lead["id"]) == ["released"]
        r = api("POST", f"/api/sales/leads/{lead['id']}/restore", de)                    # ... but the lead is still assigned: not Data Entry's to restore
        assert r.status_code == 403 and r.json()["detail"]["code"] == "lead_edit_window"
        assert get_lead(mgr, lead["id"])["archived_at"] is not None

    def test_a_lead_has_at_most_one_pending_attempt_even_when_raced(self, p, mgr):
        lead = worked_lead(mgr, p["employee"]["id"])
        url = f"/api/sales/leads/{lead['id']}/wait-list"
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: api("POST", url, H(p, "employee")), range(6)))
        assert all(r.status_code == 200 for r in results)
        assert Counter(r.json()["changed"] for r in results) == Counter({True: 1, False: 5})
        assert paths_of(mgr, lead["id"]) == ["wait_pending"]

    def test_the_wait_list_work_is_audited_on_the_timeline(self, p, mgr):
        lead = worked_lead(mgr, p["employee"]["id"])
        api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee"))
        events = api("GET", f"/api/sales/leads/{lead['id']}/activities", mgr).json()["items"]
        event = next(e for e in events if e["event_type"] == "lead_wait_listed")
        assert event["actor"]["id"] == p["employee"]["id"] and event["after"] == {"wait_list": True} and event["before"] == {"wait_list": False}


# =============================================================================
# Sales Work Batches: 100 worked leads, open / closed, the split, reassignment
# =============================================================================
class TestWorkBatchLifecycle:
    """One fresh salesperson works 100 leads: 30 rejected directly, 30 accepted directly, 40 put on the wait list (the batch forms
    with the 100th decision and stays OPEN while they wait); the 40 are then decided (25 accepted, 15 rejected) and the batch
    CLOSES. A manager hands it to another salesperson, who works the 45 leads that were not won."""

    @pytest.fixture(scope="class")
    def world(self, admin_h, p, mgr):
        worker = named_staff(admin_h, ["sales_employee"], "bt-worker")
        second = named_staff(admin_h, ["sales_employee"], "bt-second")
        ids = []
        for i in range(100):
            lead = create_lead(mgr, business_name=f"Batch {uniq()} {i}")
            ids.append(lead["id"])
        for start in range(0, 100, 50):
            r = api("POST", "/api/sales/leads/bulk-assign", mgr, {"lead_ids": ids[start:start + 50], "assigned_to": worker["id"]})
            assert r.status_code == 200 and r.json()["assigned"] == 50
        h = worker["headers"]
        plan = {"rejected": ids[:30], "accepted": ids[30:60], "waiting": ids[60:]}
        for lead_id in plan["rejected"]:
            set_stage(h, lead_id, "lost", lost_reason="not_interested")
        for lead_id in plan["accepted"]:
            win(h, lead_id)
        first_batch_before = api("GET", f"/api/sales/batches/work?employee_id={worker['id']}", mgr).json()
        for n, lead_id in enumerate(plan["waiting"]):
            assert api("POST", f"/api/sales/leads/{lead_id}/wait-list", h).status_code == 200
        return {"worker": worker, "second": second, "ids": ids, "plan": plan, "h": h, "before": first_batch_before}

    def _batch(self, mgr, world, status=None):
        url = f"/api/sales/batches/work?employee_id={world['worker']['id']}" + (f"&status={status}" if status else "")
        return api("GET", url, mgr).json()

    def test_no_batch_before_the_hundredth_decision_and_one_after(self, mgr, world):
        assert world["before"]["total"] == 0                                              # 60 decisions: nothing yet
        listing = self._batch(mgr, world)
        assert listing["total"] == 1
        batch = listing["items"][0]
        assert batch["lead_count"] == 100 and batch["status"] == "open" and batch["closed_at"] is None
        assert batch["original_employee"]["id"] == batch["current_employee"]["id"] == world["worker"]["id"]
        assert batch["attempt_count"] == 1 and batch["can_reassign"] is False            # open: it cannot be handed on yet

    def test_it_stays_open_while_any_lead_waits_and_the_split_shows_it(self, mgr, world):
        stats = self._batch(mgr, world)["items"][0]["stats"]
        assert (stats["total"], stats["accepted_direct"], stats["rejected_direct"], stats["wait_listed"], stats["wait_pending"]) == (100, 30, 30, 40, 40)
        assert stats["wait_accepted"] == stats["wait_rejected"] == 0 and stats["decided"] == 60
        assert stats["pct_accepted_direct"] == stats["pct_rejected_direct"] == 50.0 and stats["wait_list_rate"] == 40.0

    def test_an_open_batch_cannot_be_reassigned(self, mgr, world):
        batch = self._batch(mgr, world)["items"][0]
        r = api("POST", f"/api/sales/batches/work/{batch['id']}/reassign", mgr, {"employee_id": world["second"]["id"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "work_batch_open"
        assert self._batch(mgr, world)["items"][0]["attempt_count"] == 1                 # nothing was created

    def test_deciding_the_waiting_leads_closes_it(self, mgr, world):
        h, waiting = world["h"], world["plan"]["waiting"]
        for lead_id in waiting[:25]:
            win(h, lead_id)
        assert self._batch(mgr, world)["items"][0]["status"] == "open"                    # 15 still wait
        for lead_id in waiting[25:-1]:
            set_stage(h, lead_id, "lost", lost_reason="too_expensive")
        assert self._batch(mgr, world)["items"][0]["status"] == "open"                    # ONE still waits
        set_stage(h, waiting[-1], "lost", lost_reason="too_expensive")
        batch = self._batch(mgr, world)["items"][0]
        assert batch["status"] == "closed" and batch["closed_at"] and batch["can_reassign"] is True
        stats = batch["stats"]
        assert (stats["accepted_direct"], stats["rejected_direct"], stats["wait_accepted"], stats["wait_rejected"], stats["wait_pending"]) == (30, 30, 25, 15, 0)
        assert (stats["accepted"], stats["rejected"], stats["decided"]) == (55, 45, 100)
        assert (stats["pct_accepted_direct"], stats["pct_rejected_direct"], stats["pct_wait_accepted"], stats["pct_wait_rejected"]) == (30.0, 30.0, 25.0, 15.0)
        assert stats["pct_accepted"] == 55.0 and stats["pct_rejected"] == 45.0
        assert self._batch(mgr, world, "open")["total"] == 0 and self._batch(mgr, world, "closed")["total"] == 1

    def test_the_detail_has_every_lead_with_its_history(self, mgr, world):
        batch = self._batch(mgr, world)["items"][0]
        detail = api("GET", f"/api/sales/batches/work/{batch['id']}", mgr).json()
        assert [l["ordinal"] for l in detail["leads"]] == list(range(1, 101))
        assert {l["id"] for l in detail["leads"]} == set(world["ids"])
        paths = Counter(l["attempts"][0]["path"] for l in detail["leads"])
        assert paths == Counter({"direct_rejected": 30, "direct_accepted": 30, "wait_accepted": 25, "wait_rejected": 15})
        assert len(detail["attempts"]) == 1 and detail["attempts"][0]["kind"] == "initial" and detail["attempts"][0]["status"] == "closed"
        assert detail["attempts"][0]["started_by"]["id"] == world["worker"]["id"]

    def test_a_second_batch_only_forms_after_another_hundred(self, mgr, world):
        extra = worked_lead(mgr, world["worker"]["id"])
        set_stage(world["h"], extra["id"], "lost", lost_reason="other")
        assert self._batch(mgr, world)["total"] == 1                                       # 101 decisions: still one batch of 100

    def test_the_manager_hands_the_completed_batch_to_another_salesperson(self, admin_h, p, mgr, world):
        batch = self._batch(mgr, world)["items"][0]
        before_ids = {l["id"] for l in api("GET", f"/api/sales/batches/work/{batch['id']}", mgr).json()["leads"]}
        lead_rows_before = api("GET", f"/api/sales/leads/{world['ids'][0]}", mgr).json()

        # not to somebody who cannot receive leads, nor to a data entry person, nor to the same person
        data_entry = create_staff(admin_h, ["lead_data_entry"], "bt-notreceiver")
        for bad, expect, code in ((data_entry["id"], 400, "assignee_not_eligible"), (PHANTOM_ID, 400, "assignee_not_eligible"),
                                  (world["worker"]["id"], 409, "same_employee")):
            r = api("POST", f"/api/sales/batches/work/{batch['id']}/reassign", mgr, {"employee_id": bad})
            assert r.status_code == expect and r.json()["detail"]["code"] == code, (bad, r.text[:200])

        r = api("POST", f"/api/sales/batches/work/{batch['id']}/reassign", mgr, {"employee_id": world["second"]["id"]})
        assert r.status_code == 200, r.text[:400]
        detail = r.json()
        assert detail["status"] == "open" and detail["current_employee"]["id"] == world["second"]["id"]
        assert detail["original_employee"]["id"] == world["worker"]["id"] and detail["attempt_count"] == 2
        first, second = detail["attempts"]
        assert (first["attempt_no"], first["kind"], first["status"]) == (1, "initial", "closed")
        assert (second["attempt_no"], second["kind"], second["status"]) == (2, "reassignment", "open")
        assert second["employee"]["id"] == world["second"]["id"] and second["started_by"]["id"] == p["manager"]["id"]      # who handed it over
        assert second["lead_count"] == 45 and second["stats"]["not_started"] == 45
        assert first["stats"]["total"] == 100 and first["stats"]["accepted_direct"] == 30                # the first attempt is exactly as it was

        # THE SAME LEADS: nothing was copied, nothing created
        assert {l["id"] for l in detail["leads"]} == before_ids == set(world["ids"]) and len(detail["leads"]) == 100
        assert api("GET", f"/api/sales/leads/{world['ids'][0]}", mgr).json()["id"] == lead_rows_before["id"]
        # the 55 that agreed stay won and with their first owner; the 45 that did not are the second person's and reopened
        owners = Counter((l["assigned_to"] or {}).get("id") for l in detail["leads"])
        assert owners == Counter({world["worker"]["id"]: 55, world["second"]["id"]: 45})
        assert all(l["pipeline_stage"] == "won" for l in detail["leads"] if l["assigned_to"]["id"] == world["worker"]["id"])
        assert all(l["pipeline_stage"] == "assigned" for l in detail["leads"] if l["assigned_to"]["id"] == world["second"]["id"])

    def test_the_audit_trail_records_the_reassignment(self, mgr, world):
        batch = self._batch(mgr, world)["items"][0]
        events = [e for e in audit_events(batch["id"]) if e["action"] == "work_batch_reassigned"]
        assert len(events) == 1
        e = events[0]
        assert e["target_user_id"] == world["second"]["id"] and e["after"]["employee_id"] == world["second"]["id"]
        assert e["before"]["employee_id"] == world["worker"]["id"] and e["after"]["lead_count"] == 45
        assert e["metadata"]["kept"] == {"accepted": 55, "archived": 0, "waiting_elsewhere": 0}

    def test_the_reopened_leads_carry_their_timeline_and_the_new_owner_can_work_them(self, mgr, world):
        detail = api("GET", f"/api/sales/batches/work/{self._batch(mgr, world)['items'][0]['id']}", mgr).json()
        lost_lead = next(l for l in detail["leads"] if l["assigned_to"]["id"] == world["second"]["id"])
        events = [e["event_type"] for e in api("GET", f"/api/sales/leads/{lost_lead['id']}/activities?limit=200", mgr).json()["items"]]
        assert "lead_created" in events and "lead_marked_lost" in events and "lead_reassigned" in events                 # the whole history is there
        reopened = [e for e in api("GET", f"/api/sales/leads/{lost_lead['id']}/activities", mgr).json()["items"] if e["event_type"] == "stage_changed"][0]
        assert reopened["metadata"]["cause"] == "reassignment" and reopened["metadata"].get("reopened") is True
        queue = api("GET", "/api/sales/queue", world["second"]["headers"]).json()
        assert queue["total"] == 45                                                                                       # exactly their 45, nobody else's

    def test_the_second_attempt_closes_when_its_leads_are_decided_and_history_keeps_both(self, mgr, world):
        h = world["second"]["headers"]
        detail = api("GET", f"/api/sales/batches/work/{self._batch(mgr, world)['items'][0]['id']}", mgr).json()
        mine = [l["id"] for l in detail["leads"] if l["assigned_to"]["id"] == world["second"]["id"]]
        assert len(mine) == 45
        for lead_id in mine[:20]:
            set_stage(h, lead_id, "lost", lost_reason="not_interested")
        assert self._batch(mgr, world)["items"][0]["status"] == "open"
        for lead_id in mine[20:]:
            win(h, lead_id)
        batch = self._batch(mgr, world)["items"][0]
        assert batch["status"] == "closed" and batch["attempt_count"] == 2 and batch["can_reassign"] is True
        detail = api("GET", f"/api/sales/batches/work/{batch['id']}", mgr).json()
        first, second = detail["attempts"]
        assert first["status"] == second["status"] == "closed" and first["closed_at"] and second["closed_at"] >= first["closed_at"]
        assert (first["stats"]["rejected_direct"], first["stats"]["wait_rejected"]) == (30, 15)                            # unchanged
        assert (second["stats"]["rejected_direct"], second["stats"]["accepted_direct"], second["stats"]["total"]) == (20, 25, 45)
        assert second["stats"]["not_started"] == 0
        # each reworked lead shows BOTH tries, in order, with who and when; each lead the first person won shows one
        for l in detail["leads"]:
            employees = [a["employee"]["id"] for a in l["attempts"]]
            if l["id"] in mine:
                assert employees == [world["worker"]["id"], world["second"]["id"]] and [a["attempt_no"] for a in l["attempts"]] == [1, 2]
                assert all(a["outcome_at"] and a["recorded_by"] for a in l["attempts"])
            else:
                assert employees == [world["worker"]["id"]]

    def test_the_first_persons_numbers_did_not_move(self, mgr, world):
        perf = perf_row(mgr, "sales", world["worker"])
        life = perf["periods"]["lifetime"]["stats"]
        # 30 + 1 rejected directly: the 101st decision (test_a_second_batch_only_forms_after_another_hundred) is outside the batch
        assert (life["accepted_direct"], life["rejected_direct"], life["wait_accepted"], life["wait_rejected"], life["total"]) == (30, 31, 25, 15, 101)
        assert perf["periods"]["lifetime"]["completed_batches"] == 1                                                       # the first batch's first attempt
        second = perf_row(mgr, "sales", world["second"])
        s = second["periods"]["lifetime"]
        assert (s["stats"]["rejected_direct"], s["stats"]["accepted_direct"], s["stats"]["total"]) == (20, 25, 45) and s["completed_batches"] == 1

    def test_a_batch_can_be_handed_on_again_and_only_what_is_not_won_moves(self, mgr, world):
        """Attempt 2's 20 lost leads are the only ones not won: handing the batch back moves exactly those, over the SAME rows."""
        batch = self._batch(mgr, world)["items"][0]
        r = api("POST", f"/api/sales/batches/work/{batch['id']}/reassign", mgr, {"employee_id": world["worker"]["id"]})
        assert r.status_code == 200, r.text[:300]
        detail = r.json()
        assert detail["attempt_count"] == 3 and detail["current_employee"]["id"] == world["worker"]["id"] and detail["status"] == "open"
        third = detail["attempts"][2]
        assert (third["attempt_no"], third["kind"], third["lead_count"]) == (3, "reassignment", 20)
        assert len(detail["leads"]) == 100 and {l["id"] for l in detail["leads"]} == set(world["ids"])
        assert [a["attempt_no"] for a in detail["attempts"]] == [1, 2, 3] and detail["attempts"][0]["stats"]["total"] == 100
        assert len([e for e in audit_events(batch["id"]) if e["action"] == "work_batch_reassigned"]) == 2
        # an open batch (attempt 3 is being worked) is refused again
        assert api("POST", f"/api/sales/batches/work/{batch['id']}/reassign", mgr, {"employee_id": world["second"]["id"]}).status_code == 409

    def test_the_filters(self, mgr, world):
        assert self._batch(mgr, world)["total"] == 1
        for who in (world["worker"]["id"], world["second"]["id"]):
            assert api("GET", f"/api/sales/batches/work?employee_id={who}", mgr).json()["total"] == 1          # a batch is listed under everybody who worked it
        assert api("GET", "/api/sales/batches/work?employee_id=not-a-uuid", mgr).status_code == 404


# =============================================================================
# global search and trace
# =============================================================================
class TestGlobalSearch:
    @pytest.fixture(scope="class")
    def target(self, p, mgr):
        token = uniq(8)
        phone = unique_lead_phone()
        lead = api("POST", "/api/sales/leads", H(p, "data_entry"), {
            "business_name": f"Zanzibar {token} Pharmacy", "business_type": f"Veterinary {token}", "contact_name": f"Halima {token} Karim",
            "phone": phone, "city": f"Basra {token}"}).json()
        assign(mgr, lead["id"], p["employee"]["id"])
        api("POST", f"/api/sales/leads/{lead['id']}/wait-list", H(p, "employee"))
        win(H(p, "employee"), lead["id"])
        return {"lead": lead, "token": token, "phone": phone}

    def _ids(self, mgr, q, **params):
        r = api("GET", "/api/sales/batches/search", mgr, params={"q": q, **params})
        assert r.status_code == 200, r.text[:200]
        return [i["id"] for i in r.json()["items"]], r.json()

    @pytest.mark.parametrize("field,make", [
        ("business_name", lambda t: f"zanzibar {t['token']}"),
        ("contact_name", lambda t: f"halima {t['token']}"),
        ("business_type", lambda t: f"veterinary {t['token']}"),
        ("phone", lambda t: t["phone"]),
        ("any", lambda t: t["token"]),
    ])
    def test_it_finds_the_lead_by_each_field(self, mgr, target, field, make):
        ids, body = self._ids(mgr, make(target), field=field)
        assert target["lead"]["id"] in ids and body["total"] >= 1

    def test_a_field_search_looks_only_in_that_field(self, mgr, target):
        assert target["lead"]["id"] not in self._ids(mgr, f"veterinary {target['token']}", field="business_name")[0]
        assert target["lead"]["id"] not in self._ids(mgr, f"halima {target['token']}", field="business_type")[0]
        assert target["lead"]["id"] not in self._ids(mgr, f"zanzibar {target['token']}", field="phone")[0]

    def test_a_phone_is_found_by_its_digits_whatever_the_spacing(self, mgr, target):
        digits = target["phone"].lstrip("+")
        spaced = f"+{digits[:1]} {digits[1:4]} {digits[4:7]} {digits[7:]}"
        assert target["lead"]["id"] in self._ids(mgr, spaced, field="phone")[0]

    def test_each_result_is_traced_from_the_lead_to_its_results(self, p, mgr, target):
        result = next(i for i in api("GET", "/api/sales/batches/search", mgr, params={"q": target["token"]}).json()["items"] if i["id"] == target["lead"]["id"])
        assert result["entered_by"]["id"] == p["data_entry"]["id"] and result["entered_at"]
        assert result["data_batch"] and result["data_batch"]["seq"] >= 1
        assert result["master_batch"] is None or result["master_batch"]["seq"] >= 1
        assert [a["path"] for a in result["attempts"]] == ["wait_accepted"]
        assert result["attempts"][0]["employee"]["id"] == p["employee"]["id"]
        assert result["work_batches"] == []                                                # one decision: no batch of 100 yet

    def test_the_trace_adds_the_timeline(self, p, mgr, target):
        t = trace(mgr, target["lead"]["id"])
        assert [e["event_type"] for e in reversed(t["timeline"])][:1] == ["lead_created"] and t["timeline_total"] == len(t["timeline"])
        assert {"lead_assigned", "lead_wait_listed", "lead_marked_won"} <= {e["event_type"] for e in t["timeline"]}

    def test_a_lead_of_a_full_batch_shows_its_data_and_master_batches(self, mgr):
        masters = api("GET", "/api/sales/batches/master?limit=1", mgr).json()["items"]
        if not masters:
            pytest.skip("no master batch has formed on this database yet (the boundary is proven in test_sales_batches_db.py)")
        detail = api("GET", f"/api/sales/batches/master/{masters[0]['id']}", mgr).json()
        data_batch = api("GET", f"/api/sales/batches/data/{detail['data_batches'][0]['id']}", mgr).json()
        lead_id = data_batch["leads"][0]["id"]
        t = trace(mgr, lead_id)
        assert t["data_batch"]["id"] == data_batch["id"] and t["master_batch"]["id"] == masters[0]["id"]

    def test_the_result_list_is_bounded(self, mgr):
        r = api("GET", "/api/sales/batches/search", mgr, params={"q": "Acme", "limit": 3}).json()
        assert len(r["items"]) <= 3 and r["limit"] == 3

    def test_wildcards_are_matched_literally(self, mgr):
        for q in ("%%", "__", "a%b_c\\d"):
            r = api("GET", "/api/sales/batches/search", mgr, params={"q": q})
            assert r.status_code == 200
            for item in r.json()["items"]:                                                  # every result really contains the literal text
                haystack = " ".join(str(item.get(k) or "") for k in ("business_name", "business_type", "contact_name", "phone")).lower()
                assert q.lower() in haystack or q.replace("\\", "").lower() in haystack

    def test_a_search_never_returns_a_secret_or_a_normalised_column(self, mgr, target):
        r = api("GET", "/api/sales/batches/search", mgr, params={"q": target["token"]}).json()
        assert not {k for k in keys_of(r) if k.endswith("_norm") or "password" in k.lower() or k in ("estimated_value", "notes", "description")}


# =============================================================================
# employee performance and history
# =============================================================================
class TestPerformance:
    @pytest.fixture(scope="class")
    def crew(self, admin_h):
        return {"entry": named_staff(admin_h, ["lead_data_entry"], "bt-perf-entry"), "sales": named_staff(admin_h, ["sales_employee"], "bt-perf-sales")}

    def _entry(self, mgr, who):
        return perf_row(mgr, "data-entry", who)

    def _sales(self, mgr, who):
        return perf_row(mgr, "sales", who)

    def test_the_lists_are_paginated_ordered_by_name_and_filterable(self, mgr, crew):
        for kind in ("data-entry", "sales"):
            page = api("GET", f"/api/sales/performance/{kind}", mgr, params={"limit": 5}).json()
            assert page["limit"] == 5 and page["offset"] == 0 and len(page["items"]) <= 5 and page["total"] >= len(page["items"])
            names = [(i["user"]["name"], i["user"]["id"]) for i in page["items"]]
            assert names == sorted(names)
            nxt = api("GET", f"/api/sales/performance/{kind}", mgr, params={"limit": 5, "offset": 5}).json()
            assert not {i["user"]["id"] for i in nxt["items"]} & {i["user"]["id"] for i in page["items"]}
            assert api("GET", f"/api/sales/performance/{kind}", mgr, params={"limit": 101}).status_code == 422
            assert api("GET", f"/api/sales/performance/{kind}", mgr, params={"q": "x" * 101}).status_code == 422
            none = api("GET", f"/api/sales/performance/{kind}", mgr, params={"q": f"no-such-person-{uniq()}"}).json()
            assert none["items"] == [] and none["total"] == 0
            wild = api("GET", f"/api/sales/performance/{kind}", mgr, params={"q": "%_"}).json()          # LIKE wildcards are literal
            assert all("%_" in i["user"]["name"] for i in wild["items"])
        assert perf_row(mgr, "data-entry", crew["entry"])["user"]["id"] == crew["entry"]["id"]
        # the two lists are about different people: a Data Entry person is not a salesperson and the reverse
        assert api("GET", "/api/sales/performance/sales", mgr, params={"q": crew["entry"]["name"]}).json()["total"] == 0
        assert api("GET", "/api/sales/performance/data-entry", mgr, params={"q": crew["sales"]["name"]}).json()["total"] == 0

    def test_a_new_person_starts_at_zero_and_every_period_is_present(self, mgr, crew):
        for who, getter in ((crew["entry"], self._entry), (crew["sales"], self._sales)):
            item = getter(mgr, who)
            assert set(item["periods"]) == {"today", "month", "year", "lifetime"}
        assert all(v["leads_entered"] == 0 and v["work_hours"] == 0 for v in self._entry(mgr, crew["entry"])["periods"].values())
        assert all(v["stats"]["total"] == 0 and v["stats"]["pct_accepted"] == 0 for v in self._sales(mgr, crew["sales"])["periods"].values())

    def test_data_entry_counts_the_leads_entered_in_each_period_and_the_hours(self, mgr, crew):
        for _ in range(4):
            create_lead(crew["entry"]["headers"])
        item = self._entry(mgr, crew["entry"])
        for period in ("today", "month", "year", "lifetime"):
            assert item["periods"][period]["leads_entered"] == 4, period
        assert 0 < item["periods"]["today"]["work_hours"] <= 0.1                              # a minute or two of recorded work
        assert item["periods"]["lifetime"]["work_hours"] >= item["periods"]["today"]["work_hours"] > 0
        assert item["periods"]["year"]["work_hours"] >= item["periods"]["month"]["work_hours"] >= item["periods"]["today"]["work_hours"]

    def test_sales_counts_every_kind_of_decision_with_percentages(self, mgr, crew):
        h = crew["sales"]["headers"]
        rows = [worked_lead(mgr, crew["sales"]["id"]) for _ in range(5)]
        set_stage(h, rows[0]["id"], "lost", lost_reason="other")                                # direct rejected
        win(h, rows[1]["id"])                                                                   # direct accepted
        for r in rows[2:5]:
            api("POST", f"/api/sales/leads/{r['id']}/wait-list", h)
        win(h, rows[2]["id"])                                                                   # wait -> accepted
        set_stage(h, rows[3]["id"], "lost", lost_reason="other")                                # wait -> rejected
        s = self._sales(mgr, crew["sales"])["periods"]["today"]["stats"]                        # rows[4] still waits
        assert (s["total"], s["accepted_direct"], s["rejected_direct"], s["wait_listed"], s["wait_accepted"], s["wait_rejected"], s["wait_pending"]) == (5, 1, 1, 3, 1, 1, 1)
        assert (s["accepted"], s["rejected"], s["decided"]) == (2, 2, 4)
        assert (s["pct_accepted"], s["pct_rejected"], s["pct_accepted_direct"], s["pct_wait_rejected"]) == (50.0, 50.0, 25.0, 25.0)
        assert s["wait_list_rate"] == 60.0
        for period in ("month", "year", "lifetime"):
            assert self._sales(mgr, crew["sales"])["periods"][period]["stats"]["total"] == 5

    def test_the_employee_detail_returns_the_blocks_that_apply(self, mgr, crew):
        sales = api("GET", f"/api/sales/performance/employees/{crew['sales']['id']}", mgr).json()
        assert sales["sales"] is not None and sales["data_entry"] is None and sales["user"]["id"] == crew["sales"]["id"]
        entry = api("GET", f"/api/sales/performance/employees/{crew['entry']['id']}", mgr).json()
        assert entry["data_entry"] is not None and entry["sales"] is None
        assert set(entry["period_starts"]) == {"today", "month", "year", "lifetime"} and entry["period_starts"]["lifetime"] is None
        assert entry["period_starts"]["year"] <= entry["period_starts"]["month"] <= entry["period_starts"]["today"]

    def test_the_history_is_persistent_and_per_day(self, mgr, crew):
        h = api("GET", f"/api/sales/performance/employees/{crew['sales']['id']}/history?days=7", mgr).json()
        assert 1 <= len(h["days"]) <= 2                                                        # (two only if the test ran across Baghdad midnight)
        total = lambda key: sum(d[key] for d in h["days"])
        assert (total("worked"), total("accepted_direct"), total("rejected_direct"), total("wait_listed"), total("wait_accepted"), total("wait_rejected")) == (5, 1, 1, 3, 1, 1)
        assert total("work_hours") > 0 and h["sessions"] and h["sessions"][0]["actions"] >= 1
        assert [d["date"] for d in h["days"]] == sorted((d["date"] for d in h["days"]), reverse=True)     # newest first
        assert all(s["started_at"] <= s["ended_at"] and s["minutes"] >= 1 for s in h["sessions"])
        e = api("GET", f"/api/sales/performance/employees/{crew['entry']['id']}/history?days=7", mgr).json()
        assert sum(d["leads_entered"] for d in e["days"]) == 4
        assert api("GET", f"/api/sales/performance/employees/{crew['sales']['id']}/history?days=0", mgr).status_code == 422
        assert api("GET", f"/api/sales/performance/employees/{crew['sales']['id']}/history?days=367", mgr).status_code == 422

    def test_history_survives_what_later_happens_to_the_leads(self, mgr, crew, p):
        """The numbers come from permanent attempt rows, not from the leads' current owner or stage: hand every lead the salesperson
        worked to somebody else, archive them - their record does not move."""
        before = self._sales(mgr, crew["sales"])["periods"]["lifetime"]["stats"]
        for row in api("GET", f"/api/sales/leads?assigned_to={crew['sales']['id']}&limit=100", mgr).json()["items"]:
            assert api("POST", f"/api/sales/leads/{row['id']}/assign", mgr, {"assigned_to": p["employee2"]["id"]}).status_code == 200
        after = self._sales(mgr, crew["sales"])["periods"]["lifetime"]["stats"]
        for key in ("total", "accepted_direct", "rejected_direct", "wait_listed", "wait_accepted", "wait_rejected"):
            assert after[key] == before[key], key

    def test_a_person_who_left_keeps_their_record(self, admin_h, mgr, crew):
        assert api("PATCH", f"/api/sales/team/{crew['sales']['id']}", admin_h, {"status": "inactive"}).status_code == 200
        item = self._sales(mgr, crew["sales"])
        assert item["status"] == "inactive" and item["periods"]["lifetime"]["stats"]["total"] == 5
        assert api("GET", f"/api/sales/performance/employees/{crew['sales']['id']}/history", mgr).status_code == 200

    def test_nothing_in_the_performance_payload_is_a_secret(self, mgr):
        for path in ("/api/sales/performance/data-entry", "/api/sales/performance/sales"):
            payload = api("GET", path, mgr).json()
            assert not {k for k in keys_of(payload) if "password" in k.lower() or "email" in k.lower() or "phone" in k.lower() or "token" in k.lower()}
