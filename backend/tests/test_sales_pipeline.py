"""JAZ Sales - Phase 2: the sales pipeline (HTTP integration tier).

Stage changes are validated on the server, every change is recorded (who / when / before / after), Lost is an explicit
state that always carries a reason, and nobody can reach a stage - or set a lost reason - any other way. `won` is NOT a manual
move for ANYBODY (Sales Employee, Sales Manager or Super Admin): a lead is won only by the Customer Setup, which creates its
customer in the same transaction (test_sales_workflow.py has the per-role proof; here the transition table, the terminal state and
the happy path). The expected transition table is written out here independently of the implementation.
"""
import concurrent.futures

import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    activities,
    assign,
    create_campaign,
    create_lead,
    errors_of,
    get_lead,
    lead_body,
    legacy_win,
    make_personas,
    set_stage,
)
from sales_customer_test_utils import agree
from sales_test_utils import admin_token, api, assert_scratch_target, auth  # noqa: F401  (admin_token: mints instead of logging in)

STAGES = ["new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won", "lost"]
WORKING = ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation"]
LOST_REASONS = ["too_expensive", "not_interested", "already_using_another_system", "no_response", "wrong_number",
                "business_closed", "not_suitable", "delayed_decision", "competitor", "other"]

# The specification, stage by stage: where a lead in that stage may be moved BY HAND (owner present). `won` is nobody's manual
# target - it is reached only through the Customer Setup - so it appears in no row; a lead that is won stays won.
ALLOWED = {
    "new": {"lost"},
    "assigned": set(WORKING) | {"lost"},
    **{stage: (set(WORKING) - {stage}) | {"lost"} for stage in WORKING},
    "won": set(),
    "lost": {"assigned"},          # reopening: back to `assigned` while it has an owner (`new` when it has none)
}


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


def lead_in(mgr, p, stage, *, owner="employee"):
    """A fresh lead sitting in `stage` (with an owner, except `new`), reached through legitimate operations. A `won` lead is one that
    was won by hand BEFORE the rule that ended manual wins (seeded: sales_lead_test_utils.legacy_win)."""
    lead = create_lead(mgr)
    if stage == "new":
        return lead
    assign(mgr, lead["id"], p[owner]["id"])
    if stage == "assigned":
        return get_lead(mgr, lead["id"])
    if stage == "won":
        set_stage(mgr, lead["id"], "contacted")
        legacy_win(mgr, lead["id"])
        return get_lead(mgr, lead["id"])
    if stage == "lost":
        return set_stage(mgr, lead["id"], "lost", lost_reason="no_response")
    return set_stage(mgr, lead["id"], stage)


def _move(h, lead_id, stage, **extra):
    return api("POST", f"/api/sales/leads/{lead_id}/stage", h, {"stage": stage, **extra})


def _stage_chain_is_consistent(events, final_stage):
    """Oldest -> newest, each stage_changed `before` must be the previous `after`; the last `after` is the lead's stage."""
    current = "new"
    for event in events:
        if event["event_type"] != "stage_changed":
            continue
        assert event["before"]["pipeline_stage"] == current, event
        current = event["after"]["pipeline_stage"]
    assert current == final_stage


# =============================================================================
class TestTheHappyPath:
    def test_a_lead_walks_the_whole_pipeline_to_won(self, p, mgr):
        """The salesperson walks it stage by stage; the last step is the Customer Setup ("Agreed"), which wins the lead in the same
        transaction that creates its customer. `won` is offered to nobody, whoever asks."""
        emp = p["employee"]["headers"]
        lead = create_lead(mgr)
        assert (lead["pipeline_stage"], lead["allowed_stages"]) == ("new", ["lost"])
        assign(mgr, lead["id"], p["employee"]["id"])
        for stage in ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation"]:
            r = _move(emp, lead["id"], stage, note=f"moved to {stage}")
            assert r.status_code == 200, (stage, r.text)
            body = r.json()
            assert body["pipeline_stage"] == stage
            assert set(body["allowed_stages"]) == ALLOWED[stage] and "won" not in body["allowed_stages"]
            assert body["closed_at"] is None
        for headers in (emp, mgr):                                                    # not to the salesperson, not to the manager
            assert "won" not in get_lead(headers, lead["id"])["allowed_stages"]
        agree(emp, get_lead(mgr, lead["id"]))                                          # "Agreed": the customer, the company - and the win
        final = get_lead(mgr, lead["id"])
        assert final["pipeline_stage"] == "won" and final["lost_reason"] is None and final["allowed_stages"] == []
        assert final["closed_at"] is not None

        events = activities(mgr, lead["id"])
        stage_events = [e for e in events if e["event_type"] == "stage_changed"]
        assert [e["after"]["pipeline_stage"] for e in stage_events] == ["assigned", "contacted", "interested", "demo_scheduled",
                                                                       "demo_completed", "trial", "negotiation", "won"]
        for event in stage_events[1:-1]:                                           # the employee's own moves
            assert event["actor"]["id"] == p["employee"]["id"] and event["note"] == f"moved to {event['after']['pipeline_stage']}"
        assert stage_events[0]["metadata"]["automatic"] is True                    # new -> assigned came from the assignment
        assert [e["event_type"] for e in events][-3:] == ["stage_changed", "lead_marked_won", "lead_converted"]
        won = events[-2]
        assert won["before"] == {"pipeline_stage": "negotiation"} and won["after"] == {"pipeline_stage": "won"}
        assert won["actor"]["id"] == p["employee"]["id"] and won["metadata"]["cause"] == "customer_setup"
        _stage_chain_is_consistent(events, "won")

    def test_who_and_when_are_recorded_and_ordered(self, p, mgr):
        lead = lead_in(mgr, p, "assigned")
        set_stage(p["employee"]["headers"], lead["id"], "contacted")
        set_stage(mgr, lead["id"], "interested")                                    # a manager may move it too
        events = activities(mgr, lead["id"])
        moves = [e for e in events if e["event_type"] == "stage_changed"][-2:]
        assert [m["actor"]["id"] for m in moves] == [p["employee"]["id"], p["manager"]["id"]]
        assert [m["actor"]["name"] for m in moves] == ["Test pipe-employee", "Test pipe-manager"]
        assert [e["occurred_at"] for e in events] == sorted(e["occurred_at"] for e in events)
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events) and len({e["seq"] for e in events}) == len(events)


# =============================================================================
class TestTransitionTable:
    """Every (from, to) pair against the specification above - a fresh lead each time."""

    @pytest.mark.parametrize("current", STAGES)
    def test_every_target_from_every_stage(self, p, mgr, current):
        for target in STAGES:
            lead = lead_in(mgr, p, current)
            before = get_lead(mgr, lead["id"])
            events_before = len(activities(mgr, lead["id"]))
            extra = {"lost_reason": "not_suitable"} if target == "lost" else {}
            r = _move(mgr, lead["id"], target, **extra)
            if target == "won":                                                     # never a manual move - even for the manager
                assert r.status_code == 403, f"{current} -> won must be refused: {r.status_code} {r.text[:200]}"
                detail = errors_of(r)
                assert detail["field"] == "stage" and detail["code"] == "won_requires_customer_setup"
                assert get_lead(mgr, lead["id"]) == before                          # a refused move changes nothing...
                assert len(activities(mgr, lead["id"])) == events_before                             # ...and records nothing
            elif target in ALLOWED[current]:
                assert r.status_code == 200, f"{current} -> {target} should be allowed: {r.status_code} {r.text[:200]}"
                assert r.json()["pipeline_stage"] == target
            else:
                assert r.status_code == 409, f"{current} -> {target} should be refused: {r.status_code} {r.text[:200]}"
                detail = errors_of(r)
                assert detail["field"] == "stage"
                assert detail["code"] in ("same_stage", "transition_not_allowed", "unassign_required", "assignee_required")
                assert get_lead(mgr, lead["id"]) == before                          # a refused move changes nothing...
                assert len(activities(mgr, lead["id"])) == events_before                             # ...and records nothing

    def test_moving_to_the_stage_it_is_already_in_is_refused(self, p, mgr):
        lead = lead_in(mgr, p, "trial")
        r = _move(mgr, lead["id"], "trial")
        assert r.status_code == 409 and errors_of(r)["code"] == "same_stage"

    def test_new_and_assigned_are_managed_by_assignment_not_by_hand(self, p, mgr):
        lead = lead_in(mgr, p, "contacted")
        for target in ("new", "assigned"):
            assert _move(mgr, lead["id"], target).status_code == 409
        fresh = create_lead(mgr)
        assert _move(mgr, fresh["id"], "contacted").status_code == 409            # nobody owns it yet: it has to be assigned first
        assert errors_of(_move(mgr, fresh["id"], "assigned"))["code"] == "transition_not_allowed"

    def test_a_lead_nobody_owns_can_only_be_closed(self, p, mgr):
        lead = lead_in(mgr, p, "interested")
        api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr)
        for target in ("contacted", "demo_scheduled"):
            r = _move(mgr, lead["id"], target)
            assert r.status_code == 409 and errors_of(r)["code"] == "assignee_required", target
        assert _move(mgr, lead["id"], "won").status_code == 403                    # ... and never won by hand, owner or not
        assert get_lead(mgr, lead["id"])["allowed_stages"] == ["lost"]
        assert _move(mgr, lead["id"], "lost", lost_reason="business_closed").status_code == 200

    def test_won_is_never_a_manual_move_not_even_from_the_last_working_stage(self, p, mgr):
        for stage in ("assigned", "negotiation"):
            r = _move(mgr, lead_in(mgr, p, stage)["id"], "won")
            assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup", stage

    def test_stage_names_outside_the_pipeline_are_rejected(self, p, mgr):
        lead = create_lead(mgr)
        for bogus in ("closed", "WON", "Won ", "", "converted"):
            assert _move(mgr, lead["id"], bogus).status_code == 422


# =============================================================================
class TestLost:
    def test_a_lost_reason_is_required(self, p, mgr):
        lead = lead_in(mgr, p, "contacted")
        r = _move(mgr, lead["id"], "lost")
        assert r.status_code == 400 and errors_of(r)["code"] == "lost_reason_required" and errors_of(r)["field"] == "lost_reason"
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "contacted"

    @pytest.mark.parametrize("reason", LOST_REASONS)
    def test_every_listed_reason_is_accepted_and_stored(self, p, mgr, reason):
        lead = lead_in(mgr, p, "interested")
        r = _move(mgr, lead["id"], "lost", lost_reason=reason, note=f"because {reason}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pipeline_stage"] == "lost" and body["lost_reason"] == reason and body["closed_at"] is not None
        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events][-2:] == ["stage_changed", "lead_marked_lost"]
        marked = events[-1]
        assert marked["after"] == {"pipeline_stage": "lost", "lost_reason": reason} and marked["before"] == {"pipeline_stage": "interested"}
        assert marked["note"] == f"because {reason}"
        assert events[-2]["after"] == {"pipeline_stage": "lost", "lost_reason": reason} and events[-2]["before"] == {"pipeline_stage": "interested", "lost_reason": None}

    @pytest.mark.parametrize("reason", ["not-a-reason", "Too_Expensive", "", "price", "closed"])
    def test_an_unknown_reason_is_rejected(self, p, mgr, reason):
        lead = lead_in(mgr, p, "contacted")
        assert _move(mgr, lead["id"], "lost", lost_reason=reason).status_code == 422
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "contacted"

    @pytest.mark.parametrize("target", ["interested", "negotiation"])
    def test_only_a_lost_lead_may_carry_a_reason(self, p, mgr, target):
        lead = lead_in(mgr, p, "contacted")
        r = _move(mgr, lead["id"], target, lost_reason="other")
        assert r.status_code == 400 and errors_of(r)["code"] == "lost_reason_not_allowed"
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "contacted" and get_lead(mgr, lead["id"])["lost_reason"] is None

    def test_a_lead_can_be_closed_from_new_without_an_owner(self, p, mgr):
        lead = create_lead(mgr)
        r = _move(mgr, lead["id"], "lost", lost_reason="wrong_number")
        assert r.status_code == 200 and r.json()["assigned_to"] is None and r.json()["pipeline_stage"] == "lost"

    def test_the_lost_reason_cannot_be_set_or_changed_any_other_way(self, p, mgr):
        lost = lead_in(mgr, p, "lost")
        assert api("PATCH", f"/api/sales/leads/{lost['id']}", mgr, {"lost_reason": "other"}).status_code == 422
        assert api("PATCH", f"/api/sales/leads/{lost['id']}", mgr, {"pipeline_stage": "won"}).status_code == 422
        assert api("POST", "/api/sales/leads", mgr, lead_body(pipeline_stage="lost", lost_reason="other")).status_code == 422
        assert get_lead(mgr, lost["id"])["lost_reason"] == "no_response"
        assert _move(mgr, lost["id"], "lost", lost_reason="other").status_code == 409     # already lost: no silent re-labelling

    def test_reopening_a_lost_lead_clears_the_reason_and_the_close_time(self, p, mgr):
        lost = lead_in(mgr, p, "lost")
        assert lost["lost_reason"] == "no_response" and lost["closed_at"] is not None and lost["allowed_stages"] == ["assigned"]
        reopened = _move(mgr, lost["id"], "assigned", note="customer called back")
        assert reopened.status_code == 200
        body = reopened.json()
        assert body["pipeline_stage"] == "assigned" and body["lost_reason"] is None and body["closed_at"] is None
        last = activities(mgr, lost["id"])[-1]
        assert last["event_type"] == "stage_changed" and last["metadata"]["reopened"] is True and last["note"] == "customer called back"
        assert last["before"] == {"pipeline_stage": "lost", "lost_reason": "no_response"}
        assert last["after"] == {"pipeline_stage": "assigned", "lost_reason": None}
        # ...and it works again from there
        assert _move(mgr, lost["id"], "contacted").status_code == 200

    def test_an_unowned_lost_lead_reopens_to_new_and_an_owned_one_cannot_(self, p, mgr):
        unowned = create_lead(mgr)
        _move(mgr, unowned["id"], "lost", lost_reason="not_interested")
        assert get_lead(mgr, unowned["id"])["allowed_stages"] == ["new"]
        assert _move(mgr, unowned["id"], "assigned").status_code == 409            # nobody to be `assigned` to
        assert _move(mgr, unowned["id"], "new").status_code == 200

        owned = lead_in(mgr, p, "lost")
        r = _move(mgr, owned["id"], "new")
        assert r.status_code == 409 and errors_of(r)["code"] == "unassign_required"

    @pytest.mark.parametrize("target", ["contacted", "trial"])
    def test_a_lost_lead_cannot_jump_straight_back_into_the_pipeline(self, p, mgr, target):
        lost = lead_in(mgr, p, "lost")
        assert _move(mgr, lost["id"], target).status_code == 409
        assert _move(mgr, lost["id"], "won").status_code == 403                     # ... let alone to won


class TestWon:
    def test_a_lead_won_by_the_customer_setup_is_stamped_and_terminal(self, p, mgr):
        lead = lead_in(mgr, p, "negotiation")
        agree(p["employee"]["headers"], lead)
        won = get_lead(mgr, lead["id"])
        assert won["pipeline_stage"] == "won" and won["closed_at"] is not None and won["lost_reason"] is None
        assert won["allowed_stages"] == []
        self._nothing_leaves_won(mgr, won)

    def test_a_lead_won_by_hand_before_the_rule_is_just_as_terminal(self, p, mgr):
        won = lead_in(mgr, p, "won")                                               # seeded: the historical state
        assert won["pipeline_stage"] == "won" and won["closed_at"] is not None and won["allowed_stages"] == []
        self._nothing_leaves_won(mgr, won)

    @staticmethod
    def _nothing_leaves_won(mgr, won):
        for target in STAGES:
            extra = {"lost_reason": "other"} if target == "lost" else {}
            r = _move(mgr, won["id"], target, **extra)
            assert r.status_code == (403 if target == "won" else 409), target
        assert get_lead(mgr, won["id"]) == won

    def test_a_won_lead_still_belongs_to_its_owner_and_can_be_archived(self, p, mgr):
        won = lead_in(mgr, p, "won")
        assert api("GET", f"/api/sales/leads/{won['id']}", p["employee"]["headers"]).status_code == 200
        assert api("DELETE", f"/api/sales/leads/{won['id']}", mgr).status_code == 200


# =============================================================================
class TestWhoMayMoveALead:
    def test_the_owner_moves_it_and_others_cannot(self, p, mgr):
        lead = lead_in(mgr, p, "assigned", owner="employee")
        assert _move(p["employee2"]["headers"], lead["id"], "contacted").status_code == 403       # someone else's lead
        assert _move(p["data_entry"]["headers"], lead["id"], "contacted").status_code == 403       # no pipeline permission
        assert _move(p["onboarding"]["headers"], lead["id"], "contacted").status_code == 403
        assert _move(p["no_roles"]["headers"], lead["id"], "contacted").status_code == 403
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "assigned"
        assert _move(p["employee"]["headers"], lead["id"], "contacted").status_code == 200

    def test_an_employee_cannot_reach_a_stage_by_editing_the_lead(self, p, mgr):
        lead = lead_in(mgr, p, "assigned")
        h = p["employee"]["headers"]
        for forbidden in ({"pipeline_stage": "won"}, {"lost_reason": "other"}, {"closed_at": "2026-01-01T00:00:00Z"}, {"assigned_to": p["employee"]["id"]}):
            assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, forbidden).status_code == 422
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "assigned"

    def test_bad_requests(self, p, mgr):
        lead = create_lead(mgr)
        url = f"/api/sales/leads/{lead['id']}/stage"
        assert api("POST", url, mgr, {}).status_code == 422
        assert api("POST", url, mgr, {"stage": "lost", "lost_reason": "other", "extra": 1}).status_code == 422
        assert api("POST", url, mgr, {"stage": "lost", "lost_reason": "other", "note": "n" * 1001}).status_code == 422
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/stage", mgr, {"stage": "lost", "lost_reason": "other"}).status_code == 404
        assert api("POST", "/api/sales/leads/garbage/stage", mgr, {"stage": "lost", "lost_reason": "other"}).status_code == 404
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "new"


# =============================================================================
class TestPipelineViews:
    def test_stage_counts_follow_the_moves(self, p, mgr):
        campaign = create_campaign(mgr)
        make = lambda: create_lead(mgr, campaign_id=campaign["id"])  # noqa: E731
        a, b, c, d = make(), make(), make(), make()
        for lead in (a, b, c):
            assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, b["id"], "contacted")
        set_stage(mgr, c["id"], "contacted")
        legacy_win(mgr, c["id"])                                                  # (a lead won before the rule that ended manual wins)
        set_stage(mgr, d["id"], "lost", lost_reason="other")
        counts = api("GET", f"/api/sales/leads/stage-counts?campaign_id={campaign['id']}", mgr).json()["counts"]
        assert counts == {"new": 0, "assigned": 1, "contacted": 1, "interested": 0, "demo_scheduled": 0, "demo_completed": 0,
                          "trial": 0, "negotiation": 0, "won": 1, "lost": 1}

    def test_filtering_the_list_by_stage_finds_the_moved_lead(self, p, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "demo_scheduled")
        found = api("GET", f"/api/sales/leads?campaign_id={campaign['id']}&pipeline_stage=demo_scheduled", mgr).json()
        assert [i["id"] for i in found["items"]] == [lead["id"]] and found["items"][0]["allowed_stages"] == [
            "contacted", "interested", "demo_completed", "trial", "negotiation", "lost"]


# =============================================================================
class TestConcurrency:
    def _burst(self, calls):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(lambda call: call(), calls))

    def test_parallel_moves_to_the_same_stage_apply_once(self, p, mgr):
        lead = lead_in(mgr, p, "assigned")
        results = self._burst([lambda: _move(mgr, lead["id"], "contacted")] * 8)
        codes = sorted(r.status_code for r in results)
        assert codes == [200] + [409] * 7, codes
        assert all(errors_of(r)["code"] == "same_stage" for r in results if r.status_code == 409)
        assert [e["event_type"] for e in activities(mgr, lead["id"])].count("stage_changed") == 2   # the automatic one + this one

    def test_racing_different_moves_leave_a_history_the_lead_agrees_with(self, p, mgr):
        lead = lead_in(mgr, p, "assigned")
        targets = ["contacted", "interested", "trial", "negotiation", "lost", "demo_scheduled", "contacted", "interested"]
        calls = [lambda t=t: _move(mgr, lead["id"], t, **({"lost_reason": "other"} if t == "lost" else {})) for t in targets]
        results = self._burst(calls)
        assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
        assert any(r.status_code == 200 for r in results)
        final = get_lead(mgr, lead["id"])
        _stage_chain_is_consistent(activities(mgr, lead["id"]), final["pipeline_stage"])
        assert (final["lost_reason"] is not None) == (final["pipeline_stage"] == "lost")
        assert (final["closed_at"] is not None) == (final["pipeline_stage"] in ("won", "lost"))

    def test_a_move_racing_an_unassignment_ends_in_a_state_the_history_explains(self, p, mgr):
        lead = lead_in(mgr, p, "assigned")
        calls = []
        for _ in range(4):
            calls.append(lambda: _move(mgr, lead["id"], "contacted"))
            calls.append(lambda: api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr))
        results = self._burst(calls)
        assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
        final = get_lead(mgr, lead["id"])
        # every unassignment ran, so nobody owns it; it is `new` if the unassignment beat every move, else `contacted`
        assert final["assigned_to"] is None and final["pipeline_stage"] in ("new", "contacted")
        _stage_chain_is_consistent(activities(mgr, lead["id"]), final["pipeline_stage"])
