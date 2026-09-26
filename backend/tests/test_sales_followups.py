"""JAZ Sales - Phase 3: follow-ups (HTTP integration tier).

Create, edit, complete, cancel and list follow-ups; who can receive one (a deactivated or revoked employee cannot);
the views (My follow-ups, Upcoming, Overdue, Completed) and their counts; and the timeline events. "Overdue" is not a
status: it is pending AND due_at < now. Who may do what is covered by test_sales_activities_rbac.py; this module uses
the roles that are allowed and concentrates on the behavior.
"""
import concurrent.futures
import time
from datetime import timedelta

import pytest

from sales.timezone import app_today

from sales_activity_test_utils import (
    PHANTOM_ID,
    act,
    ago,
    api,
    create_followup,
    create_lead,
    create_staff,
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
from sales_lead_test_utils import assign
from sales_test_utils import admin_token, assert_scratch_target, auth, lookup_user, owner_token, owner_user  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "fu")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture(scope="module")
def emp(p):
    return p["employee"]["headers"]


@pytest.fixture()
def lead(p, mgr):
    return worked_lead(mgr, p["employee"]["id"])


def _deactivate(admin_h, staff_id):
    assert api("PATCH", f"/api/sales/team/{staff_id}", admin_h, {"status": "inactive"}).status_code == 200


def _reactivate(admin_h, staff_id):
    assert api("PATCH", f"/api/sales/team/{staff_id}", admin_h, {"status": "active"}).status_code == 200


# =============================================================================
class TestCreateFollowup:
    def test_defaults_and_shape(self, p, lead, emp):
        due = later(days=1)
        f = create_followup(emp, lead["id"], due_at=due, notes="send the price list")
        assert set(f) == {"id", "lead", "assigned_to", "due_at", "notes", "status", "completed_at", "is_overdue", "created_by", "created_at", "updated_at", "can"}
        assert f["status"] == "pending" and f["completed_at"] is None and f["is_overdue"] is False
        assert abs(parse(f["due_at"]) - parse(due)) < timedelta(seconds=1)
        assert f["notes"] == "send the price list"
        assert f["assigned_to"] == {"id": p["employee"]["id"], "name": "Test fu-employee", "status": "active"}   # nobody named = the caller
        assert f["created_by"]["id"] == p["employee"]["id"]
        assert f["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "assigned", "archived": False}
        assert f["can"] == {"update": True, "complete": True, "cancel": True, "assign": False}                    # assigning others needs sales.leads.assign

    def test_a_manager_can_give_it_to_the_lead_owner(self, p, lead, mgr):
        f = create_followup(mgr, lead["id"], assigned_to=p["employee"]["id"])
        assert f["assigned_to"]["id"] == p["employee"]["id"] and f["created_by"]["id"] == p["manager"]["id"]
        assert f["can"]["assign"] is True
        assert create_followup(mgr, lead["id"])["assigned_to"]["id"] == p["manager"]["id"]                           # nobody named = the manager

    def test_an_employee_cannot_give_it_to_somebody_else(self, p, lead, emp, mgr):
        r = api("POST", "/api/sales/followups", emp, {"lead_id": lead["id"], "due_at": later(days=1), "assigned_to": p["manager"]["id"]})
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"
        assert listing(mgr, f"/api/sales/followups?lead_id={lead['id']}")["total"] == 0                              # nothing was created
        assert create_followup(emp, lead["id"], assigned_to=p["employee"]["id"])["assigned_to"]["id"] == p["employee"]["id"]   # naming yourself is fine

    def test_a_super_admin_must_name_who_it_is_for(self, p, lead, admin_h):
        r = api("POST", "/api/sales/followups", admin_h, {"lead_id": lead["id"], "due_at": later(days=1)})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_required" and errors_of(r)["field"] == "assigned_to"
        assert create_followup(admin_h, lead["id"], assigned_to=p["employee"]["id"])["assigned_to"]["id"] == p["employee"]["id"]

    def test_due_time_rules(self, lead, emp):
        overdue = create_followup(emp, lead["id"], due_at=ago(days=2))                                # a past time is allowed: it is simply overdue
        assert overdue["is_overdue"] is True and overdue["status"] == "pending"
        for bad in ("2026-09-25T10:00:00", "1999-12-31T23:59:59Z", "2100-01-02T00:00:00Z", "tomorrow", None):
            body = {"lead_id": lead["id"]} if bad is None else {"lead_id": lead["id"], "due_at": bad}
            assert api("POST", "/api/sales/followups", emp, body).status_code == 422, bad

    def test_notes_are_trimmed_and_blank_means_none(self, lead, emp):
        assert create_followup(emp, lead["id"], notes="  call back  ")["notes"] == "call back"
        assert create_followup(emp, lead["id"], notes="  ")["notes"] is None
        assert api("POST", "/api/sales/followups", emp, {"lead_id": lead["id"], "due_at": later(days=1), "notes": "x" * 2001}).status_code == 422

    def test_unknown_fields_are_refused(self, lead, emp):
        for smuggled in ({"status": "completed"}, {"completed_at": later(days=1)}, {"created_by": PHANTOM_ID}, {"id": PHANTOM_ID}):
            r = api("POST", "/api/sales/followups", emp, {"lead_id": lead["id"], "due_at": later(days=1), **smuggled})
            assert r.status_code == 422, smuggled

    def test_the_lead_must_exist_be_in_scope_and_not_archived(self, p, mgr, emp):
        r = api("POST", "/api/sales/followups", emp, {"lead_id": PHANTOM_ID, "due_at": later(days=1)})
        assert r.status_code == 404 and errors_of(r)["code"] == "lead_not_found"
        others = worked_lead(mgr, p["employee2"]["id"])
        assert api("POST", "/api/sales/followups", emp, {"lead_id": others["id"], "due_at": later(days=1)}).status_code == 403
        mine = worked_lead(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{mine['id']}", mgr)
        r = api("POST", "/api/sales/followups", emp, {"lead_id": mine["id"], "due_at": later(days=1)})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"


# =============================================================================
class TestWhoCanReceiveAFollowup:
    """A deactivated / revoked / wrong-role person cannot be given a NEW follow-up - and every refusal looks the same."""

    def _refused(self, mgr, lead, who_id, code="activity_assignee_not_eligible"):
        count = lambda: listing(mgr, f"/api/sales/followups?lead_id={lead['id']}&assigned_to={who_id}")["total"]  # noqa: E731
        before = count()
        r = api("POST", "/api/sales/followups", mgr, {"lead_id": lead["id"], "due_at": later(days=1), "assigned_to": who_id})
        assert r.status_code == 400, r.text
        detail = errors_of(r)
        assert detail["field"] == "assigned_to" and detail["code"] == code
        assert count() == before                                                                                     # nothing was created

    def test_a_deactivated_employee_cannot_receive_one_and_can_again_once_reactivated(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "fu-deactivated", do_login=False)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], worker["id"])
        assert create_followup(mgr, lead["id"], assigned_to=worker["id"])                                            # eligible while active
        _deactivate(admin_h, worker["id"])
        self._refused(mgr, lead, worker["id"])
        _reactivate(admin_h, worker["id"])
        assert create_followup(mgr, lead["id"], assigned_to=worker["id"])["assigned_to"]["id"] == worker["id"]

    def test_an_employee_whose_role_was_revoked_cannot_receive_one_and_can_again_once_regranted(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "fu-revoked", do_login=False)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], worker["id"])
        assert api("DELETE", f"/api/sales/team/{worker['id']}/roles/sales_employee", admin_h).status_code == 200
        self._refused(mgr, lead, worker["id"])
        assert api("POST", f"/api/sales/team/{worker['id']}/roles", admin_h, {"role_key": "sales_employee"}).status_code == 200
        assert create_followup(mgr, lead["id"], assigned_to=worker["id"])

    def test_wrong_role_accounts_and_made_up_ids_get_the_same_answer(self, admin_h, p, mgr, owner_user):
        lead = worked_lead(mgr, p["employee"]["id"])
        admin_id = lookup_user("admin@jaz.com")["id"]
        for who in (p["data_entry"]["id"], p["onboarding"]["id"], p["no_roles"]["id"], admin_id, owner_user["id"], PHANTOM_ID):
            self._refused(mgr, lead, who)

    def test_the_assignee_must_be_able_to_see_the_lead(self, p, mgr):
        lead = worked_lead(mgr, p["employee"]["id"])
        self._refused(mgr, lead, p["employee2"]["id"], code="activity_assignee_cannot_see_lead")              # employee2 sees only their own leads
        assert create_followup(mgr, lead["id"], assigned_to=p["employee"]["id"])                                     # the lead's owner: yes
        assert create_followup(mgr, lead["id"], assigned_to=p["manager"]["id"])                                      # a manager sees every lead: yes

    def test_a_custom_role_holding_the_permission_and_a_scope_may_receive_one(self, admin_h, p, mgr):
        from sales_activity_test_utils import make_custom_role, retire_roles
        role = make_custom_role(["sales.access", "sales.followups.manage", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], "fu-custom", do_login=False)
            lead = worked_lead(mgr, p["employee"]["id"])
            assert create_followup(mgr, lead["id"], assigned_to=person["id"])["assigned_to"]["id"] == person["id"]
            missing = make_custom_role(["sales.access", "sales.followups.view", "sales.leads.scope_all"])          # can VIEW but not WORK follow-ups
            try:
                viewer = create_staff(admin_h, [missing], "fu-viewer", do_login=False)
                self._refused(mgr, lead, viewer["id"])
            finally:
                retire_roles([missing])
        finally:
            retire_roles([role])

    def test_existing_followups_of_a_deactivated_employee_stay_put_and_can_be_handed_over(self, admin_h, p, mgr):
        a = create_staff(admin_h, ["sales_employee"], "fu-a", do_login=False)
        b = create_staff(admin_h, ["sales_employee"], "fu-b", do_login=False)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], a["id"])
        f = create_followup(mgr, lead["id"], assigned_to=a["id"], due_at=ago(days=1))
        _deactivate(admin_h, a["id"])
        shown = fetch(mgr, "followups", f["id"])
        assert shown["assigned_to"] == {"id": a["id"], "name": "Test fu-a", "status": "inactive"} and shown["is_overdue"] is True   # nothing is lost or reassigned silently
        # ...and the manager sees it overdue (narrowed to the lead: the scratch DB accumulates far-past overdue items from other runs,
        # so the GLOBAL first page is not a meaningful place to look)
        assert f["id"] in ids_of(listing(mgr, f"/api/sales/followups?due=overdue&lead_id={lead['id']}&limit=100"))
        assert patch(mgr, "followups", f["id"], {"notes": "still editable"})["notes"] == "still editable"            # the current assignee is never re-checked
        # handing it to B: B must be eligible AND see the lead, so the lead moves first
        assert api("PATCH", f"/api/sales/followups/{f['id']}", mgr, {"assigned_to": b["id"]}).status_code == 400
        assign(mgr, lead["id"], b["id"])
        moved = patch(mgr, "followups", f["id"], {"assigned_to": b["id"]})
        assert moved["assigned_to"]["id"] == b["id"]
        event = timeline_of(mgr, lead["id"])[-1]
        assert event["event_type"] == "followup_updated"
        assert event["before"]["assigned_to"] == {"id": a["id"], "name": "Test fu-a"}
        assert event["after"]["assigned_to"] == {"id": b["id"], "name": "Test fu-b"}


# =============================================================================
class TestCompleteAndCancel:
    def test_complete(self, p, lead, emp, mgr):
        f = create_followup(emp, lead["id"], due_at=ago(hours=5), notes="ring them")
        done = act(emp, "followups", f["id"], "complete", {"note": "spoke to them, all good"})
        assert done["status"] == "completed" and done["is_overdue"] is False                                        # completed is no longer overdue
        assert abs(parse(done["completed_at"]) - utcnow()) < timedelta(minutes=2)
        assert done["can"] == {"update": False, "complete": False, "cancel": False, "assign": False}
        assert done["due_at"] == f["due_at"] and done["notes"] == "ring them"                                       # the follow-up itself is unchanged
        created, completed = timeline_of(mgr, lead["id"])
        assert completed["event_type"] == "followup_completed"
        assert completed["actor"]["id"] == p["employee"]["id"]
        assert completed["before"]["status"] == "pending" and completed["after"]["status"] == "completed"
        assert abs(parse(completed["after"]["completed_at"]) - parse(done["completed_at"])) < timedelta(seconds=1)
        assert completed["note"] == "spoke to them, all good"
        assert completed["metadata"]["followup_id"] == f["id"] and completed["metadata"]["was_overdue"] is True

    def test_completing_one_that_was_not_yet_due_is_not_marked_overdue(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"], due_at=later(days=3))
        act(emp, "followups", f["id"], "complete")
        assert timeline_of(mgr, lead["id"])[-1]["metadata"]["was_overdue"] is False

    def test_the_note_is_optional_and_the_body_may_be_absent_or_empty(self, lead, emp, mgr):
        for body in (None, {}, {"note": None}, {"note": "   "}):
            f = create_followup(emp, lead["id"])
            act(emp, "followups", f["id"], "complete", body)
            assert timeline_of(mgr, lead["id"])[-1]["note"] is None
        f = create_followup(emp, lead["id"])
        assert api("POST", f"/api/sales/followups/{f['id']}/complete", emp, {"note": "x" * 1001}).status_code == 422
        assert api("POST", f"/api/sales/followups/{f['id']}/complete", emp, {"status": "cancelled"}).status_code == 422
        assert fetch(emp, "followups", f["id"])["status"] == "pending"

    def test_cancel(self, p, lead, emp, mgr):
        f = create_followup(emp, lead["id"])
        cancelled = act(emp, "followups", f["id"], "cancel", {"note": "the lead asked us to stop"})
        assert cancelled["status"] == "cancelled" and cancelled["completed_at"] is None
        assert cancelled["can"] == {"update": False, "complete": False, "cancel": False, "assign": False}
        event = timeline_of(mgr, lead["id"])[-1]
        assert event["event_type"] == "followup_cancelled" and event["actor"]["id"] == p["employee"]["id"]
        assert event["before"]["status"] == "pending" and event["after"] == {"status": "cancelled"}
        assert event["note"] == "the lead asked us to stop" and event["metadata"]["followup_id"] == f["id"]

    def test_a_follow_up_ends_only_once(self, lead, emp, mgr):
        for first, second in (("complete", "complete"), ("complete", "cancel"), ("cancel", "complete"), ("cancel", "cancel")):
            f = create_followup(emp, lead["id"])
            act(emp, "followups", f["id"], first)
            r = api("POST", f"/api/sales/followups/{f['id']}/{second}", emp)
            assert r.status_code == 409 and errors_of(r)["code"] == "followup_not_pending", (first, second)
            r = api("PATCH", f"/api/sales/followups/{f['id']}", emp, {"notes": "too late"})
            assert r.status_code == 409 and errors_of(r)["code"] == "followup_not_pending"
        assert len([t for t in work_types(mgr, lead["id"]) if t in ("followup_completed", "followup_cancelled")]) == 4    # one per follow-up: no duplicates

    def test_unknown_and_malformed_ids(self, emp):
        for bad in (PHANTOM_ID, "not-a-uuid"):
            for action in ("complete", "cancel"):
                assert api("POST", f"/api/sales/followups/{bad}/{action}", emp).status_code == 404
            assert api("PATCH", f"/api/sales/followups/{bad}", emp, {"notes": "x"}).status_code == 404
            assert api("GET", f"/api/sales/followups/{bad}", emp).status_code == 404

    def test_two_people_completing_at_once_produce_exactly_one_completion(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda h: api("POST", f"/api/sales/followups/{f['id']}/complete", h), [emp, mgr] * 3))
        assert sorted(r.status_code for r in results) == [200] + [409] * 5
        assert work_types(mgr, lead["id"]).count("followup_completed") == 1

    def test_completing_and_cancelling_never_move_the_lead(self, lead, emp, mgr):
        before = get_lead(mgr, lead["id"])
        act(emp, "followups", create_followup(emp, lead["id"])["id"], "complete")
        act(emp, "followups", create_followup(emp, lead["id"])["id"], "cancel")
        assert get_lead(mgr, lead["id"]) == before


# =============================================================================
class TestEditAFollowup:
    def test_change_the_due_time_and_notes(self, lead, emp):
        f = create_followup(emp, lead["id"], notes="first")
        new_due = later(days=4)
        updated = patch(emp, "followups", f["id"], {"due_at": new_due, "notes": "second"})
        assert abs(parse(updated["due_at"]) - parse(new_due)) < timedelta(seconds=1) and updated["notes"] == "second"
        assert updated["status"] == "pending" and updated["assigned_to"] == f["assigned_to"] and updated["lead"] == f["lead"]
        assert fetch(emp, "followups", f["id"]) == updated
        assert patch(emp, "followups", f["id"], {"notes": None})["notes"] is None                                    # notes can be cleared
        for field in ("due_at", "assigned_to"):
            r = api("PATCH", f"/api/sales/followups/{f['id']}", emp, {field: None})
            assert r.status_code == 400 and errors_of(r)["field"] == field                                           # ...the rest cannot

    def test_invalid_edits_are_refused_and_change_nothing(self, lead, emp):
        f = create_followup(emp, lead["id"])
        assert api("PATCH", f"/api/sales/followups/{f['id']}", emp, {}).status_code == 400
        for bad in ({"due_at": "2026-01-01T00:00:00"}, {"due_at": "1999-01-01T00:00:00Z"}, {"notes": "x" * 2001}, {"status": "completed"}, {"lead_id": PHANTOM_ID}, {"created_by": PHANTOM_ID}):
            assert api("PATCH", f"/api/sales/followups/{f['id']}", emp, bad).status_code == 422, bad
        assert fetch(emp, "followups", f["id"]) == f

    def test_saving_the_same_values_writes_nothing_and_records_nothing(self, p, lead, emp, mgr):
        f = create_followup(emp, lead["id"], notes="same")
        before = work_types(mgr, lead["id"])
        again = patch(emp, "followups", f["id"], {"due_at": f["due_at"], "notes": "same", "assigned_to": p["employee"]["id"]})
        assert again == f
        assert work_types(mgr, lead["id"]) == before

    def test_changing_the_assignee_needs_the_assign_permission_and_an_eligible_person(self, p, lead, emp, mgr):
        f = create_followup(emp, lead["id"])
        r = api("PATCH", f"/api/sales/followups/{f['id']}", emp, {"assigned_to": p["manager"]["id"]})
        assert r.status_code == 403                                                                                  # an employee cannot hand it over
        moved = patch(mgr, "followups", f["id"], {"assigned_to": p["manager"]["id"]})
        assert moved["assigned_to"]["id"] == p["manager"]["id"]
        r = api("PATCH", f"/api/sales/followups/{f['id']}", mgr, {"assigned_to": p["data_entry"]["id"]})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_not_eligible"
        r = api("PATCH", f"/api/sales/followups/{f['id']}", mgr, {"assigned_to": p["employee2"]["id"]})
        assert r.status_code == 400 and errors_of(r)["code"] == "activity_assignee_cannot_see_lead"
        assert fetch(mgr, "followups", f["id"])["assigned_to"]["id"] == p["manager"]["id"]                           # the refusals changed nothing

    def test_the_edit_events_carry_before_and_after(self, p, lead, emp, mgr):
        f = create_followup(emp, lead["id"], notes="before")
        new_due = later(days=6)
        patch(emp, "followups", f["id"], {"notes": "after", "due_at": new_due})
        patch(mgr, "followups", f["id"], {"assigned_to": p["manager"]["id"]})
        created, by_employee, by_manager = timeline_of(mgr, lead["id"])
        assert created["event_type"] == "followup_created"
        assert by_employee["event_type"] == "followup_updated" and by_employee["metadata"]["fields"] == ["due_at", "notes"]
        assert by_employee["before"]["notes"] == "before" and by_employee["after"]["notes"] == "after"
        assert abs(parse(by_employee["after"]["due_at"]) - parse(new_due)) < timedelta(seconds=1)
        assert abs(parse(by_employee["before"]["due_at"]) - parse(f["due_at"])) < timedelta(seconds=1)
        assert by_manager["before"] == {"assigned_to": {"id": p["employee"]["id"], "name": "Test fu-employee"}}
        assert by_manager["after"] == {"assigned_to": {"id": p["manager"]["id"], "name": "Test fu-manager"}}
        assert by_manager["actor"]["id"] == p["manager"]["id"]

    def test_an_archived_leads_follow_up_is_frozen(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        f = create_followup(emp, lead["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        for method, path, body in (("PATCH", "", {"notes": "x"}), ("POST", "/complete", None), ("POST", "/cancel", None)):
            r = api(method, f"/api/sales/followups/{f['id']}{path}", emp, body)
            assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived", path
        assert fetch(emp, "followups", f["id"])["can"] == {"update": False, "complete": False, "cancel": False, "assign": False}
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        act(emp, "followups", f["id"], "complete")

    def test_there_is_no_way_to_delete_a_follow_up(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"])
        assert api("DELETE", f"/api/sales/followups/{f['id']}", mgr).status_code == 405


# =============================================================================
@pytest.fixture(scope="module")
def world(p, mgr, emp):
    """One lead holding eight follow-ups that between them exercise every view."""
    lead = worked_lead(mgr, p["employee"]["id"])
    items = {
        "overdue_old": create_followup(emp, lead["id"], due_at=ago(days=3), notes="o1"),
        "overdue_new": create_followup(emp, lead["id"], due_at=ago(hours=1), notes="o2"),
        "soon": create_followup(emp, lead["id"], due_at=later(hours=2), notes="u1"),
        "later": create_followup(mgr, lead["id"], due_at=later(days=5), assigned_to=p["employee"]["id"], notes="u2"),
        "managers_own": create_followup(mgr, lead["id"], due_at=later(days=1), notes="m1"),
    }
    items["done"] = act(emp, "followups", create_followup(emp, lead["id"], due_at=ago(days=2))["id"], "complete")
    items["done2"] = act(emp, "followups", create_followup(emp, lead["id"], due_at=later(days=1))["id"], "complete")
    items["dropped"] = act(emp, "followups", create_followup(emp, lead["id"], due_at=ago(days=1))["id"], "cancel")
    return {"lead": lead, "items": items, "base": f"/api/sales/followups?lead_id={lead['id']}&limit=100"}


class TestViews:
    """My follow-ups / Upcoming / Overdue / Completed on one lead's worth of follow-ups."""

    def _ids(self, world, h, query=""):
        page = listing(h, world["base"] + query)
        by_id = {v["id"]: k for k, v in world["items"].items()}
        return [by_id[i] for i in ids_of(page)]

    def test_my_follow_ups_are_mine_and_pending_soonest_first(self, world, emp, mgr):
        assert self._ids(world, emp, "&assigned_to=me&status=pending") == ["overdue_old", "overdue_new", "soon", "later"]
        assert self._ids(world, mgr, "&assigned_to=me&status=pending") == ["managers_own"]                          # "me" is whoever is asking

    def test_upcoming_is_pending_and_not_yet_due(self, world, emp):
        assert self._ids(world, emp, "&due=upcoming") == ["soon", "managers_own", "later"]

    def test_overdue_is_pending_and_already_due(self, world, emp):
        assert self._ids(world, emp, "&due=overdue") == ["overdue_old", "overdue_new"]                              # a completed / cancelled one never is
        assert all(i["is_overdue"] for i in listing(emp, world["base"] + "&due=overdue")["items"])
        assert self._ids(world, emp, "&due=overdue&order=desc") == ["overdue_new", "overdue_old"]

    def test_completed_and_cancelled(self, world, emp):
        assert self._ids(world, emp, "&status=completed&sort=completed_at&order=desc") == ["done2", "done"]           # newest completion first
        assert self._ids(world, emp, "&status=cancelled") == ["dropped"]
        assert set(self._ids(world, emp, "&status=completed,cancelled")) == {"done", "done2", "dropped"}
        assert all(i["is_overdue"] is False for i in listing(emp, world["base"] + "&status=completed,cancelled")["items"])

    def test_conflicting_filters_simply_match_nothing(self, world, emp):
        assert self._ids(world, emp, "&status=completed&due=overdue") == []                                         # `due` only ever means pending
        assert self._ids(world, emp, "&status=cancelled&due=upcoming") == []

    def test_the_flag_on_every_item_agrees_with_the_filters(self, world, emp):
        for item in listing(emp, world["base"])["items"]:
            assert item["is_overdue"] == (item["status"] == "pending" and parse(item["due_at"]) < utcnow()), item["id"]

    def test_counts(self, world, emp, mgr):
        base = f"/api/sales/followups/counts?lead_id={world['lead']['id']}"
        assert listing(emp, base) == {"pending": 5, "overdue": 2, "upcoming": 3, "completed": 2, "cancelled": 1, "mine_pending": 4, "mine_overdue": 2}
        assert listing(mgr, base) == {"pending": 5, "overdue": 2, "upcoming": 3, "completed": 2, "cancelled": 1, "mine_pending": 1, "mine_overdue": 0}
        assert listing(emp, base + "&assigned_to=me")["pending"] == 4                                              # the assignee filter narrows the numbers
        assert listing(emp, base + "&status=completed&due=overdue") == listing(emp, base)                          # status / due filters are ignored by the counts

    def test_date_range_and_search(self, p, mgr, emp):
        token = uniq(12)
        lead = worked_lead(mgr, p["employee"]["id"], business_name=f"Windowed {token} Co")
        today = app_today()
        a = create_followup(emp, lead["id"], due_at=ago(days=10))
        b = create_followup(emp, lead["id"], due_at=later(days=2))
        c = create_followup(emp, lead["id"], due_at=later(days=20))
        base = f"/api/sales/followups?q={token}"
        assert set(ids_of(listing(emp, base))) == {a["id"], b["id"], c["id"]}
        assert ids_of(listing(emp, base + f"&due_from={today}&due_to={today + timedelta(days=5)}")) == [b["id"]]
        assert ids_of(listing(emp, base + f"&due_to={today - timedelta(days=5)}")) == [a["id"]]
        assert listing(emp, f"/api/sales/followups?q={token}zz")["total"] == 0

    def test_sorting_and_pagination(self, world, emp):
        page = listing(emp, world["base"] + "&sort=created_at&order=desc")
        created = [parse(i["created_at"]) for i in page["items"]]
        assert created == sorted(created, reverse=True)
        first, second = (listing(emp, f"/api/sales/followups?lead_id={world['lead']['id']}&limit=3&offset={o}") for o in (0, 3))
        assert first["total"] == second["total"] == 8 and len(first["items"]) == 3 and len(second["items"]) == 3
        assert not set(ids_of(first)) & set(ids_of(second))
        for bad in ("limit=0", "limit=101", "offset=-1", "sort=notes", "order=up", "due=soon", "status=done", "status=pending,done", "assigned_to=nobody", "due_from=someday"):
            assert api("GET", f"/api/sales/followups?{bad}", emp).status_code in (400, 422), bad

    def test_a_cross_lead_list_hides_archived_leads(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        f = create_followup(emp, lead["id"], due_at=ago(days=1), notes=f"arch-{uniq()}")
        assert f["id"] in ids_of(listing(emp, "/api/sales/followups?due=overdue&assigned_to=me&limit=100"))
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert f["id"] not in ids_of(listing(emp, "/api/sales/followups?due=overdue&assigned_to=me&limit=100"))
        assert ids_of(listing(emp, f"/api/sales/followups?lead_id={lead['id']}")) == [f["id"]]
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert f["id"] in ids_of(listing(emp, "/api/sales/followups?due=overdue&assigned_to=me&limit=100"))

    def test_a_follow_up_becomes_overdue_when_its_time_passes(self, lead, emp):
        f = create_followup(emp, lead["id"], due_at=later(seconds=2))
        assert f["is_overdue"] is False
        assert f["id"] not in ids_of(listing(emp, f"/api/sales/followups?lead_id={lead['id']}&due=overdue"))
        time.sleep(3)
        assert fetch(emp, "followups", f["id"])["is_overdue"] is True
        assert ids_of(listing(emp, f"/api/sales/followups?lead_id={lead['id']}&due=overdue")) == [f["id"]]
        assert listing(emp, f"/api/sales/followups/counts?lead_id={lead['id']}")["overdue"] == 1


# =============================================================================
class TestFollowupTimeline:
    def test_the_full_history_of_one_follow_up(self, p, lead, emp, mgr):
        due = later(days=2)
        f = create_followup(emp, lead["id"], due_at=due, notes="prepare the quote")
        patch(emp, "followups", f["id"], {"notes": "prepare the quote and the contract"})
        act(emp, "followups", f["id"], "complete", {"note": "done"})
        created, updated, completed = timeline_of(mgr, lead["id"])
        assert [e["event_type"] for e in (created, updated, completed)] == ["followup_created", "followup_updated", "followup_completed"]
        assert created["actor"] == {"id": p["employee"]["id"], "name": "Test fu-employee"}
        assert created["before"] is None and created["note"] == "prepare the quote" and created["metadata"]["followup_id"] == f["id"]
        assert abs(parse(created["after"]["due_at"]) - parse(due)) < timedelta(seconds=1)
        assert created["after"]["assigned_to"] == {"id": p["employee"]["id"], "name": "Test fu-employee"}
        assert created["metadata"]["actor_roles"] == ["sales_employee"]
        assert [e["seq"] for e in (created, updated, completed)] == sorted(e["seq"] for e in (created, updated, completed))

    def test_long_notes_are_clipped_on_the_timeline_but_kept_whole_on_the_follow_up(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"], notes="n" * 1900)
        assert len(timeline_of(mgr, lead["id"])[-1]["note"]) == 1001
        assert fetch(emp, "followups", f["id"])["notes"] == "n" * 1900

    def test_events_are_only_written_for_real_changes(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"])
        assert api("PATCH", f"/api/sales/followups/{f['id']}", emp, {}).status_code == 400                           # refused: no event
        api("POST", f"/api/sales/followups/{f['id']}/complete", emp, {"note": "x" * 1001})                          # refused: no event
        assert work_types(mgr, lead["id"]) == ["followup_created"]

    def test_pipeline_stage_is_untouched_by_any_follow_up_action(self, lead, emp, mgr):
        f = create_followup(emp, lead["id"], due_at=ago(days=1))
        patch(emp, "followups", f["id"], {"due_at": later(days=1)})
        act(emp, "followups", f["id"], "complete")
        assert stage_of(mgr, lead["id"]) == "assigned"
