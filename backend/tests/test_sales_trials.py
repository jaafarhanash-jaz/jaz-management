"""JAZ Sales - Phase 3: Sales-side trials (HTTP integration tier).

Start, edit, complete and cancel a trial; at most ONE active trial per lead (also under a race); the views (Active,
Ending soon, Completed, Cancelled) and their counts; and the timeline events. A Sales trial is only a record that
Sales offered a trial period: it is NOT the platform's subscription / trial system and touches none of it. Who may do
what is covered by test_sales_activities_rbac.py.
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
    create_lead,
    create_trial,
    errors_of,
    fetch,
    get_lead,
    ids_of,
    iso,
    later,
    listing,
    make_personas,
    parse,
    patch,
    run_db,
    stage_of,
    timeline_of,
    uniq,
    utcnow,
    work_types,
    worked_lead,
)
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "trial")


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
class TestStartATrial:
    def test_defaults_and_shape(self, p, lead, emp):
        end = later(days=14)
        t = create_trial(emp, lead["id"], expected_end_at=end, notes="14 days, all modules")
        assert set(t) == {"id", "lead", "started_at", "expected_end_at", "actual_end_at", "status", "notes", "is_overdue", "created_by", "created_at", "updated_at", "can"}
        assert t["status"] == "active" and t["actual_end_at"] is None and t["is_overdue"] is False and t["notes"] == "14 days, all modules"
        assert abs(parse(t["started_at"]) - utcnow()) < timedelta(minutes=2)                                        # default: now
        assert abs(parse(t["expected_end_at"]) - parse(end)) < timedelta(seconds=1)
        assert t["created_by"] == {"id": p["employee"]["id"], "name": "Test trial-employee", "status": None}
        assert t["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "assigned", "archived": False}
        assert t["can"] == {"update": True, "complete": True, "cancel": True}

    def test_a_manager_and_a_super_admin_can_start_one_too(self, p, lead, mgr, admin_h, emp):
        assert create_trial(mgr, lead["id"])["created_by"]["id"] == p["manager"]["id"]
        other = worked_lead(mgr, p["employee"]["id"])
        assert create_trial(admin_h, other["id"])["created_by"]["name"] == "Super Admin"

    def test_the_time_rules(self, p, mgr, lead, emp):
        started = ago(days=3)
        t = create_trial(emp, lead["id"], started_at=started, expected_end_at=later(days=11))                       # a trial that began earlier: fine
        assert abs(parse(t["started_at"]) - parse(started)) < timedelta(seconds=1)
        other = create_trial(emp, worked_lead(mgr, p["employee"]["id"])["id"], started_at=ago(days=20), expected_end_at=ago(days=6))
        assert other["status"] == "active" and other["is_overdue"] is True                                          # already past its expected end: active but overdue
        for body in (
            {"expected_end_at": None},
            {"expected_end_at": "2026-09-25T10:00:00"}, {"expected_end_at": "1999-12-31T23:59:59Z"}, {"expected_end_at": "2100-01-02T00:00:00Z"},
            {"expected_end_at": later(days=5), "started_at": "2026-09-25T10:00:00"}, {"expected_end_at": later(days=5), "started_at": "1999-01-01T00:00:00Z"},
        ):
            r = api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], **{k: v for k, v in body.items() if v is not None}})
            assert r.status_code == 422, body

    def test_the_end_must_come_after_the_start(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        for expected in (ago(days=1), iso(utcnow())):
            r = api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], "expected_end_at": expected})
            assert r.status_code == 400 and errors_of(r)["code"] == "expected_end_before_start" and errors_of(r)["field"] == "expected_end_at"
        start = ago(hours=1)
        r = api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], "started_at": start, "expected_end_at": start})
        assert r.status_code == 400 and errors_of(r)["code"] == "expected_end_before_start"
        assert listing(mgr, f"/api/sales/trials?lead_id={lead['id']}")["total"] == 0

    def test_a_trial_cannot_start_in_the_future_beyond_clock_skew(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        r = api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], "started_at": later(hours=2), "expected_end_at": later(days=9)})
        assert r.status_code == 400 and errors_of(r)["code"] == "started_at_in_future"
        assert create_trial(emp, lead["id"], started_at=later(minutes=1), expected_end_at=later(days=9))["status"] == "active"   # a fast clock: tolerated

    def test_notes_unknown_fields_and_the_lead(self, p, mgr, lead, emp):
        assert create_trial(emp, worked_lead(mgr, p["employee"]["id"])["id"], notes="  padded  ")["notes"] == "padded"
        assert create_trial(emp, worked_lead(mgr, p["employee"]["id"])["id"], notes="   ")["notes"] is None
        assert api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], "expected_end_at": later(days=5), "notes": "x" * 2001}).status_code == 422
        for smuggled in ({"status": "completed"}, {"actual_end_at": later(days=1)}, {"created_by": PHANTOM_ID}, {"id": PHANTOM_ID}):
            assert api("POST", "/api/sales/trials", emp, {"lead_id": lead["id"], "expected_end_at": later(days=5), **smuggled}).status_code == 422
        r = api("POST", "/api/sales/trials", emp, {"lead_id": PHANTOM_ID, "expected_end_at": later(days=5)})
        assert r.status_code == 404 and errors_of(r)["code"] == "lead_not_found"
        others = worked_lead(mgr, p["employee2"]["id"])
        assert api("POST", "/api/sales/trials", emp, {"lead_id": others["id"], "expected_end_at": later(days=5)}).status_code == 403
        mine = worked_lead(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{mine['id']}", mgr)
        r = api("POST", "/api/sales/trials", emp, {"lead_id": mine["id"], "expected_end_at": later(days=5)})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"


# =============================================================================
class TestOneActiveTrialPerLead:
    def test_a_second_active_trial_is_a_conflict_and_changes_nothing(self, lead, emp, mgr):
        first = create_trial(emp, lead["id"])
        for headers in (emp, mgr):
            r = api("POST", "/api/sales/trials", headers, {"lead_id": lead["id"], "expected_end_at": later(days=30)})
            assert r.status_code == 409 and errors_of(r)["code"] == "active_trial_exists" and errors_of(r)["field"] == "lead"
        assert listing(emp, f"/api/sales/trials?lead_id={lead['id']}")["total"] == 1
        assert fetch(emp, "trials", first["id"]) == first
        assert work_types(mgr, lead["id"]) == ["trial_started"]                                                     # the refusals left no event

    def test_after_the_trial_ends_another_can_start_completed_or_cancelled(self, lead, emp, mgr):
        first = create_trial(emp, lead["id"])
        act(emp, "trials", first["id"], "complete")
        second = create_trial(emp, lead["id"])
        act(emp, "trials", second["id"], "cancel")
        third = create_trial(emp, lead["id"])
        assert third["status"] == "active"
        assert listing(emp, f"/api/sales/trials?lead_id={lead['id']}")["total"] == 3
        assert listing(emp, f"/api/sales/trials?lead_id={lead['id']}&status=active")["total"] == 1                  # still only one active at any time

    def test_trials_of_different_leads_do_not_interfere(self, p, mgr, emp):
        leads = [worked_lead(mgr, p["employee"]["id"]) for _ in range(3)]
        assert all(create_trial(emp, l["id"])["status"] == "active" for l in leads)

    def test_racing_starts_produce_exactly_one_active_trial(self, lead, emp, mgr):
        """Two (or six) requests can pass the pre-check together; the partial unique index lets exactly one win and the
        losers get the same clean 409 - never a 500, never two active trials."""
        body = {"lead_id": lead["id"], "expected_end_at": later(days=14)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda h: api("POST", "/api/sales/trials", h, body), [emp, mgr] * 3))
        assert sorted(r.status_code for r in results) == [201] + [409] * 5, [r.text[:120] for r in results]
        assert {errors_of(r)["code"] for r in results if r.status_code == 409} == {"active_trial_exists"}
        assert listing(emp, f"/api/sales/trials?lead_id={lead['id']}&status=active")["total"] == 1
        assert work_types(mgr, lead["id"]) == ["trial_started"]

    def test_the_database_itself_refuses_a_second_active_trial(self, p, mgr):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        lead = worked_lead(mgr, p["employee"]["id"])
        create_trial(mgr, lead["id"])

        async def _second(db):
            with pytest.raises(IntegrityError) as exc:
                async with db.begin_nested():
                    await db.execute(text(
                        "INSERT INTO sales_trials (id, lead_id, expected_end_at, created_by) "
                        "VALUES (gen_random_uuid(), :lead, now() + interval '5 days', :user)"
                    ), {"lead": lead["id"], "user": p["manager"]["id"]})
            assert "uq_sales_trials_one_active" in str(exc.value)

        run_db(_second)


# =============================================================================
class TestEndATrial:
    def test_complete(self, p, lead, emp, mgr):
        t = create_trial(emp, lead["id"], notes="the trial")
        done = act(emp, "trials", t["id"], "complete", {"note": "they liked it"})
        assert done["status"] == "completed" and done["is_overdue"] is False
        assert abs(parse(done["actual_end_at"]) - utcnow()) < timedelta(minutes=2)                                  # default: now
        assert done["started_at"] == t["started_at"] and done["expected_end_at"] == t["expected_end_at"] and done["notes"] == "the trial"
        assert done["can"] == {"update": False, "complete": False, "cancel": False}
        started, ended = timeline_of(mgr, lead["id"])
        assert ended["event_type"] == "trial_completed" and ended["actor"]["id"] == p["employee"]["id"]
        assert ended["before"]["status"] == "active" and abs(parse(ended["before"]["expected_end_at"]) - parse(t["expected_end_at"])) < timedelta(seconds=1)
        assert ended["after"]["status"] == "completed" and abs(parse(ended["after"]["actual_end_at"]) - parse(done["actual_end_at"])) < timedelta(seconds=1)
        assert ended["note"] == "they liked it" and ended["metadata"]["trial_id"] == t["id"]

    def test_complete_with_the_real_end_time(self, lead, emp):
        t = create_trial(emp, lead["id"], started_at=ago(days=10), expected_end_at=later(days=4))
        when = ago(days=2)
        done = act(emp, "trials", t["id"], "complete", {"actual_end_at": when})
        assert abs(parse(done["actual_end_at"]) - parse(when)) < timedelta(seconds=1)

    def test_the_real_end_cannot_precede_the_start_or_lie_in_the_future(self, lead, emp):
        t = create_trial(emp, lead["id"], started_at=ago(days=5), expected_end_at=later(days=9))
        r = api("POST", f"/api/sales/trials/{t['id']}/complete", emp, {"actual_end_at": ago(days=6)})
        assert r.status_code == 400 and errors_of(r)["code"] == "actual_end_before_start" and errors_of(r)["field"] == "actual_end_at"
        r = api("POST", f"/api/sales/trials/{t['id']}/complete", emp, {"actual_end_at": later(hours=3)})
        assert r.status_code == 400 and errors_of(r)["code"] == "actual_end_at_in_future"
        for bad in ("2026-09-25T10:00:00", "1999-01-01T00:00:00Z", "soon"):
            assert api("POST", f"/api/sales/trials/{t['id']}/complete", emp, {"actual_end_at": bad}).status_code == 422
        assert fetch(emp, "trials", t["id"])["status"] == "active"                                                  # none of that ended it

    def test_cancel_records_when_it_ended_early(self, p, lead, emp, mgr):
        t = create_trial(emp, lead["id"])
        cancelled = act(emp, "trials", t["id"], "cancel", {"note": "they changed their mind"})
        assert cancelled["status"] == "cancelled" and abs(parse(cancelled["actual_end_at"]) - utcnow()) < timedelta(minutes=2)
        started, ended = timeline_of(mgr, lead["id"])
        assert ended["event_type"] == "trial_cancelled" and ended["actor"]["id"] == p["employee"]["id"]
        assert ended["before"]["status"] == "active" and ended["after"]["status"] == "cancelled" and "actual_end_at" in ended["after"]
        assert ended["note"] == "they changed their mind" and ended["metadata"]["trial_id"] == t["id"]

    def test_ending_a_trial_that_began_a_few_minutes_ahead_of_a_fast_clock_never_ends_before_it_started(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        t = create_trial(emp, lead["id"], started_at=later(minutes=4), expected_end_at=later(days=9))
        for action in ("cancel", "complete"):
            lead2 = worked_lead(mgr, p["employee"]["id"])
            t2 = create_trial(emp, lead2["id"], started_at=later(minutes=4), expected_end_at=later(days=9))
            ended = act(emp, "trials", t2["id"], action)
            assert parse(ended["actual_end_at"]) >= parse(ended["started_at"])                                      # the CHECK would have failed -> 500
        assert act(emp, "trials", t["id"], "cancel")["status"] == "cancelled"

    def test_the_note_is_optional_and_the_body_may_be_absent_or_empty(self, p, mgr, emp):
        for action in ("complete", "cancel"):
            for body in (None, {}, {"note": "  "}):
                lead = worked_lead(mgr, p["employee"]["id"])
                t = create_trial(emp, lead["id"])
                act(emp, "trials", t["id"], action, body)
                assert timeline_of(mgr, lead["id"])[-1]["note"] is None
        lead = worked_lead(mgr, p["employee"]["id"])
        t = create_trial(emp, lead["id"])
        for bad in ({"note": "x" * 1001}, {"status": "active"}):
            assert api("POST", f"/api/sales/trials/{t['id']}/cancel", emp, bad).status_code == 422
            assert api("POST", f"/api/sales/trials/{t['id']}/complete", emp, bad).status_code == 422
        assert fetch(emp, "trials", t["id"])["status"] == "active"

    def test_a_trial_ends_only_once(self, p, mgr, emp):
        for first in ("complete", "cancel"):
            for second in ("complete", "cancel"):
                lead = worked_lead(mgr, p["employee"]["id"])
                t = create_trial(emp, lead["id"])
                act(emp, "trials", t["id"], first)
                r = api("POST", f"/api/sales/trials/{t['id']}/{second}", emp)
                assert r.status_code == 409 and errors_of(r)["code"] == "trial_not_active", (first, second)
                r = api("PATCH", f"/api/sales/trials/{t['id']}", emp, {"notes": "too late"})
                assert r.status_code == 409 and errors_of(r)["code"] == "trial_not_active"
                assert len([w for w in work_types(mgr, lead["id"]) if w in ("trial_completed", "trial_cancelled")]) == 1

    def test_two_people_ending_the_same_trial_at_once_produce_exactly_one_ending(self, lead, emp, mgr):
        t = create_trial(emp, lead["id"])
        calls = [("complete", emp), ("cancel", mgr), ("cancel", emp), ("complete", mgr)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda c: api("POST", f"/api/sales/trials/{t['id']}/{c[0]}", c[1]), calls))
        assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
        assert len([w for w in work_types(mgr, lead["id"]) if w in ("trial_completed", "trial_cancelled")]) == 1

    def test_unknown_and_malformed_ids(self, emp):
        for bad in (PHANTOM_ID, "not-a-uuid"):
            for action in ("complete", "cancel"):
                assert api("POST", f"/api/sales/trials/{bad}/{action}", emp).status_code == 404
            assert api("PATCH", f"/api/sales/trials/{bad}", emp, {"notes": "x"}).status_code == 404
            assert api("GET", f"/api/sales/trials/{bad}", emp).status_code == 404

    def test_an_archived_leads_trial_is_frozen(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        t = create_trial(emp, lead["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        for method, path, body in (("PATCH", "", {"notes": "x"}), ("POST", "/complete", None), ("POST", "/cancel", None)):
            r = api(method, f"/api/sales/trials/{t['id']}{path}", emp, body)
            assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived", path
        assert fetch(emp, "trials", t["id"])["can"] == {"update": False, "complete": False, "cancel": False}
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        act(emp, "trials", t["id"], "complete")

    def test_there_is_no_way_to_delete_a_trial(self, lead, emp, mgr):
        t = create_trial(emp, lead["id"])
        assert api("DELETE", f"/api/sales/trials/{t['id']}", mgr).status_code == 405


# =============================================================================
class TestEditATrial:
    def test_change_the_expected_end_and_notes(self, p, lead, emp, mgr):
        t = create_trial(emp, lead["id"], notes="first")
        new_end = later(days=30)
        updated = patch(emp, "trials", t["id"], {"expected_end_at": new_end, "notes": "second"})
        assert abs(parse(updated["expected_end_at"]) - parse(new_end)) < timedelta(seconds=1) and updated["notes"] == "second"
        assert updated["started_at"] == t["started_at"] and updated["status"] == "active"
        assert fetch(emp, "trials", t["id"]) == updated
        assert patch(emp, "trials", t["id"], {"notes": None})["notes"] is None
        started, first_edit, second_edit = timeline_of(mgr, lead["id"])
        assert first_edit["event_type"] == "trial_updated" and first_edit["metadata"]["fields"] == ["expected_end_at", "notes"]
        assert abs(parse(first_edit["before"]["expected_end_at"]) - parse(t["expected_end_at"])) < timedelta(seconds=1)
        assert abs(parse(first_edit["after"]["expected_end_at"]) - parse(new_end)) < timedelta(seconds=1)
        assert first_edit["before"]["notes"] == "first" and first_edit["after"]["notes"] == "second"
        assert second_edit["before"] == {"notes": "second"} and second_edit["after"] == {"notes": None}

    def test_invalid_edits_are_refused_and_change_nothing(self, lead, emp):
        t = create_trial(emp, lead["id"], started_at=ago(days=2), expected_end_at=later(days=12))
        assert api("PATCH", f"/api/sales/trials/{t['id']}", emp, {}).status_code == 400
        r = api("PATCH", f"/api/sales/trials/{t['id']}", emp, {"expected_end_at": None})
        assert r.status_code == 400 and errors_of(r)["field"] == "expected_end_at"
        r = api("PATCH", f"/api/sales/trials/{t['id']}", emp, {"expected_end_at": ago(days=3)})                   # before the start
        assert r.status_code == 400 and errors_of(r)["code"] == "expected_end_before_start"
        for bad in ({"expected_end_at": "2026-01-01T00:00:00"}, {"expected_end_at": "1999-01-01T00:00:00Z"}, {"notes": "x" * 2001},
                    {"status": "completed"}, {"started_at": ago(days=9)}, {"actual_end_at": later(days=1)}, {"lead_id": PHANTOM_ID}):
            assert api("PATCH", f"/api/sales/trials/{t['id']}", emp, bad).status_code == 422, bad
        assert fetch(emp, "trials", t["id"]) == t

    def test_an_expected_end_in_the_past_is_fine_it_just_makes_the_trial_overdue(self, lead, emp):
        t = create_trial(emp, lead["id"], started_at=ago(days=10), expected_end_at=later(days=4))
        assert patch(emp, "trials", t["id"], {"expected_end_at": ago(days=1)})["is_overdue"] is True

    def test_saving_the_same_values_records_nothing(self, lead, emp, mgr):
        t = create_trial(emp, lead["id"], notes="same")
        assert patch(emp, "trials", t["id"], {"notes": "same", "expected_end_at": t["expected_end_at"]}) == t
        assert work_types(mgr, lead["id"]) == ["trial_started"]

    def test_refused_actions_leave_no_event(self, lead, emp, mgr):
        t = create_trial(emp, lead["id"])
        api("PATCH", f"/api/sales/trials/{t['id']}", emp, {})
        api("POST", f"/api/sales/trials/{t['id']}/complete", emp, {"actual_end_at": later(hours=5)})
        api("POST", f"/api/sales/trials/{t['id']}/cancel", emp, {"note": "x" * 1001})
        assert work_types(mgr, lead["id"]) == ["trial_started"]


# =============================================================================
@pytest.fixture(scope="module")
def world(p, mgr, emp):
    """Five leads sharing a search token, one trial each, that between them exercise every view."""
    token = uniq(12)
    leads = {name: worked_lead(mgr, p["employee"]["id"], business_name=f"Trialled {token} {name}") for name in ("soon", "later", "overdue", "done", "dropped")}
    items = {
        "soon": create_trial(emp, leads["soon"]["id"], expected_end_at=later(hours=20)),
        "later": create_trial(emp, leads["later"]["id"], expected_end_at=later(days=10)),
        "overdue": create_trial(emp, leads["overdue"]["id"], started_at=ago(days=20), expected_end_at=ago(days=2)),
    }
    items["done"] = act(emp, "trials", create_trial(emp, leads["done"]["id"], started_at=ago(days=15), expected_end_at=ago(days=1))["id"], "complete")
    items["dropped"] = act(emp, "trials", create_trial(emp, leads["dropped"]["id"])["id"], "cancel")
    return {"token": token, "leads": leads, "items": items, "base": f"/api/sales/trials?q={token}&limit=100"}


class TestViews:
    def _ids(self, world, h, query=""):
        by_id = {v["id"]: k for k, v in world["items"].items()}
        return [by_id[i] for i in ids_of(listing(h, world["base"] + query))]

    def test_active_is_soonest_expected_end_first_overdue_ones_first_of_all(self, world, emp):
        assert self._ids(world, emp, "&status=active") == ["overdue", "soon", "later"]
        assert self._ids(world, emp, "&status=active&order=desc") == ["later", "soon", "overdue"]
        flags = {i["id"]: i["is_overdue"] for i in listing(emp, world["base"] + "&status=active")["items"]}
        assert [flags[world["items"][k]["id"]] for k in ("overdue", "soon", "later")] == [True, False, False]

    def test_ending_soon_means_within_the_window_including_already_past(self, world, emp):
        assert self._ids(world, emp, "&ending_within_days=3") == ["overdue", "soon"]
        assert self._ids(world, emp, "&ending_within_days=1") == ["overdue", "soon"]                                # 20 hours away: inside one day
        assert self._ids(world, emp, "&ending_within_days=30") == ["overdue", "soon", "later"]
        assert "done" not in self._ids(world, emp, "&ending_within_days=30")                                        # only ACTIVE trials can be ending soon

    def test_completed_and_cancelled(self, world, emp):
        assert self._ids(world, emp, "&status=completed&sort=actual_end_at&order=desc") == ["done"]
        assert self._ids(world, emp, "&status=cancelled") == ["dropped"]
        assert set(self._ids(world, emp, "&status=completed,cancelled")) == {"done", "dropped"}
        assert all(i["is_overdue"] is False for i in listing(emp, world["base"] + "&status=completed,cancelled")["items"])

    def test_counts(self, world, emp, mgr):
        base = f"/api/sales/trials/counts?q={world['token']}"
        expected = {"active": 3, "ending_soon": 2, "overdue": 1, "completed": 1, "cancelled": 1}
        assert listing(emp, base) == expected and listing(mgr, base) == expected
        assert listing(emp, base + "&ending_soon_days=30")["ending_soon"] == 3                                     # the window is a parameter
        assert listing(emp, base + "&ending_soon_days=1")["ending_soon"] == 2
        assert listing(emp, base + "&status=completed&ending_within_days=1") == expected                           # list filters are ignored by the counts
        for bad in ("ending_soon_days=0", "ending_soon_days=31"):
            assert api("GET", f"/api/sales/trials/counts?{bad}", emp).status_code == 422

    def test_sorting_dates_search_and_pagination(self, world, emp):
        today = app_today()
        base = world["base"]
        assert self._ids(world, emp, "&sort=started_at&order=asc")[:2] == ["overdue", "done"]                      # started 20 and 15 days ago
        assert self._ids(world, emp, f"&ending_from={today + timedelta(days=5)}") == ["later", "dropped"]         # (the cancelled one was due in 14 days)
        assert set(self._ids(world, emp, f"&ending_to={today - timedelta(days=1)}")) == {"overdue", "done"}
        assert listing(emp, f"/api/sales/trials?q={world['token']}zz")["total"] == 0
        first, second = (listing(emp, f"/api/sales/trials?q={world['token']}&limit=3&offset={o}") for o in (0, 3))
        assert first["total"] == second["total"] == 5 and len(first["items"]) == 3 and len(second["items"]) == 2
        assert not set(ids_of(first)) & set(ids_of(second))
        for bad in ("limit=0", "limit=101", "offset=-1", "order=up", "sort=notes", "status=paused", "status=active,paused", "ending_within_days=0",
                    "ending_within_days=31", "ending_from=soon", "lead_id=nope"):
            assert api("GET", f"/api/sales/trials?{bad}", emp).status_code in (400, 422), bad

    def test_a_cross_lead_list_hides_archived_leads(self, world, mgr, emp):
        lead = world["leads"]["later"]
        assert world["items"]["later"]["id"] in ids_of(listing(emp, world["base"] + "&status=active"))
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert world["items"]["later"]["id"] not in ids_of(listing(emp, world["base"] + "&status=active"))
        assert ids_of(listing(emp, f"/api/sales/trials?lead_id={lead['id']}")) == [world["items"]["later"]["id"]]
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert world["items"]["later"]["id"] in ids_of(listing(emp, world["base"] + "&status=active"))

    def test_a_trial_becomes_overdue_when_its_expected_end_passes(self, p, mgr, emp):
        import time
        lead = worked_lead(mgr, p["employee"]["id"])
        t = create_trial(emp, lead["id"], started_at=ago(days=1), expected_end_at=later(seconds=2))
        assert t["is_overdue"] is False and listing(emp, f"/api/sales/trials/counts?lead_id={lead['id']}")["overdue"] == 0
        time.sleep(3)
        assert fetch(emp, "trials", t["id"])["is_overdue"] is True
        assert listing(emp, f"/api/sales/trials/counts?lead_id={lead['id']}") == {"active": 1, "ending_soon": 1, "overdue": 1, "completed": 0, "cancelled": 0}


# =============================================================================
class TestTrialTimeline:
    def test_the_full_history_of_one_trial(self, p, lead, emp, mgr):
        end = later(days=14)
        t = create_trial(emp, lead["id"], expected_end_at=end, notes="all modules")
        patch(emp, "trials", t["id"], {"expected_end_at": later(days=21)})
        act(emp, "trials", t["id"], "complete", {"note": "converted"})
        events = timeline_of(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["trial_started", "trial_updated", "trial_completed"]
        started = events[0]
        assert started["actor"] == {"id": p["employee"]["id"], "name": "Test trial-employee"} and started["before"] is None
        assert abs(parse(started["after"]["started_at"]) - parse(t["started_at"])) < timedelta(seconds=1)
        assert abs(parse(started["after"]["expected_end_at"]) - parse(end)) < timedelta(seconds=1)
        assert started["note"] == "all modules" and started["metadata"]["trial_id"] == t["id"] and started["metadata"]["actor_roles"] == ["sales_employee"]
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)

    def test_long_notes_are_clipped_on_the_timeline_but_kept_whole_on_the_trial(self, lead, emp, mgr):
        t = create_trial(emp, lead["id"], notes="n" * 1900)
        assert len(timeline_of(mgr, lead["id"])[-1]["note"]) == 1001
        assert fetch(emp, "trials", t["id"])["notes"] == "n" * 1900

    def test_no_trial_action_moves_the_lead(self, lead, emp, mgr):
        before = get_lead(mgr, lead["id"])
        t = create_trial(emp, lead["id"])
        patch(emp, "trials", t["id"], {"notes": "x"})
        act(emp, "trials", t["id"], "complete")
        act(emp, "trials", create_trial(emp, lead["id"])["id"], "cancel")
        assert get_lead(mgr, lead["id"]) == before
        assert stage_of(mgr, lead["id"]) == "assigned"


# =============================================================================
class TestItIsNotThePlatformTrial:
    """A Sales trial is a record on a lead. It never creates, extends or ends a company's subscription or access."""

    def test_no_company_or_subscription_data_changes(self, p, mgr, emp, admin_h):
        from sqlalchemy import text

        async def _snapshot(db):
            companies = (await db.execute(text(
                "SELECT count(*), md5(coalesce(string_agg(concat_ws('|', id, subscription_status, subscription_plan_id, subscription_start_date, "
                "subscription_end_date, subscription_price, updated_at), ',' ORDER BY id), '')) FROM companies"
            ))).one()
            plans = (await db.execute(text("SELECT count(*), md5(coalesce(string_agg(concat_ws('|', id, updated_at), ',' ORDER BY id), '')) FROM subscription_plans"))).one()
            return tuple(companies), tuple(plans)

        before = run_db(_snapshot)
        lead = worked_lead(mgr, p["employee"]["id"])
        t = create_trial(emp, lead["id"])
        patch(emp, "trials", t["id"], {"notes": "x"})
        act(emp, "trials", t["id"], "complete")
        act(emp, "trials", create_trial(emp, lead["id"])["id"], "cancel")
        assert run_db(_snapshot) == before
