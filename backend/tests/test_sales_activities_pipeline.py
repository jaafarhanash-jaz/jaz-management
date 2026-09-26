"""JAZ Sales - Phase 3: how calls, follow-ups, demos and trials meet the Phase-2 pipeline (HTTP integration tier).

The rules under test:
  * an activity NEVER moves the lead by itself - not when it is created, not when it is completed, cancelled or marked
    no-show. A stage change stays the explicit, separately validated POST /leads/{id}/stage;
  * that stage endpoint keeps enforcing the Phase-2 transition rules whatever the activities say - the natural next stage
    after an activity (demo scheduled -> demo_scheduled ...) is offered by the UI but checked by the server like any other;
  * activities and stage changes interleave on ONE immutable timeline;
  * activities follow the lead: they live with it through won / lost / reopen, reassignment, unassignment and archiving.
"""
import pytest

from sales_activity_test_utils import (
    PHANTOM_ID,
    act,
    activities,
    ago,
    api,
    assign,
    create_call,
    create_demo,
    create_followup,
    create_lead,
    create_staff,
    create_trial,
    errors_of,
    fetch,
    get_lead,
    ids_of,
    later,
    listing,
    make_custom_role,
    make_personas,
    retire_roles,
    set_stage,
    stage_of,
    timeline_of,
    work_types,
    worked_lead,
)
from sales_customer_test_utils import agree
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401

# the working stages a salesperson moves a lead through once they own it
WORKING = ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "pipe")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture(scope="module")
def emp(p):
    return p["employee"]["headers"]


def _in_stage(mgr, emp, p, stage):
    """A fresh lead owned by the employee and moved (by the employee) to `stage`."""
    lead = worked_lead(mgr, p["employee"]["id"])
    if stage != "assigned":
        set_stage(emp, lead["id"], stage)
    return lead


def _everything(emp, lead_id):
    """One of every kind of activity, in each of its lifecycle outcomes - every action the API offers."""
    call = create_call(emp, lead_id, result="interested")
    api("PATCH", f"/api/sales/calls/{call['id']}", emp, {"notes": "corrected"})
    f1, f2, f3 = (create_followup(emp, lead_id) for _ in range(3))
    api("PATCH", f"/api/sales/followups/{f1['id']}", emp, {"notes": "edited"})
    act(emp, "followups", f1["id"], "complete")
    act(emp, "followups", f2["id"], "cancel")
    d1, d2, d3, d4 = (create_demo(emp, lead_id) for _ in range(4))
    act(emp, "demos", d1["id"], "reschedule", {"scheduled_at": later(days=9)})
    api("PATCH", f"/api/sales/demos/{d1['id']}", emp, {"notes": "edited"})
    act(emp, "demos", d2["id"], "complete")
    act(emp, "demos", d3["id"], "cancel")
    act(emp, "demos", d4["id"], "no-show")
    t1 = create_trial(emp, lead_id)
    api("PATCH", f"/api/sales/trials/{t1['id']}", emp, {"notes": "edited"})
    act(emp, "trials", t1["id"], "complete")
    t2 = create_trial(emp, lead_id)
    act(emp, "trials", t2["id"], "cancel")
    create_trial(emp, lead_id)                                                                                    # left active


# =============================================================================
class TestActivitiesNeverMoveTheLead:
    LEAD_FACTS = ("pipeline_stage", "assigned_to", "assigned_at", "lost_reason", "closed_at", "archived_at", "priority", "estimated_value", "updated_at", "allowed_stages")

    @pytest.mark.parametrize("stage", ["assigned"] + WORKING)
    def test_no_activity_action_changes_the_lead_in_any_working_stage(self, p, mgr, emp, stage):
        lead = _in_stage(mgr, emp, p, stage)
        before = get_lead(mgr, lead["id"])
        _everything(emp, lead["id"])
        after = get_lead(mgr, lead["id"])
        assert {k: after[k] for k in self.LEAD_FACTS} == {k: before[k] for k in self.LEAD_FACTS}, stage
        assert after == before                                                                                    # not one field of the lead moved
        assert stage_of(mgr, lead["id"]) == stage

    @pytest.mark.parametrize("result", ["answered", "no_answer", "busy", "wrong_number", "interested", "not_interested", "call_back"])
    def test_no_call_result_moves_the_lead_not_even_a_hopeless_one(self, p, mgr, emp, result):
        lead = worked_lead(mgr, p["employee"]["id"])
        create_call(emp, lead["id"], result=result)
        assert stage_of(mgr, lead["id"]) == "assigned"                                                            # even wrong_number / not_interested do not close it

    def test_a_manager_and_a_super_admin_do_not_move_it_either(self, p, mgr, admin_h):
        lead = worked_lead(mgr, p["employee"]["id"])
        for headers in (mgr, admin_h):
            create_call(headers, lead["id"])
            create_followup(headers, lead["id"], assigned_to=p["employee"]["id"])
            create_demo(headers, lead["id"], assigned_to=p["employee"]["id"])
        create_trial(mgr, lead["id"])
        assert stage_of(mgr, lead["id"]) == "assigned"

    def test_an_activity_never_writes_a_stage_event(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        _everything(emp, lead["id"])
        types = [e["event_type"] for e in activities(mgr, lead["id"])]
        assert types.count("stage_changed") == 1                                                                  # the automatic new -> assigned, and nothing else
        assert not {"lead_marked_won", "lead_marked_lost", "lead_updated"} & set(types)


# =============================================================================
class TestStageChangesStayServerValidated:
    def test_the_natural_next_stage_after_each_activity_is_an_ordinary_validated_move(self, p, mgr, emp):
        """What the UI offers after an activity: the server checks it exactly like any stage change."""
        lead = worked_lead(mgr, p["employee"]["id"])
        create_call(emp, lead["id"], result="answered")
        assert set_stage(emp, lead["id"], "contacted")["pipeline_stage"] == "contacted"
        create_call(emp, lead["id"], result="interested")
        assert set_stage(emp, lead["id"], "interested")["pipeline_stage"] == "interested"
        demo = create_demo(emp, lead["id"])
        assert set_stage(emp, lead["id"], "demo_scheduled")["pipeline_stage"] == "demo_scheduled"
        act(emp, "demos", demo["id"], "complete")
        assert set_stage(emp, lead["id"], "demo_completed")["pipeline_stage"] == "demo_completed"
        trial = create_trial(emp, lead["id"])
        assert set_stage(emp, lead["id"], "trial")["pipeline_stage"] == "trial"
        act(emp, "trials", trial["id"], "complete")
        assert set_stage(emp, lead["id"], "negotiation")["pipeline_stage"] == "negotiation"
        # `won` is nobody's stage move - not the salesperson's, not the manager's (a lead is won only by the Customer Setup,
        # test_sales_workflow.py): the server refuses it whatever they completed
        for headers in (emp, mgr):
            r = api("POST", f"/api/sales/leads/{lead['id']}/stage", headers, {"stage": "won"})
            assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup" and stage_of(mgr, lead["id"]) == "negotiation"
        agree(emp, lead)                                                                                          # ... the Customer Setup is what wins it
        assert stage_of(mgr, lead["id"]) == "won"

    def test_an_activity_does_not_make_an_invalid_move_valid(self, p, mgr, emp):
        # an UNASSIGNED lead ("new") can only be closed as lost - a demo on it changes nothing about that
        lead = create_lead(mgr)
        demo = create_demo(mgr, lead["id"])                                                                       # a manager may schedule one for themselves
        for stage in ("demo_scheduled", "demo_completed", "trial", "contacted"):
            r = api("POST", f"/api/sales/leads/{lead['id']}/stage", mgr, {"stage": stage})
            assert r.status_code == 409 and errors_of(r)["code"] == "transition_not_allowed", stage
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", mgr, {"stage": "won"})                            # (won is never a manual move)
        assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup"
        assert stage_of(mgr, lead["id"]) == "new" and fetch(mgr, "demos", demo["id"])["status"] == "scheduled"

    def test_an_active_trial_does_not_open_the_way_from_new_to_trial(self, p, mgr):
        lead = create_lead(mgr)
        create_trial(mgr, lead["id"])
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", mgr, {"stage": "trial"})
        assert r.status_code == 409 and errors_of(r)["code"] == "transition_not_allowed"

    def test_the_transition_table_still_applies_whatever_was_completed(self, p, mgr, emp):
        lead = _in_stage(mgr, emp, p, "demo_scheduled")
        act(emp, "demos", create_demo(emp, lead["id"])["id"], "complete")
        assert set_stage(emp, lead["id"], "demo_completed")["pipeline_stage"] == "demo_completed"
        for stage in ("new", "assigned"):                                                                          # never back to the unowned stages by hand
            r = api("POST", f"/api/sales/leads/{lead['id']}/stage", emp, {"stage": stage})
            assert r.status_code == 409 and errors_of(r)["code"] == "transition_not_allowed", stage
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", emp, {"stage": "demo_completed"})
        assert r.status_code == 409 and errors_of(r)["code"] == "same_stage"

    def test_lost_still_needs_a_reason_and_won_is_still_terminal(self, p, mgr, emp):
        lead = _in_stage(mgr, emp, p, "interested")
        act(emp, "demos", create_demo(emp, lead["id"])["id"], "complete")
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", emp, {"stage": "lost"})
        assert r.status_code == 400 and errors_of(r)["code"] == "lost_reason_required"
        agree(emp, lead)                                                                                          # the Customer Setup wins it
        assert get_lead(mgr, lead["id"])["closed_at"] is not None
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", emp, {"stage": "negotiation"})
        assert r.status_code == 409 and errors_of(r)["code"] == "transition_not_allowed"                          # won is terminal, activities or not

    def test_allowed_stages_do_not_depend_on_the_activities(self, p, mgr, emp):
        bare = _in_stage(mgr, emp, p, "interested")
        busy = _in_stage(mgr, emp, p, "interested")
        _everything(emp, busy["id"])
        assert get_lead(mgr, busy["id"])["allowed_stages"] == get_lead(mgr, bare["id"])["allowed_stages"]

    def test_logging_work_and_moving_the_stage_are_separate_permissions(self, admin_h, p, mgr):
        """Somebody who may log calls but not change stages gets no back door into the pipeline."""
        role = make_custom_role(["sales.calls.manage", "sales.calls.view", "sales.leads.view", "sales.leads.scope_all"])
        try:
            person = create_staff(admin_h, [role], "pipe-nostage")
            lead = worked_lead(mgr, p["employee"]["id"])
            assert create_call(person["headers"], lead["id"], result="interested")
            r = api("POST", f"/api/sales/leads/{lead['id']}/stage", person["headers"], {"stage": "contacted"})
            assert r.status_code == 403 and stage_of(mgr, lead["id"]) == "assigned"
        finally:
            retire_roles([role])

    def test_the_stage_change_by_an_employee_is_recorded_between_the_activities(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        create_call(emp, lead["id"])
        set_stage(emp, lead["id"], "contacted", note="after the call")
        create_followup(emp, lead["id"])
        types = [e["event_type"] for e in activities(mgr, lead["id"])]
        assert types == ["lead_created", "lead_assigned", "stage_changed", "call_created", "stage_changed", "followup_created"]


# =============================================================================
class TestAWholeSalesJourney:
    def test_new_to_won_with_every_kind_of_activity_on_one_timeline(self, p, mgr, emp):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])                                                              # new -> assigned (automatic)
        create_call(emp, lead["id"], result="answered", duration_seconds=60, notes="introduced JAZ")
        set_stage(emp, lead["id"], "contacted")
        follow = create_followup(emp, lead["id"], due_at=later(days=1), notes="send the brochure")
        create_call(emp, lead["id"], result="interested", duration_seconds=300)
        set_stage(emp, lead["id"], "interested")
        act(emp, "followups", follow["id"], "complete", {"note": "sent"})
        demo = create_demo(emp, lead["id"], scheduled_at=later(days=2))
        set_stage(emp, lead["id"], "demo_scheduled")
        act(emp, "demos", demo["id"], "reschedule", {"scheduled_at": later(days=3), "note": "client asked"})
        act(emp, "demos", demo["id"], "complete", {"note": "great demo"})
        set_stage(emp, lead["id"], "demo_completed")
        trial = create_trial(emp, lead["id"], expected_end_at=later(days=14))
        set_stage(emp, lead["id"], "trial")
        act(emp, "trials", trial["id"], "complete", {"note": "they want to buy"})
        set_stage(emp, lead["id"], "negotiation")
        agree(emp, lead)                                                                                          # "Agreed": the Customer Setup wins the lead and makes the customer

        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events] == [
            "lead_created", "lead_assigned", "stage_changed",
            "call_created", "stage_changed",
            "followup_created", "call_created", "stage_changed", "followup_completed",
            "demo_scheduled", "stage_changed", "demo_rescheduled", "demo_completed", "stage_changed",
            "trial_started", "stage_changed", "trial_completed",
            "stage_changed", "stage_changed", "lead_marked_won", "lead_converted",
        ]
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)                                     # one ordered, immutable history
        stages = [e["after"]["pipeline_stage"] for e in events if e["event_type"] == "stage_changed"]
        assert stages == ["assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won"]
        assert {e["actor"]["name"] for e in events[:3]} == {"Test pipe-manager"}                                    # created and assigned by the manager ...
        assert {e["actor"]["name"] for e in events[3:]} == {"Test pipe-employee"}                                   # ... everything after: the employee
        assert stage_of(mgr, lead["id"]) == "won" and get_lead(mgr, lead["id"])["closed_at"] is not None
        # everything the employee did is reachable from the lead
        for kind, n in (("calls", 2), ("followups", 1), ("demos", 1), ("trials", 1)):
            assert listing(emp, f"/api/sales/{kind}?lead_id={lead['id']}")["total"] == n, kind


# =============================================================================
class TestActivitiesFollowTheLead:
    def test_nothing_is_cancelled_when_a_lead_is_won_or_lost(self, p, mgr, emp):
        lead = _in_stage(mgr, emp, p, "negotiation")
        f, d, t = create_followup(emp, lead["id"]), create_demo(emp, lead["id"]), create_trial(emp, lead["id"])
        set_stage(emp, lead["id"], "lost", lost_reason="too_expensive", note="budget")
        assert (fetch(emp, "followups", f["id"])["status"], fetch(emp, "demos", d["id"])["status"], fetch(emp, "trials", t["id"])["status"]) == ("pending", "scheduled", "active")
        assert work_types(mgr, lead["id"]) == ["followup_created", "demo_scheduled", "trial_started"]              # no automatic cancellation events

    def test_a_closed_lead_can_still_be_worked_and_its_items_cleaned_up(self, p, mgr, emp):
        """Won and lost are pipeline states, not locks: a lost lead may get a follow-up ("call back in six months"), and
        the leftovers of a closed lead can be completed or cancelled by hand."""
        lead = _in_stage(mgr, emp, p, "contacted")
        f = create_followup(emp, lead["id"])
        set_stage(emp, lead["id"], "lost", lost_reason="delayed_decision")
        assert create_followup(emp, lead["id"], due_at=later(days=180), notes="call back in six months")
        assert create_call(emp, lead["id"], result="call_back")
        act(emp, "followups", f["id"], "cancel", {"note": "the lead is lost"})
        assert set_stage(mgr, lead["id"], "assigned", note="reopened")["pipeline_stage"] == "assigned"           # reopening works, items intact
        assert listing(emp, f"/api/sales/followups?lead_id={lead['id']}&status=pending")["total"] == 1
        won = _in_stage(mgr, emp, p, "negotiation")
        agree(emp, won)                                                                                           # the salesperson's win: the Customer Setup
        assert create_followup(emp, won["id"], notes="check they are happy")

    def test_reassigning_a_lead_moves_who_can_see_its_work_but_not_who_authored_it(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        call = create_call(emp, lead["id"], notes="mine")
        f = create_followup(emp, lead["id"])
        d = create_demo(emp, lead["id"])
        assign(mgr, lead["id"], p["employee2"]["id"])                                                              # -> employee2
        new_owner = p["employee2"]["headers"]
        for kind, item in (("calls", call), ("followups", f), ("demos", d)):
            assert api("GET", f"/api/sales/{kind}/{item['id']}", emp).status_code == 403, kind                    # the old owner is out
            assert api("GET", f"/api/sales/{kind}/{item['id']}", new_owner).status_code == 200, kind              # the new owner is in
        assert fetch(new_owner, "calls", call["id"])["employee"]["id"] == p["employee"]["id"]                    # the call is still the old owner's
        assert fetch(new_owner, "followups", f["id"])["assigned_to"]["id"] == p["employee"]["id"]                # the follow-up still names them ...
        assert api("PATCH", f"/api/sales/followups/{f['id']}", mgr, {"assigned_to": p["employee2"]["id"]}).status_code == 200   # ... until a manager hands it over
        assert act(new_owner, "followups", f["id"], "complete")["status"] == "completed"
        assert api("POST", f"/api/sales/followups/{f['id']}/cancel", emp).status_code == 403

    def test_unassigning_a_lead_takes_its_work_out_of_the_employees_reach(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        f = create_followup(emp, lead["id"])
        assert api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr).status_code == 200
        assert stage_of(mgr, lead["id"]) == "new"                                                                  # Phase-2 rule, untouched by the work on it
        assert api("GET", f"/api/sales/followups/{f['id']}", emp).status_code == 403
        assert fetch(mgr, "followups", f["id"])["status"] == "pending"                                             # nothing was cancelled or lost
        assign(mgr, lead["id"], p["employee"]["id"])
        assert api("GET", f"/api/sales/followups/{f['id']}", emp).status_code == 200

    def test_archiving_a_lead_hides_its_work_from_the_lists_and_freezes_it(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        call, f = create_call(emp, lead["id"]), create_followup(emp, lead["id"], due_at=ago(days=1))
        d, t = create_demo(emp, lead["id"]), create_trial(emp, lead["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        for kind, item in (("calls", call), ("followups", f), ("demos", d), ("trials", t)):
            assert item["id"] not in ids_of(listing(emp, f"/api/sales/{kind}?limit=100&q={lead['business_name'].split()[-1]}"))
            assert ids_of(listing(emp, f"/api/sales/{kind}?lead_id={lead['id']}")) == [item["id"]]                # still readable on the lead itself
        r = api("POST", f"/api/sales/leads/{lead['id']}/stage", emp, {"stage": "contacted"})
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"                                    # the Phase-2 rule, unchanged
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert set(work_types(mgr, lead["id"])) == {"call_created", "followup_created", "demo_scheduled", "trial_started"}
        assert set_stage(emp, lead["id"], "contacted")["pipeline_stage"] == "contacted"

    def test_the_lead_listing_and_stage_counts_are_not_affected_by_its_work(self, p, mgr, emp):
        lead = worked_lead(mgr, p["employee"]["id"])
        token = lead["business_name"].split()[-1]
        before = listing(emp, f"/api/sales/leads/stage-counts?q={token}")
        _everything(emp, lead["id"])
        listed = listing(emp, f"/api/sales/leads?q={token}")
        assert [i["id"] for i in listed["items"]] == [lead["id"]] and listed["items"][0]["pipeline_stage"] == "assigned"
        assert listing(emp, f"/api/sales/leads/stage-counts?q={token}") == before
