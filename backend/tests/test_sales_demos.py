"""JAZ Sales - Phase 3: demos (HTTP integration tier).

Schedule, reschedule, complete, cancel and mark as no-show; who can be given a demo; the views (Upcoming, Completed,
Cancelled / No-show) and their counts; and the timeline events every one of those actions leaves. There is no calendar
integration: this is Sales-side tracking. Who may do what is covered by test_sales_activities_rbac.py.
"""
import concurrent.futures
from datetime import timedelta

import pytest

from sales.timezone import app_today

from sales_activity_test_utils import (
    PHANTOM_ID,
    act,
    ago,
    api,
    create_demo,
    create_lead,
    create_staff,
    errors_of,
    fetch,
    get_lead,
    ids_of,
    later,
    listing,
    make_custom_role,
    make_personas,
    parse,
    patch,
    retire_roles,
    stage_of,
    timeline_of,
    uniq,
    utcnow,
    work_types,
    worked_lead,
)
from sales_lead_test_utils import assign
from sales_test_utils import admin_token, assert_scratch_target, auth, lookup_user, owner_token, owner_user  # noqa: F401

ENDINGS = [("complete", "completed", "demo_completed"), ("cancel", "cancelled", "demo_cancelled"), ("no-show", "no_show", "demo_no_show")]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "demo")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture(scope="module")
def emp(p):
    return p["employee"]["headers"]


@pytest.fixture()
def lead(p, mgr):
    return worked_lead(mgr, p["employee"]["id"])


# =============================================================================
class TestScheduleADemo:
    def test_defaults_and_shape(self, p, lead, emp):
        when = later(days=2)
        d = create_demo(emp, lead["id"], scheduled_at=when, notes="show the reports")
        assert set(d) == {"id", "lead", "assigned_to", "scheduled_at", "status", "notes", "completed_at", "is_past_due", "created_by", "created_at", "updated_at", "can"}
        assert d["status"] == "scheduled" and d["completed_at"] is None and d["is_past_due"] is False
        assert abs(parse(d["scheduled_at"]) - parse(when)) < timedelta(seconds=1) and d["notes"] == "show the reports"
        assert d["assigned_to"] == {"id": p["employee"]["id"], "name": "Test demo-employee", "status": "active"}      # nobody named = the caller
        assert d["created_by"]["id"] == p["employee"]["id"]
        assert d["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "assigned", "archived": False}
        assert d["can"] == {"update": True, "reschedule": True, "complete": True, "cancel": True, "no_show": True, "assign": False}

    def test_a_manager_can_give_it_to_the_lead_owner_an_employee_cannot_give_it_away(self, p, lead, emp, mgr):
        d = create_demo(mgr, lead["id"], assigned_to=p["employee"]["id"])
        assert d["assigned_to"]["id"] == p["employee"]["id"] and d["created_by"]["id"] == p["manager"]["id"] and d["can"]["assign"] is True
        assert create_demo(mgr, lead["id"])["assigned_to"]["id"] == p["manager"]["id"]
        r = api("POST", "/api/sales/demos", emp, {"lead_id": lead["id"], "scheduled_at": later(days=1), "assigned_to": p["manager"]["id"]})
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"

    def test_a_super_admin_must_name_who_gives_it(self, p, lead, admin_h):
        r = api("POST", "/api/sales/demos", admin_h, {"lead_id": lead["id"], "scheduled_at": later(days=1)})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_required"
        assert create_demo(admin_h, lead["id"], assigned_to=p["employee"]["id"])["assigned_to"]["id"] == p["employee"]["id"]

    def test_the_time_rules(self, lead, emp):
        past = create_demo(emp, lead["id"], scheduled_at=ago(days=1))                                              # logging one that already took place: fine
        assert past["is_past_due"] is True and past["status"] == "scheduled"
        for bad in ("2026-09-25T10:00:00", "1999-12-31T23:59:59Z", "2100-01-02T00:00:00Z", "next week", None):
            body = {"lead_id": lead["id"]} if bad is None else {"lead_id": lead["id"], "scheduled_at": bad}
            assert api("POST", "/api/sales/demos", emp, body).status_code == 422, bad

    def test_notes_unknown_fields_and_the_lead(self, p, mgr, lead, emp):
        assert create_demo(emp, lead["id"], notes="  bring the tablet  ")["notes"] == "bring the tablet"
        assert create_demo(emp, lead["id"], notes="  ")["notes"] is None
        assert api("POST", "/api/sales/demos", emp, {"lead_id": lead["id"], "scheduled_at": later(days=1), "notes": "x" * 2001}).status_code == 422
        for smuggled in ({"status": "completed"}, {"completed_at": later(days=1)}, {"created_by": PHANTOM_ID}):
            assert api("POST", "/api/sales/demos", emp, {"lead_id": lead["id"], "scheduled_at": later(days=1), **smuggled}).status_code == 422
        r = api("POST", "/api/sales/demos", emp, {"lead_id": PHANTOM_ID, "scheduled_at": later(days=1)})
        assert r.status_code == 404 and errors_of(r)["code"] == "lead_not_found"
        others = worked_lead(mgr, p["employee2"]["id"])
        assert api("POST", "/api/sales/demos", emp, {"lead_id": others["id"], "scheduled_at": later(days=1)}).status_code == 403
        mine = worked_lead(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{mine['id']}", mgr)
        r = api("POST", "/api/sales/demos", emp, {"lead_id": mine["id"], "scheduled_at": later(days=1)})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"

    def test_several_demos_on_one_lead_are_fine(self, lead, emp):
        first, second = create_demo(emp, lead["id"]), create_demo(emp, lead["id"], scheduled_at=later(days=9))
        assert first["id"] != second["id"] and listing(emp, f"/api/sales/demos?lead_id={lead['id']}")["total"] == 2


# =============================================================================
class TestWhoCanBeGivenADemo:
    def _refused(self, mgr, lead, who_id, code="activity_assignee_not_eligible"):
        count = lambda: listing(mgr, f"/api/sales/demos?lead_id={lead['id']}&assigned_to={who_id}")["total"]  # noqa: E731
        before = count()
        r = api("POST", "/api/sales/demos", mgr, {"lead_id": lead["id"], "scheduled_at": later(days=1), "assigned_to": who_id})
        assert r.status_code == 400, r.text
        assert errors_of(r)["field"] == "assigned_to" and errors_of(r)["code"] == code
        assert count() == before

    def test_deactivated_revoked_wrong_role_and_made_up_people_are_refused(self, admin_h, p, mgr, owner_user):
        lead = create_lead(mgr)
        gone = create_staff(admin_h, ["sales_employee"], "demo-gone", do_login=False)
        revoked = create_staff(admin_h, ["sales_employee"], "demo-revoked", do_login=False)
        for who in (gone, revoked):
            assign(mgr, lead["id"], who["id"])
            assert create_demo(mgr, lead["id"], assigned_to=who["id"])                                             # eligible while active
        assert api("PATCH", f"/api/sales/team/{gone['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert api("DELETE", f"/api/sales/team/{revoked['id']}/roles/sales_employee", admin_h).status_code == 200
        for who_id in (gone["id"], revoked["id"], p["data_entry"]["id"], p["onboarding"]["id"], p["no_roles"]["id"], lookup_user("admin@jaz.com")["id"], owner_user["id"], PHANTOM_ID):
            self._refused(mgr, lead, who_id)
        assert api("PATCH", f"/api/sales/team/{gone['id']}", admin_h, {"status": "active"}).status_code == 200      # ... and back again
        assign(mgr, lead["id"], gone["id"])
        assert create_demo(mgr, lead["id"], assigned_to=gone["id"])["assigned_to"]["id"] == gone["id"]

    def test_the_assignee_must_be_able_to_see_the_lead(self, p, mgr):
        lead = worked_lead(mgr, p["employee"]["id"])
        self._refused(mgr, lead, p["employee2"]["id"], code="activity_assignee_cannot_see_lead")
        assert create_demo(mgr, lead["id"], assigned_to=p["manager"]["id"])

    def test_eligibility_is_per_kind_of_work(self, admin_h, p, mgr):
        """Holding sales.followups.manage does not make somebody eligible for a demo, and vice versa."""
        only_followups = make_custom_role(["sales.access", "sales.followups.manage", "sales.leads.scope_all"])
        only_demos = make_custom_role(["sales.access", "sales.demos.manage", "sales.leads.scope_all"])
        try:
            fu_person = create_staff(admin_h, [only_followups], "demo-fu-only", do_login=False)
            demo_person = create_staff(admin_h, [only_demos], "demo-demo-only", do_login=False)
            lead = worked_lead(mgr, p["employee"]["id"])
            self._refused(mgr, lead, fu_person["id"])
            assert create_demo(mgr, lead["id"], assigned_to=demo_person["id"])["assigned_to"]["id"] == demo_person["id"]
            r = api("POST", "/api/sales/followups", mgr, {"lead_id": lead["id"], "due_at": later(days=1), "assigned_to": demo_person["id"]})
            assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_not_eligible"
            assert api("POST", "/api/sales/followups", mgr, {"lead_id": lead["id"], "due_at": later(days=1), "assigned_to": fu_person["id"]}).status_code == 201
        finally:
            retire_roles([only_followups, only_demos])


# =============================================================================
class TestEndingADemo:
    @pytest.mark.parametrize("action,status,event", ENDINGS)
    def test_complete_cancel_and_no_show(self, p, lead, emp, mgr, action, status, event):
        d = create_demo(emp, lead["id"], scheduled_at=ago(hours=1), notes="the demo")
        ended = act(emp, "demos", d["id"], action, {"note": f"note for {action}"})
        assert ended["status"] == status and ended["is_past_due"] is False                                        # no longer waiting for an outcome
        assert (ended["completed_at"] is not None) == (status == "completed")                                     # only a completed demo has a completion time
        if status == "completed":
            assert abs(parse(ended["completed_at"]) - utcnow()) < timedelta(minutes=2)
        assert ended["scheduled_at"] == d["scheduled_at"] and ended["notes"] == "the demo"
        assert ended["can"] == {"update": False, "reschedule": False, "complete": False, "cancel": False, "no_show": False, "assign": False}
        created, closing = timeline_of(mgr, lead["id"])
        assert closing["event_type"] == event and closing["actor"]["id"] == p["employee"]["id"]
        assert closing["before"]["status"] == "scheduled" and abs(parse(closing["before"]["scheduled_at"]) - parse(d["scheduled_at"])) < timedelta(seconds=1)
        assert closing["after"]["status"] == status and ("completed_at" in closing["after"]) == (status == "completed")
        assert closing["note"] == f"note for {action}" and closing["metadata"]["demo_id"] == d["id"]

    def test_the_note_is_optional_and_the_body_may_be_absent_or_empty(self, lead, emp, mgr):
        for action, *_ in ENDINGS:
            for body in (None, {}, {"note": "  "}):
                d = create_demo(emp, lead["id"])
                act(emp, "demos", d["id"], action, body)
                assert timeline_of(mgr, lead["id"])[-1]["note"] is None
        d = create_demo(emp, lead["id"])
        assert api("POST", f"/api/sales/demos/{d['id']}/complete", emp, {"note": "x" * 1001}).status_code == 422
        assert api("POST", f"/api/sales/demos/{d['id']}/complete", emp, {"status": "cancelled"}).status_code == 422
        assert fetch(emp, "demos", d["id"])["status"] == "scheduled"

    def test_a_demo_ends_only_once(self, lead, emp, mgr):
        for first, *_ in ENDINGS:
            for second, *_ in ENDINGS + [("reschedule", None, None)]:
                d = create_demo(emp, lead["id"])
                act(emp, "demos", d["id"], first)
                body = {"scheduled_at": later(days=9)} if second == "reschedule" else None
                r = api("POST", f"/api/sales/demos/{d['id']}/{second}", emp, body)
                assert r.status_code == 409 and errors_of(r)["code"] == "demo_not_scheduled", (first, second)
            r = api("PATCH", f"/api/sales/demos/{d['id']}", emp, {"notes": "too late"})
            assert r.status_code == 409 and errors_of(r)["code"] == "demo_not_scheduled"
        endings = [t for t in work_types(mgr, lead["id"]) if t in ("demo_completed", "demo_cancelled", "demo_no_show")]
        assert len(endings) == 12                                                                                   # 3 first actions x 4 second attempts = 12 demos, one ending event each: no duplicates

    def test_a_past_due_demo_still_needs_an_outcome_and_can_get_one(self, lead, emp):
        d = create_demo(emp, lead["id"], scheduled_at=ago(days=3))
        assert fetch(emp, "demos", d["id"])["is_past_due"] is True
        assert act(emp, "demos", d["id"], "no-show")["status"] == "no_show"

    def test_two_people_ending_the_same_demo_at_once_produce_exactly_one_ending(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"])
        calls = [("complete", emp), ("cancel", mgr), ("no-show", emp), ("complete", mgr), ("cancel", emp), ("no-show", mgr)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda c: api("POST", f"/api/sales/demos/{d['id']}/{c[0]}", c[1]), calls))
        assert sorted(r.status_code for r in results) == [200] + [409] * 5
        endings = [t for t in work_types(mgr, lead["id"]) if t in ("demo_completed", "demo_cancelled", "demo_no_show")]
        assert len(endings) == 1

    def test_unknown_and_malformed_ids(self, emp):
        for bad in (PHANTOM_ID, "not-a-uuid"):
            for action in ("complete", "cancel", "no-show"):
                assert api("POST", f"/api/sales/demos/{bad}/{action}", emp).status_code == 404
            assert api("POST", f"/api/sales/demos/{bad}/reschedule", emp, {"scheduled_at": later(days=1)}).status_code == 404
            assert api("PATCH", f"/api/sales/demos/{bad}", emp, {"notes": "x"}).status_code == 404
            assert api("GET", f"/api/sales/demos/{bad}", emp).status_code == 404


# =============================================================================
class TestRescheduleAndEdit:
    def test_reschedule_moves_only_the_time(self, p, lead, emp, mgr):
        d = create_demo(emp, lead["id"], scheduled_at=later(days=2), notes="keep me")
        new_time = later(days=5)
        moved = act(emp, "demos", d["id"], "reschedule", {"scheduled_at": new_time, "note": "the client asked for later"})
        assert abs(parse(moved["scheduled_at"]) - parse(new_time)) < timedelta(seconds=1)
        assert moved["status"] == "scheduled" and moved["notes"] == "keep me" and moved["assigned_to"] == d["assigned_to"]
        assert fetch(emp, "demos", d["id"]) == moved
        created, event = timeline_of(mgr, lead["id"])
        assert event["event_type"] == "demo_rescheduled" and event["actor"]["id"] == p["employee"]["id"]
        assert abs(parse(event["before"]["scheduled_at"]) - parse(d["scheduled_at"])) < timedelta(seconds=1)
        assert abs(parse(event["after"]["scheduled_at"]) - parse(new_time)) < timedelta(seconds=1)
        assert event["note"] == "the client asked for later" and event["metadata"]["demo_id"] == d["id"]

    def test_a_demo_can_be_rescheduled_many_times_each_is_an_event(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"])
        for days in (3, 4, 1):
            act(emp, "demos", d["id"], "reschedule", {"scheduled_at": later(days=days)})
        act(emp, "demos", d["id"], "complete")
        assert work_types(mgr, lead["id"]) == ["demo_scheduled", "demo_rescheduled", "demo_rescheduled", "demo_rescheduled", "demo_completed"]

    def test_rescheduling_to_the_same_time_is_a_conflict_and_records_nothing(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"])
        r = api("POST", f"/api/sales/demos/{d['id']}/reschedule", emp, {"scheduled_at": d["scheduled_at"]})
        assert r.status_code == 409 and errors_of(r)["code"] == "demo_same_time" and errors_of(r)["field"] == "scheduled_at"
        assert work_types(mgr, lead["id"]) == ["demo_scheduled"]

    def test_invalid_reschedules(self, lead, emp):
        d = create_demo(emp, lead["id"])
        for bad in ({}, {"scheduled_at": None}, {"scheduled_at": "2026-01-01T00:00:00"}, {"scheduled_at": "1999-01-01T00:00:00Z"},
                    {"scheduled_at": later(days=1), "note": "x" * 1001}, {"scheduled_at": later(days=1), "status": "completed"}):
            assert api("POST", f"/api/sales/demos/{d['id']}/reschedule", emp, bad).status_code == 422, bad
        assert api("POST", f"/api/sales/demos/{d['id']}/reschedule", emp).status_code == 422                         # the new time is required
        assert fetch(emp, "demos", d["id"]) == d

    def test_rescheduling_a_past_due_demo_brings_it_back(self, lead, emp):
        d = create_demo(emp, lead["id"], scheduled_at=ago(days=1))
        assert d["is_past_due"] is True
        assert act(emp, "demos", d["id"], "reschedule", {"scheduled_at": later(days=1)})["is_past_due"] is False

    def test_edit_notes_and_assignee(self, p, lead, emp, mgr):
        d = create_demo(emp, lead["id"], notes="first")
        assert patch(emp, "demos", d["id"], {"notes": "second"})["notes"] == "second"
        assert patch(emp, "demos", d["id"], {"notes": None})["notes"] is None
        assert api("PATCH", f"/api/sales/demos/{d['id']}", emp, {}).status_code == 400
        assert api("PATCH", f"/api/sales/demos/{d['id']}", emp, {"assigned_to": None}).status_code == 400
        assert api("PATCH", f"/api/sales/demos/{d['id']}", emp, {"assigned_to": p["manager"]["id"]}).status_code == 403     # an employee cannot hand it over
        moved = patch(mgr, "demos", d["id"], {"assigned_to": p["manager"]["id"], "notes": "for the manager"})
        assert moved["assigned_to"]["id"] == p["manager"]["id"]
        r = api("PATCH", f"/api/sales/demos/{d['id']}", mgr, {"assigned_to": p["data_entry"]["id"]})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_not_eligible"
        r = api("PATCH", f"/api/sales/demos/{d['id']}", mgr, {"assigned_to": p["employee2"]["id"]})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_cannot_see_lead"
        assert fetch(mgr, "demos", d["id"])["assigned_to"]["id"] == p["manager"]["id"]
        events = timeline_of(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["demo_scheduled", "demo_updated", "demo_updated", "demo_updated"]
        assert events[-1]["before"]["assigned_to"] == {"id": p["employee"]["id"], "name": "Test demo-employee"}
        assert events[-1]["after"]["assigned_to"] == {"id": p["manager"]["id"], "name": "Test demo-manager"}
        assert events[-1]["metadata"]["fields"] == ["assigned_to", "notes"]

    def test_the_time_cannot_be_changed_by_an_edit_only_by_rescheduling(self, lead, emp):
        d = create_demo(emp, lead["id"])
        for bad in ({"scheduled_at": later(days=3)}, {"status": "completed"}, {"lead_id": PHANTOM_ID}):
            assert api("PATCH", f"/api/sales/demos/{d['id']}", emp, bad).status_code == 422, bad
        assert fetch(emp, "demos", d["id"]) == d

    def test_saving_the_same_values_records_nothing(self, p, lead, emp, mgr):
        d = create_demo(emp, lead["id"], notes="same")
        assert patch(emp, "demos", d["id"], {"notes": "same", "assigned_to": p["employee"]["id"]}) == d
        assert work_types(mgr, lead["id"]) == ["demo_scheduled"]

    def test_an_archived_leads_demo_is_frozen(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        d = create_demo(emp, lead["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        for method, path, body in (("PATCH", "", {"notes": "x"}), ("POST", "/complete", None), ("POST", "/cancel", None),
                                   ("POST", "/no-show", None), ("POST", "/reschedule", {"scheduled_at": later(days=9)})):
            r = api(method, f"/api/sales/demos/{d['id']}{path}", emp, body)
            assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived", path
        assert fetch(emp, "demos", d["id"])["can"]["complete"] is False
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        act(emp, "demos", d["id"], "complete")

    def test_there_is_no_way_to_delete_a_demo(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"])
        assert api("DELETE", f"/api/sales/demos/{d['id']}", mgr).status_code == 405


# =============================================================================
@pytest.fixture(scope="module")
def world(p, mgr, emp):
    """One lead holding eight demos that between them exercise every view."""
    lead = worked_lead(mgr, p["employee"]["id"])
    items = {
        "past_due": create_demo(emp, lead["id"], scheduled_at=ago(days=1), notes="p"),
        "tomorrow": create_demo(emp, lead["id"], scheduled_at=later(days=1), notes="t"),
        "next_week": create_demo(mgr, lead["id"], scheduled_at=later(days=7), assigned_to=p["employee"]["id"], notes="n"),
        "managers_own": create_demo(mgr, lead["id"], scheduled_at=later(days=3), notes="m"),
    }
    items["done"] = act(emp, "demos", create_demo(emp, lead["id"], scheduled_at=ago(days=5))["id"], "complete")
    items["done2"] = act(emp, "demos", create_demo(emp, lead["id"], scheduled_at=ago(days=2))["id"], "complete")
    items["dropped"] = act(emp, "demos", create_demo(emp, lead["id"], scheduled_at=later(days=2))["id"], "cancel")
    items["absent"] = act(emp, "demos", create_demo(emp, lead["id"], scheduled_at=ago(days=4))["id"], "no-show")
    return {"lead": lead, "items": items, "base": f"/api/sales/demos?lead_id={lead['id']}&limit=100"}


class TestViews:
    def _ids(self, world, h, query=""):
        by_id = {v["id"]: k for k, v in world["items"].items()}
        return [by_id[i] for i in ids_of(listing(h, world["base"] + query))]

    def test_upcoming_is_every_demo_still_to_happen_or_to_be_closed_out_soonest_first(self, world, emp):
        # the past-due one is FIRST: it needs attention most, and it must not disappear from every tab
        assert self._ids(world, emp, "&status=scheduled") == ["past_due", "tomorrow", "managers_own", "next_week"]
        assert self._ids(world, emp, "&status=scheduled&order=desc") == ["next_week", "managers_own", "tomorrow", "past_due"]
        flags = {i["id"]: i["is_past_due"] for i in listing(emp, world["base"] + "&status=scheduled")["items"]}
        assert [flags[world["items"][k]["id"]] for k in ("past_due", "tomorrow", "managers_own", "next_week")] == [True, False, False, False]

    def test_completed(self, world, emp):
        assert self._ids(world, emp, "&status=completed") == ["done", "done2"]                                     # by scheduled time
        assert all(i["completed_at"] for i in listing(emp, world["base"] + "&status=completed")["items"])

    def test_cancelled_and_no_show(self, world, emp):
        assert self._ids(world, emp, "&status=cancelled") == ["dropped"]
        assert self._ids(world, emp, "&status=no_show") == ["absent"]
        assert set(self._ids(world, emp, "&status=cancelled,no_show")) == {"dropped", "absent"}
        assert all(i["is_past_due"] is False for i in listing(emp, world["base"] + "&status=cancelled,no_show")["items"])

    def test_my_demos(self, world, emp, mgr):
        assert self._ids(world, emp, "&assigned_to=me&status=scheduled") == ["past_due", "tomorrow", "next_week"]
        assert self._ids(world, mgr, "&assigned_to=me&status=scheduled") == ["managers_own"]

    def test_counts(self, world, emp, mgr):
        base = f"/api/sales/demos/counts?lead_id={world['lead']['id']}"
        assert listing(emp, base) == {"scheduled": 4, "upcoming": 3, "past_due": 1, "completed": 2, "cancelled": 1, "no_show": 1, "mine_scheduled": 3}
        assert listing(mgr, base) == {"scheduled": 4, "upcoming": 3, "past_due": 1, "completed": 2, "cancelled": 1, "no_show": 1, "mine_scheduled": 1}
        assert listing(emp, base + "&assigned_to=me")["scheduled"] == 3
        assert listing(emp, base + "&status=completed") == listing(emp, base)                                      # the status filter is ignored by the counts

    def test_date_range_and_search(self, p, mgr, emp):
        token = uniq(12)
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Demoed {token} Ltd")
        today = app_today()
        a, b, c = (create_demo(emp, lead["id"], scheduled_at=t) for t in (ago(days=10), later(days=2), later(days=20)))
        base = f"/api/sales/demos?q={token}&order=asc"
        assert ids_of(listing(emp, base)) == [a["id"], b["id"], c["id"]]
        assert ids_of(listing(emp, base + f"&scheduled_from={today}&scheduled_to={today + timedelta(days=5)}")) == [b["id"]]
        assert ids_of(listing(emp, base + f"&scheduled_to={today - timedelta(days=5)}")) == [a["id"]]
        assert listing(emp, f"/api/sales/demos?q={token}zz")["total"] == 0

    def test_pagination_and_bad_parameters(self, world, emp):
        first, second = (listing(emp, f"/api/sales/demos?lead_id={world['lead']['id']}&limit=5&offset={o}") for o in (0, 5))
        assert first["total"] == second["total"] == 8 and len(first["items"]) == 5 and len(second["items"]) == 3
        assert not set(ids_of(first)) & set(ids_of(second))
        for bad in ("limit=0", "limit=101", "offset=-1", "order=up", "status=held", "status=scheduled,held", "assigned_to=nobody", "scheduled_from=soon", "lead_id=nope"):
            assert api("GET", f"/api/sales/demos?{bad}", emp).status_code in (400, 422), bad

    def test_a_cross_lead_list_hides_archived_leads(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        d = create_demo(emp, lead["id"], notes=f"arch-{uniq()}")
        listed = lambda: ids_of(listing(emp, "/api/sales/demos?status=scheduled&assigned_to=me&limit=100"))  # noqa: E731
        assert d["id"] in listed()
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert d["id"] not in listed()
        assert ids_of(listing(emp, f"/api/sales/demos?lead_id={lead['id']}")) == [d["id"]]
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert d["id"] in listed()

    def test_a_deactivated_assignees_demo_stays_visible_and_flagged(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "demo-lapsed", do_login=False)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], worker["id"])
        d = create_demo(mgr, lead["id"], assigned_to=worker["id"])
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"}).status_code == 200
        shown = fetch(mgr, "demos", d["id"])
        assert shown["assigned_to"] == {"id": worker["id"], "name": "Test demo-lapsed", "status": "inactive"}


# =============================================================================
class TestDemoTimeline:
    def test_the_full_history_of_one_demo(self, p, lead, emp, mgr):
        when = later(days=2)
        d = create_demo(emp, lead["id"], scheduled_at=when, notes="the first look")
        act(emp, "demos", d["id"], "reschedule", {"scheduled_at": later(days=4)})
        patch(emp, "demos", d["id"], {"notes": "the second look"})
        act(emp, "demos", d["id"], "complete", {"note": "went well"})
        events = timeline_of(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["demo_scheduled", "demo_rescheduled", "demo_updated", "demo_completed"]
        scheduled = events[0]
        assert scheduled["actor"] == {"id": p["employee"]["id"], "name": "Test demo-employee"} and scheduled["before"] is None
        assert abs(parse(scheduled["after"]["scheduled_at"]) - parse(when)) < timedelta(seconds=1)
        assert scheduled["after"]["assigned_to"] == {"id": p["employee"]["id"], "name": "Test demo-employee"}
        assert scheduled["note"] == "the first look" and scheduled["metadata"]["demo_id"] == d["id"]
        assert scheduled["metadata"]["actor_roles"] == ["sales_employee"]
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)

    def test_long_notes_are_clipped_on_the_timeline_but_kept_whole_on_the_demo(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"], notes="n" * 1900)
        assert len(timeline_of(mgr, lead["id"])[-1]["note"]) == 1001
        assert fetch(emp, "demos", d["id"])["notes"] == "n" * 1900

    def test_no_demo_action_moves_the_lead(self, lead, emp, mgr):
        before = get_lead(mgr, lead["id"])
        d = create_demo(emp, lead["id"])
        act(emp, "demos", d["id"], "reschedule", {"scheduled_at": later(days=6)})
        patch(emp, "demos", d["id"], {"notes": "x"})
        act(emp, "demos", d["id"], "complete")
        for action in ("cancel", "no-show"):
            act(emp, "demos", create_demo(emp, lead["id"])["id"], action)
        assert get_lead(mgr, lead["id"]) == before
        assert stage_of(mgr, lead["id"]) == "assigned"

    def test_refused_actions_leave_no_event(self, lead, emp, mgr):
        d = create_demo(emp, lead["id"])
        api("POST", f"/api/sales/demos/{d['id']}/reschedule", emp, {"scheduled_at": d["scheduled_at"]})
        api("POST", f"/api/sales/demos/{d['id']}/complete", emp, {"note": "x" * 1001})
        api("PATCH", f"/api/sales/demos/{d['id']}", emp, {})
        assert work_types(mgr, lead["id"]) == ["demo_scheduled"]
