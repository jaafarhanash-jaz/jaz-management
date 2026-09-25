"""JAZ Sales - Phase 3: calls (HTTP integration tier).

Logging a call to a lead, reading and filtering calls, correcting them, and the timeline events they leave. A call is
a record of something that happened: it is never deleted, it is authored by the caller (never by a request field),
and logging or correcting one never moves the lead in the pipeline. Who may do what is covered by
test_sales_activities_rbac.py; this module uses the roles that are allowed and concentrates on the behavior.
"""
import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest

from sales.timezone import app_today

from sales_activity_test_utils import (
    PHANTOM_ID,
    act,
    activities,
    ago,
    api,
    create_call,
    create_lead,
    errors_of,
    fetch,
    get_lead,
    ids_of,
    later,
    listing,
    make_personas,
    parse,
    patch,
    stage_of,
    timeline_of,
    uniq,
    utcnow,
    work_types,
    worked_lead,
)
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401  (admin_token: mints instead of logging in)

RESULTS = ["answered", "no_answer", "busy", "wrong_number", "interested", "not_interested", "call_back"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "calls")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture(scope="module")
def emp(p):
    return p["employee"]["headers"]


@pytest.fixture()
def lead(p, mgr):
    """A fresh lead assigned to the employee (in the employee's scope, and the manager's)."""
    return worked_lead(mgr, p["employee"]["id"])


# =============================================================================
class TestLogACall:
    def test_defaults_and_shape(self, p, lead, emp):
        call = create_call(emp, lead["id"])
        assert set(call) == {"id", "lead", "employee", "called_at", "duration_seconds", "result", "notes", "created_at", "updated_at", "can"}
        assert call["result"] == "answered" and call["duration_seconds"] is None and call["notes"] is None
        assert call["employee"] == {"id": p["employee"]["id"], "name": "Test calls-employee", "status": "active"}      # who made it
        assert call["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "assigned", "archived": False}
        assert abs(utcnow() - parse(call["called_at"])) < timedelta(minutes=2)                                          # default: now
        assert call["can"] == {"update": True}

    @pytest.mark.parametrize("result", RESULTS)
    def test_every_result_is_accepted(self, lead, emp, result):
        assert create_call(emp, lead["id"], result=result)["result"] == result

    def test_an_unknown_or_missing_result_is_rejected(self, lead, emp):
        for body in ({"lead_id": lead["id"], "result": "great"}, {"lead_id": lead["id"]}, {"lead_id": lead["id"], "result": None}):
            assert api("POST", "/api/sales/calls", emp, body).status_code == 422

    def test_duration_bounds(self, lead, emp):
        assert create_call(emp, lead["id"], duration_seconds=0)["duration_seconds"] == 0              # zero seconds is a fact ...
        assert create_call(emp, lead["id"], duration_seconds=86400)["duration_seconds"] == 86400
        assert create_call(emp, lead["id"])["duration_seconds"] is None                                # ... unknown is another
        for bad in (-1, 86401, 1.5, "long", []):
            r = api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy", "duration_seconds": bad})
            assert r.status_code == 422, bad

    def test_the_time_of_the_call(self, lead, emp):
        past = create_call(emp, lead["id"], called_at=ago(days=30))                                    # logged after the fact: fine
        assert abs(parse(past["called_at"]) - (utcnow() - timedelta(days=30))) < timedelta(minutes=1)
        assert create_call(emp, lead["id"], called_at=later(minutes=1))                                # a slightly fast clock: tolerated
        r = api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy", "called_at": later(hours=2)})
        assert r.status_code == 400 and errors_of(r)["code"] == "called_at_in_future" and errors_of(r)["field"] == "called_at"
        for bad in ("2026-09-25T10:00:00", "1999-12-31T23:59:59Z", "2100-01-02T00:00:00Z", "yesterday", "2026-09-25"):
            r = api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy", "called_at": bad})
            assert r.status_code == 422, bad                                                               # naive / out of range / not a time

    def test_a_time_with_an_offset_is_the_same_instant(self, lead, emp):
        target = utcnow() - timedelta(hours=3)
        local = target.astimezone(timezone(timedelta(hours=3))).isoformat()
        assert abs(parse(create_call(emp, lead["id"], called_at=local)["called_at"]) - target) < timedelta(seconds=1)

    def test_notes_are_trimmed_and_blank_means_none(self, lead, emp):
        assert create_call(emp, lead["id"], notes="  spoke to the owner  ")["notes"] == "spoke to the owner"
        assert create_call(emp, lead["id"], notes="   ")["notes"] is None
        assert create_call(emp, lead["id"], notes="x" * 2000)["notes"] == "x" * 2000
        assert api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy", "notes": "x" * 2001}).status_code == 422
        assert api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy", "notes": "a\x00b"}).status_code == 422   # NUL cannot be stored

    def test_the_author_is_always_the_caller_never_a_request_field(self, p, lead, mgr, emp):
        assert create_call(mgr, lead["id"])["employee"]["id"] == p["manager"]["id"]
        for smuggled in ({"employee_id": p["employee"]["id"]}, {"status": "x"}, {"id": PHANTOM_ID}, {"created_by": PHANTOM_ID}, {"lead": {}}):
            r = api("POST", "/api/sales/calls", mgr, {"lead_id": lead["id"], "result": "busy", **smuggled})
            assert r.status_code == 422, smuggled                                                          # unknown fields are refused outright

    def test_the_lead_must_exist(self, emp):
        r = api("POST", "/api/sales/calls", emp, {"lead_id": PHANTOM_ID, "result": "busy"})
        assert r.status_code == 404 and errors_of(r)["code"] == "lead_not_found"
        assert api("POST", "/api/sales/calls", emp, {"lead_id": "not-a-uuid", "result": "busy"}).status_code == 422
        assert api("POST", "/api/sales/calls", emp, {"result": "busy"}).status_code == 422

    def test_a_lead_outside_the_callers_scope_is_refused_and_nothing_is_logged(self, p, mgr):
        others = worked_lead(mgr, p["employee2"]["id"])
        r = api("POST", "/api/sales/calls", p["employee"]["headers"], {"lead_id": others["id"], "result": "busy"})
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"
        assert listing(mgr, f"/api/sales/calls?lead_id={others['id']}")["total"] == 0
        assert work_types(mgr, others["id"]) == []

    def test_an_archived_lead_takes_no_calls(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200
        r = api("POST", "/api/sales/calls", emp, {"lead_id": lead["id"], "result": "busy"})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"
        assert api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr).status_code == 200
        assert create_call(emp, lead["id"])


# =============================================================================
class TestReadCalls:
    def test_get_one_call(self, lead, emp):
        call = create_call(emp, lead["id"], notes="hello")
        assert fetch(emp, "calls", call["id"]) == call

    def test_unknown_and_malformed_ids_are_404(self, emp):
        for bad in (PHANTOM_ID, "not-a-uuid", "12345"):
            assert api("GET", f"/api/sales/calls/{bad}", emp).status_code == 404, bad
            assert api("PATCH", f"/api/sales/calls/{bad}", emp, {"notes": "x"}).status_code == 404, bad

    def test_a_leads_calls_newest_first_with_pagination(self, lead, emp):
        times = [ago(days=5), ago(days=1), ago(days=3), ago(days=2), ago(days=4)]
        made = [create_call(emp, lead["id"], called_at=t, notes=f"n{i}")["id"] for i, t in enumerate(times)]
        page = listing(emp, f"/api/sales/calls?lead_id={lead['id']}")
        assert page["total"] == 5 and page["limit"] == 25 and page["offset"] == 0
        assert ids_of(page) == [made[1], made[3], made[2], made[4], made[0]]                      # by time of the call, newest first
        assert ids_of(listing(emp, f"/api/sales/calls?lead_id={lead['id']}&order=asc")) == list(reversed(ids_of(page)))
        first, second = (listing(emp, f"/api/sales/calls?lead_id={lead['id']}&limit=3&offset={o}") for o in (0, 3))
        assert first["total"] == second["total"] == 5 and len(first["items"]) == 3 and len(second["items"]) == 2
        assert ids_of(first) + ids_of(second) == ids_of(page)
        for bad in ("limit=0", "limit=101", "offset=-1", "order=sideways"):
            assert api("GET", f"/api/sales/calls?{bad}", emp).status_code == 422, bad

    def test_filters(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        mine = create_call(emp, lead["id"], result="answered", called_at=ago(days=10))
        theirs = create_call(mgr, lead["id"], result="no_answer", called_at=ago(days=2), notes="by the manager")
        third = create_call(emp, lead["id"], result="busy", called_at=ago(hours=1))
        base = f"/api/sales/calls?lead_id={lead['id']}"
        assert set(ids_of(listing(emp, base + "&employee_id=me"))) == {mine["id"], third["id"]}
        assert ids_of(listing(mgr, base + "&employee_id=me")) == [theirs["id"]]
        assert ids_of(listing(mgr, base + f"&employee_id={p['employee']['id']}&order=asc")) == [mine["id"], third["id"]]
        assert ids_of(listing(emp, base + "&result=no_answer")) == [theirs["id"]]
        assert set(ids_of(listing(emp, base + "&result=answered,busy"))) == {mine["id"], third["id"]}
        today = app_today()
        assert set(ids_of(listing(emp, base + f"&called_from={today - timedelta(days=3)}"))) == {theirs["id"], third["id"]}
        assert ids_of(listing(emp, base + f"&called_to={today - timedelta(days=5)}")) == [mine["id"]]
        assert ids_of(listing(emp, base + f"&called_from={today - timedelta(days=3)}&called_to={today - timedelta(days=3)}")) == []
        assert listing(emp, base + "&result=busy&employee_id=me")["total"] == 1
        for bad in ("result=great", "result=answered,great", "employee_id=nobody", "called_from=yesterday", "lead_id=nope"):
            assert api("GET", f"/api/sales/calls?{bad}", emp).status_code in (400, 422), bad

    def test_search_by_the_leads_name(self, p, mgr, emp):
        token = uniq(12)
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Findable {token} Traders", contact_name=f"Contact {token[::-1]}")
        call = create_call(emp, lead["id"])
        assert ids_of(listing(emp, f"/api/sales/calls?q={token}")) == [call["id"]]
        assert ids_of(listing(emp, f"/api/sales/calls?q={token[::-1]}")) == [call["id"]]               # the contact name too
        assert listing(emp, f"/api/sales/calls?q={token}%25")["total"] == 0                            # % and _ are literals, not wildcards
        assert listing(emp, "/api/sales/calls?q=%20%20")["total"] >= 1                                 # blank search = no search
        assert listing(emp, f"/api/sales/calls?q={uniq(12)}zz")["total"] == 0

    def test_result_counts(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        for result in ("answered", "answered", "busy", "call_back"):
            create_call(emp, lead["id"], result=result)
        counts = listing(emp, f"/api/sales/calls/result-counts?lead_id={lead['id']}")
        assert counts["total"] == 4 and set(counts["counts"]) == set(RESULTS)                          # every result is present, zeros included
        assert counts["counts"]["answered"] == 2 and counts["counts"]["busy"] == 1 and counts["counts"]["call_back"] == 1
        assert counts["counts"]["wrong_number"] == 0
        only_busy = listing(emp, f"/api/sales/calls/result-counts?lead_id={lead['id']}&result=busy")    # the result filter is ignored
        assert only_busy == counts
        assert listing(emp, f"/api/sales/calls/result-counts?lead_id={lead['id']}&employee_id={p['employee2']['id']}")["total"] == 0

    def test_a_cross_lead_list_hides_archived_leads_but_the_leads_own_list_does_not(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        call = create_call(emp, lead["id"], notes=f"arch-{uniq()}")
        assert call["id"] in ids_of(listing(emp, "/api/sales/calls?limit=100&employee_id=me"))
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert call["id"] not in ids_of(listing(emp, "/api/sales/calls?limit=100&employee_id=me"))
        assert ids_of(listing(emp, f"/api/sales/calls?lead_id={lead['id']}")) == [call["id"]]           # the archived lead's own history stays readable
        assert fetch(emp, "calls", call["id"])["can"] == {"update": False}                             # ...but frozen
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert call["id"] in ids_of(listing(emp, "/api/sales/calls?limit=100&employee_id=me"))


# =============================================================================
class TestCorrectACall:
    def test_change_every_correctable_field(self, lead, emp):
        call = create_call(emp, lead["id"], result="no_answer", duration_seconds=0, notes="first")
        when = ago(hours=3)
        updated = patch(emp, "calls", call["id"], {"result": "interested", "duration_seconds": 240, "notes": "second", "called_at": when})
        assert (updated["result"], updated["duration_seconds"], updated["notes"]) == ("interested", 240, "second")
        assert abs(parse(updated["called_at"]) - parse(when)) < timedelta(seconds=1)
        assert updated["id"] == call["id"] and updated["employee"] == call["employee"] and updated["lead"] == call["lead"]
        assert parse(updated["updated_at"]) >= parse(call["updated_at"])
        assert fetch(emp, "calls", call["id"]) == updated

    def test_optional_fields_can_be_cleared_required_ones_cannot(self, lead, emp):
        call = create_call(emp, lead["id"], duration_seconds=30, notes="something")
        cleared = patch(emp, "calls", call["id"], {"duration_seconds": None, "notes": None})
        assert cleared["duration_seconds"] is None and cleared["notes"] is None
        assert patch(emp, "calls", call["id"], {"notes": "   "})["notes"] is None                     # blank clears too
        for field in ("result", "called_at"):
            r = api("PATCH", f"/api/sales/calls/{call['id']}", emp, {field: None})
            assert r.status_code == 400 and errors_of(r)["field"] == field, field

    def test_an_empty_or_invalid_correction_is_refused(self, lead, emp):
        call = create_call(emp, lead["id"])
        assert api("PATCH", f"/api/sales/calls/{call['id']}", emp, {}).status_code == 400
        for bad in ({"result": "great"}, {"duration_seconds": -5}, {"called_at": "2026-01-01T00:00:00"}, {"notes": "x" * 2001}):
            assert api("PATCH", f"/api/sales/calls/{call['id']}", emp, bad).status_code == 422, bad
        r = api("PATCH", f"/api/sales/calls/{call['id']}", emp, {"called_at": later(days=1)})
        assert r.status_code == 400 and errors_of(r)["code"] == "called_at_in_future"
        assert fetch(emp, "calls", call["id"]) == call                                                 # nothing changed

    def test_the_lead_and_the_author_never_change(self, p, lead, emp):
        call = create_call(emp, lead["id"])
        for field, value in (("lead_id", PHANTOM_ID), ("employee_id", p["manager"]["id"]), ("id", PHANTOM_ID), ("created_at", later(days=1))):
            assert api("PATCH", f"/api/sales/calls/{call['id']}", emp, {field: value}).status_code == 422, field
        assert fetch(emp, "calls", call["id"]) == call

    def test_saving_the_same_values_writes_nothing_and_records_nothing(self, lead, emp, mgr):
        call = create_call(emp, lead["id"], result="busy", notes="same")
        before = work_types(mgr, lead["id"])
        again = patch(emp, "calls", call["id"], {"result": "busy", "notes": "same", "called_at": call["called_at"]})
        assert again == call                                                                            # not even updated_at moved
        assert work_types(mgr, lead["id"]) == before

    def test_an_archived_leads_call_is_frozen(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        call = create_call(emp, lead["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        r = api("PATCH", f"/api/sales/calls/{call['id']}", emp, {"notes": "too late"})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert patch(emp, "calls", call["id"], {"notes": "now fine"})["notes"] == "now fine"

    def test_there_is_no_way_to_delete_a_call(self, lead, emp, mgr):
        call = create_call(emp, lead["id"])
        for headers in (emp, mgr):
            assert api("DELETE", f"/api/sales/calls/{call['id']}", headers).status_code == 405
        assert fetch(emp, "calls", call["id"]) == call

    def test_concurrent_corrections_are_applied_one_after_another(self, lead, emp, mgr):
        """Each correction runs under a row lock, so the before/after values on the timeline chain together exactly."""
        call = create_call(emp, lead["id"], result="busy")
        targets = ["answered", "no_answer", "interested", "call_back", "not_interested", "wrong_number"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda r: api("PATCH", f"/api/sales/calls/{call['id']}", emp, {"result": r}), targets))
        assert all(r.status_code == 200 for r in responses)
        events = [e for e in timeline_of(mgr, lead["id"]) if e["event_type"] == "call_updated"]
        assert len(events) == 6
        chain = ["busy"] + [e["after"]["result"] for e in events]
        assert [e["before"]["result"] for e in events] == chain[:-1]                                   # every before is the previous after
        assert sorted(chain[1:]) == sorted(targets)
        assert fetch(emp, "calls", call["id"])["result"] == chain[-1]


# =============================================================================
class TestCallTimeline:
    def test_a_created_call_is_one_event_with_what_who_when(self, p, lead, emp, mgr):
        call = create_call(emp, lead["id"], result="interested", duration_seconds=125, notes="wants a demo", called_at=ago(minutes=20))
        (event,) = timeline_of(mgr, lead["id"])
        assert event["event_type"] == "call_created"
        assert event["actor"] == {"id": p["employee"]["id"], "name": "Test calls-employee"}                 # who
        assert abs(parse(event["occurred_at"]) - utcnow()) < timedelta(minutes=2)                            # when it was recorded
        assert event["before"] is None
        assert event["after"]["result"] == "interested" and event["after"]["duration_seconds"] == 125
        assert abs(parse(event["after"]["called_at"]) - parse(call["called_at"])) < timedelta(seconds=1)     # when the call happened
        assert event["after"]["employee"] == {"id": p["employee"]["id"], "name": "Test calls-employee"}
        assert event["note"] == "wants a demo"
        assert event["metadata"]["call_id"] == call["id"] and event["metadata"]["actor_roles"] == ["sales_employee"]

    def test_a_call_without_a_duration_records_none_rather_than_zero(self, lead, emp, mgr):
        create_call(emp, lead["id"])
        (event,) = timeline_of(mgr, lead["id"])
        assert "duration_seconds" not in event["after"] and event["note"] is None

    def test_a_correction_records_only_what_changed_before_and_after(self, lead, emp, mgr):
        call = create_call(emp, lead["id"], result="busy", duration_seconds=10, notes="before")
        patch(emp, "calls", call["id"], {"result": "answered", "notes": "after", "duration_seconds": 10})   # duration: unchanged
        created, updated = timeline_of(mgr, lead["id"])
        assert updated["event_type"] == "call_updated"
        assert updated["before"] == {"result": "busy", "notes": "before"}
        assert updated["after"] == {"result": "answered", "notes": "after"}
        assert updated["metadata"]["fields"] == ["notes", "result"] and updated["metadata"]["call_id"] == call["id"]
        patch(emp, "calls", call["id"], {"duration_seconds": None})
        assert timeline_of(mgr, lead["id"])[-1]["before"] == {"duration_seconds": 10}
        assert timeline_of(mgr, lead["id"])[-1]["after"] == {"duration_seconds": None}

    def test_long_notes_are_clipped_on_the_timeline_but_kept_whole_on_the_call(self, lead, emp, mgr):
        call = create_call(emp, lead["id"], notes="n" * 1900)
        (event,) = timeline_of(mgr, lead["id"])
        assert len(event["note"]) == 1001 and event["note"].endswith("…")
        assert fetch(emp, "calls", call["id"])["notes"] == "n" * 1900
        patch(emp, "calls", call["id"], {"notes": "m" * 1800})
        assert len(timeline_of(mgr, lead["id"])[-1]["after"]["notes"]) <= 301                              # payload values are clipped shorter still

    def test_events_are_ordered_and_never_contain_secrets(self, lead, emp, mgr):
        call = create_call(emp, lead["id"], notes="password: hunter2 (a note is free text and stays a note)")
        patch(emp, "calls", call["id"], {"result": "busy"})

        def keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield str(k).lower()
                    yield from keys(v)
            elif isinstance(obj, list):
                for item in obj:
                    yield from keys(item)

        events = timeline_of(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["call_created", "call_updated"]
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
        for event in events:
            assert not {"password", "token", "secret", "hash", "authorization"} & set(keys([event["before"], event["after"], event["metadata"]]))

    def test_logging_or_correcting_a_call_never_moves_the_lead(self, p, lead, emp, mgr):
        before = get_lead(mgr, lead["id"])
        for result in ("answered", "interested", "not_interested", "wrong_number", "call_back"):
            call = create_call(emp, lead["id"], result=result)
            patch(emp, "calls", call["id"], {"result": "busy"})
        after = get_lead(mgr, lead["id"])
        assert after == before                                                                          # stage, owner, lost reason, updated_at: untouched
        assert stage_of(mgr, lead["id"]) == "assigned"

    def test_a_managers_call_is_recorded_under_the_managers_authority(self, p, lead, mgr):
        create_call(mgr, lead["id"])
        (event,) = timeline_of(mgr, lead["id"])
        assert event["actor"]["id"] == p["manager"]["id"] and event["metadata"]["actor_roles"] == ["sales_manager"]

    def test_a_super_admin_can_log_a_call_too(self, admin_h, lead):
        call = create_call(admin_h, lead["id"])
        assert call["employee"]["name"] == "Super Admin"
        (event,) = timeline_of(admin_h, lead["id"])
        assert event["metadata"]["actor_roles"] == []
