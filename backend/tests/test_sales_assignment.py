"""JAZ Sales - Phase 2: lead assignment (HTTP integration tier).

Assign, reassign, unassign, bulk assign, who may RECEIVE an assignment (deactivated / revoked / wrong-role people
must not), the manager's workload view, and race-safety. Every assignment change is checked against the lead's
immutable timeline as well as against the lead itself.
"""
import concurrent.futures

import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    activities,
    assign,
    create_lead,
    errors_of,
    event_types,
    get_lead,
    make_custom_role,
    make_personas,
    retire_roles,
    set_stage,
)
from sales_test_utils import (  # noqa: F401  (the token fixtures mint instead of logging in)
    admin_token,
    api,
    assert_scratch_target,
    auth,
    create_staff,
    employee_token,
    employee_user,
    owner_token,
    owner_user,
)


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "asg")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


def _assign_raw(h, lead_id, assignee_id):
    return api("POST", f"/api/sales/leads/{lead_id}/assign", h, {"assigned_to": assignee_id})


def _unassign(h, lead_id):
    r = api("POST", f"/api/sales/leads/{lead_id}/unassign", h)
    assert r.status_code == 200, r.text
    return r.json()


# =============================================================================
class TestAssignReassignUnassign:
    def test_assigning_a_new_lead_moves_it_to_the_assigned_stage(self, p, mgr):
        lead = create_lead(mgr)
        assert lead["assigned_to"] is None and lead["pipeline_stage"] == "new"
        result = assign(mgr, lead["id"], p["employee"]["id"])
        assert result["changed"] is True
        after = result["lead"]
        assert after["pipeline_stage"] == "assigned" and after["assigned_at"] is not None
        assert after["assigned_to"] == {"id": p["employee"]["id"], "name": "Test asg-employee", "status": "active"}
        assert get_lead(mgr, lead["id"]) == after

        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["lead_created", "lead_assigned", "stage_changed"]
        assigned, staged = events[1], events[2]
        assert assigned["actor"]["id"] == p["manager"]["id"]
        assert assigned["before"] == {"assigned_to": None}
        assert assigned["after"] == {"assigned_to": {"id": p["employee"]["id"], "name": "Test asg-employee"}}
        assert staged["before"] == {"pipeline_stage": "new"} and staged["after"] == {"pipeline_stage": "assigned"}
        assert staged["metadata"]["automatic"] is True and staged["metadata"]["cause"] == "assignment"

    def test_assigning_to_the_current_owner_is_a_quiet_no_op(self, p, mgr):
        lead = create_lead(mgr)
        first = assign(mgr, lead["id"], p["employee"]["id"])["lead"]
        again = assign(mgr, lead["id"], p["employee"]["id"])
        assert again["changed"] is False and again["lead"]["assigned_at"] == first["assigned_at"]
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_assigned", "stage_changed"]   # nothing new recorded

    def test_reassigning_records_who_lost_and_who_got_the_lead(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "contacted")
        result = assign(mgr, lead["id"], p["employee2"]["id"])
        assert result["changed"] is True
        assert result["lead"]["assigned_to"]["id"] == p["employee2"]["id"]
        assert result["lead"]["pipeline_stage"] == "contacted"                       # the stage is the salesperson's progress: it stays
        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events] == ["lead_created", "lead_assigned", "stage_changed", "stage_changed", "lead_reassigned"]
        assert events[-1]["before"]["assigned_to"]["id"] == p["employee"]["id"]
        assert events[-1]["after"]["assigned_to"]["id"] == p["employee2"]["id"]      # and no automatic stage move after it

    def test_reassigning_at_the_assigned_stage_keeps_the_stage(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        after = assign(mgr, lead["id"], p["employee2"]["id"])["lead"]
        assert after["pipeline_stage"] == "assigned"
        assert event_types(mgr, lead["id"])[-1] == "lead_reassigned"

    def test_unassigning_returns_an_assigned_lead_to_new(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        result = _unassign(mgr, lead["id"])
        assert result["changed"] is True
        after = result["lead"]
        assert after["assigned_to"] is None and after["assigned_at"] is None and after["pipeline_stage"] == "new"
        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events][-2:] == ["lead_unassigned", "stage_changed"]
        assert events[-2]["before"]["assigned_to"]["id"] == p["employee"]["id"] and events[-2]["after"] == {"assigned_to": None}
        assert events[-1]["metadata"]["automatic"] is True and events[-1]["metadata"]["cause"] == "unassignment"

    def test_unassigning_twice_changes_nothing_the_second_time(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        _unassign(mgr, lead["id"])
        before = event_types(mgr, lead["id"])
        assert _unassign(mgr, lead["id"])["changed"] is False
        assert event_types(mgr, lead["id"]) == before
        assert _unassign(mgr, create_lead(mgr)["id"])["changed"] is False                      # never assigned at all

    def test_unassigning_a_lead_in_progress_keeps_its_stage(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "interested")
        after = _unassign(mgr, lead["id"])["lead"]
        assert after["assigned_to"] is None and after["pipeline_stage"] == "interested"
        assert after["allowed_stages"] == ["lost"]                          # nobody owns it: it can only be closed until reassigned
        # created, assigned + its automatic stage move, the manual move to `interested`, then just the unassignment
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_assigned", "stage_changed", "stage_changed", "lead_unassigned"]

    def test_a_closed_lead_can_be_reassigned_without_changing_its_stage(self, p, mgr):
        lost = create_lead(mgr)
        assign(mgr, lost["id"], p["employee"]["id"])
        set_stage(mgr, lost["id"], "lost", lost_reason="no_response")
        after = assign(mgr, lost["id"], p["employee2"]["id"])["lead"]
        assert after["pipeline_stage"] == "lost" and after["lost_reason"] == "no_response" and after["assigned_to"]["id"] == p["employee2"]["id"]

    def test_the_owner_can_be_replaced_many_times_and_the_timeline_tells_the_whole_story(self, p, mgr):
        lead = create_lead(mgr)
        chain = [p["employee"]["id"], p["employee2"]["id"], p["employee"]["id"]]
        for who in chain:
            assign(mgr, lead["id"], who)
        _unassign(mgr, lead["id"])
        events = [e for e in activities(mgr, lead["id"]) if e["event_type"].startswith("lead_") and "assign" in e["event_type"]]
        assert [e["event_type"] for e in events] == ["lead_assigned", "lead_reassigned", "lead_reassigned", "lead_unassigned"]
        owners = [None] + chain + [None]
        for event, (old, new) in zip(events, zip(owners, owners[1:])):
            assert (event["before"]["assigned_to"] or {}).get("id") == old
            assert (event["after"]["assigned_to"] or {}).get("id") == new

    def test_bad_requests(self, p, mgr):
        lead = create_lead(mgr)
        url = f"/api/sales/leads/{lead['id']}/assign"
        assert api("POST", url, mgr, {}).status_code == 422
        assert api("POST", url, mgr, {"assigned_to": "not-a-uuid"}).status_code == 422
        assert api("POST", url, mgr, {"assigned_to": p["employee"]["id"], "extra": 1}).status_code == 422
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/assign", mgr, {"assigned_to": p["employee"]["id"]}).status_code == 404
        assert api("POST", "/api/sales/leads/garbage/assign", mgr, {"assigned_to": p["employee"]["id"]}).status_code == 404
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/unassign", mgr).status_code == 404
        assert get_lead(mgr, lead["id"])["assigned_to"] is None


# =============================================================================
class TestAssigneeEligibility:
    """Only an ACTIVE staff account whose ACTIVE role works assigned leads can receive an assignment."""

    def _refused(self, mgr, lead, assignee_id):
        r = _assign_raw(mgr, lead["id"], assignee_id)
        assert r.status_code == 400, r.text
        detail = errors_of(r)
        assert detail["field"] == "assigned_to" and detail["code"] == "assignee_not_eligible"
        return detail

    @pytest.mark.parametrize("who", ["manager", "data_entry", "onboarding", "no_roles"])
    def test_staff_who_do_not_work_assigned_leads_cannot_receive_them(self, p, mgr, who):
        lead = create_lead(mgr)
        self._refused(mgr, lead, p[who]["id"])
        assert get_lead(mgr, lead["id"])["assigned_to"] is None and event_types(mgr, lead["id"]) == ["lead_created"]

    def test_super_admin_and_customers_cannot_receive_them(self, p, mgr, admin_h, owner_user, employee_user):
        lead = create_lead(mgr)
        me = api("GET", "/api/sales/me", admin_h).json()["user"]["id"]
        for target in (me, owner_user["id"], employee_user["id"], PHANTOM_ID):
            self._refused(mgr, lead, target)

    def test_a_deactivated_employee_cannot_receive_a_lead_and_can_again_once_reactivated(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "asg-deactivated", do_login=False)
        lead = create_lead(mgr)
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"}).status_code == 200
        self._refused(mgr, lead, worker["id"])
        assert get_lead(mgr, lead["id"])["assigned_to"] is None
        assert api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "active"}).status_code == 200
        assert assign(mgr, lead["id"], worker["id"])["changed"] is True

    def test_an_employee_whose_role_was_revoked_cannot_receive_a_lead_and_can_again_once_regranted(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "asg-revoked", do_login=False)
        lead = create_lead(mgr)
        assert api("DELETE", f"/api/sales/team/{worker['id']}/roles/sales_employee", admin_h).status_code == 200
        self._refused(mgr, lead, worker["id"])
        assert api("POST", f"/api/sales/team/{worker['id']}/roles", admin_h, {"role_key": "sales_employee"}).status_code == 200
        assert assign(mgr, lead["id"], worker["id"])["changed"] is True

    def test_a_retired_role_stops_granting_eligibility(self, admin_h, mgr):
        role = make_custom_role(["sales.access", "sales.leads.view", "sales.leads.scope_assigned"])
        try:
            person = create_staff(admin_h, [role], "asg-custom", do_login=False)
            lead = create_lead(mgr)
            assert assign(mgr, lead["id"], person["id"])["changed"] is True            # permission-driven: any role holding the scope qualifies
            retire_roles([role])
            self._refused(mgr, create_lead(mgr), person["id"])
        finally:
            retire_roles([role])

    def test_a_manager_who_also_holds_the_employee_role_can_be_assigned_leads(self, admin_h, mgr):
        both = create_staff(admin_h, ["sales_manager", "sales_employee"], "asg-both", do_login=False)
        assert assign(mgr, create_lead(mgr)["id"], both["id"])["changed"] is True

    def test_existing_leads_of_a_deactivated_employee_stay_put_and_can_be_reassigned(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "asg-leaves", do_login=False)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], worker["id"])
        api("PATCH", f"/api/sales/team/{worker['id']}", admin_h, {"status": "inactive"})
        shown = get_lead(mgr, lead["id"])
        assert shown["assigned_to"]["id"] == worker["id"] and shown["assigned_to"]["status"] == "inactive"
        assert assign(mgr, lead["id"], p["employee"]["id"])["lead"]["assigned_to"]["id"] == p["employee"]["id"]


# =============================================================================
class TestAssigneesEndpoint:
    def test_lists_exactly_the_people_who_can_receive_leads_with_their_open_workload(self, admin_h, mgr):
        fresh = create_staff(admin_h, ["sales_employee"], "asg-workload", do_login=False)
        for stage in (None, "contacted", "negotiation"):                           # 3 open leads
            lead = create_lead(mgr)
            assign(mgr, lead["id"], fresh["id"])
            if stage:
                set_stage(mgr, lead["id"], stage)
        won = create_lead(mgr)
        assign(mgr, won["id"], fresh["id"])
        set_stage(mgr, won["id"], "contacted")
        set_stage(mgr, won["id"], "won")                                            # closed: not workload
        lost = create_lead(mgr)
        assign(mgr, lost["id"], fresh["id"])
        set_stage(mgr, lost["id"], "lost", lost_reason="wrong_number")              # closed: not workload
        archived = create_lead(mgr)
        assign(mgr, archived["id"], fresh["id"])
        api("DELETE", f"/api/sales/leads/{archived['id']}", mgr)                    # archived: not workload

        r = api("GET", "/api/sales/assignees", mgr)
        assert r.status_code == 200
        rows = {row["id"]: row for row in r.json()}
        assert rows[fresh["id"]]["open_leads"] == 3
        assert set(rows[fresh["id"]]) == {"id", "name", "email", "open_leads"}      # nothing else about the person

    def test_excludes_everyone_who_cannot_receive_a_lead(self, admin_h, p, mgr):
        gone = create_staff(admin_h, ["sales_employee"], "asg-list-inactive", do_login=False)
        revoked = create_staff(admin_h, ["sales_employee"], "asg-list-revoked", do_login=False)
        api("PATCH", f"/api/sales/team/{gone['id']}", admin_h, {"status": "inactive"})
        api("DELETE", f"/api/sales/team/{revoked['id']}/roles/sales_employee", admin_h)
        ids = {row["id"] for row in api("GET", "/api/sales/assignees", mgr).json()}
        assert {p["employee"]["id"], p["employee2"]["id"]} <= ids
        for excluded in (gone["id"], revoked["id"], p["manager"]["id"], p["data_entry"]["id"], p["onboarding"]["id"], p["no_roles"]["id"]):
            assert excluded not in ids

    def test_is_sorted_by_name(self, mgr):
        names = [row["name"] for row in api("GET", "/api/sales/assignees", mgr).json()]
        assert names == sorted(names)

    def test_workload_moves_with_assignments(self, admin_h, p, mgr):
        worker = create_staff(admin_h, ["sales_employee"], "asg-moving", do_login=False)
        count = lambda: {r["id"]: r["open_leads"] for r in api("GET", "/api/sales/assignees", mgr).json()}[worker["id"]]  # noqa: E731
        assert count() == 0
        lead = create_lead(mgr)
        assign(mgr, lead["id"], worker["id"])
        assert count() == 1
        assign(mgr, lead["id"], p["employee"]["id"])
        assert count() == 0


# =============================================================================
class TestBulkAssign:
    def _bulk(self, h, ids, assignee, expect=200):
        r = api("POST", "/api/sales/leads/bulk-assign", h, {"lead_ids": ids, "assigned_to": assignee})
        assert r.status_code == expect, r.text
        return r

    def test_assigns_a_mixed_batch_and_counts_what_really_changed(self, p, mgr):
        fresh = [create_lead(mgr) for _ in range(3)]
        owned_by_other = create_lead(mgr)
        assign(mgr, owned_by_other["id"], p["employee2"]["id"])
        already_mine = create_lead(mgr)
        assign(mgr, already_mine["id"], p["employee"]["id"])
        in_progress = create_lead(mgr)
        assign(mgr, in_progress["id"], p["employee2"]["id"])
        set_stage(mgr, in_progress["id"], "trial")

        ids = [x["id"] for x in (*fresh, owned_by_other, already_mine, in_progress)]
        body = self._bulk(mgr, ids, p["employee"]["id"]).json()
        assert body == {"total": 6, "assigned": 5, "unchanged": 1}
        for lead_id in ids:
            assert get_lead(mgr, lead_id)["assigned_to"]["id"] == p["employee"]["id"]
        assert get_lead(mgr, fresh[0]["id"])["pipeline_stage"] == "assigned"          # new -> assigned, like a single assignment
        assert get_lead(mgr, in_progress["id"])["pipeline_stage"] == "trial"          # progress is kept
        assert event_types(mgr, fresh[0]["id"]) == ["lead_created", "lead_assigned", "stage_changed"]
        assert event_types(mgr, owned_by_other["id"])[-1] == "lead_reassigned"
        assert event_types(mgr, already_mine["id"]) == ["lead_created", "lead_assigned", "stage_changed"]   # unchanged: no extra event

    def test_repeated_ids_count_once(self, p, mgr):
        lead = create_lead(mgr)
        assert self._bulk(mgr, [lead["id"], lead["id"], lead["id"]], p["employee"]["id"]).json() == {"total": 1, "assigned": 1, "unchanged": 0}
        assert event_types(mgr, lead["id"]).count("lead_assigned") == 1

    def test_one_missing_lead_fails_the_whole_batch(self, p, mgr):
        leads = [create_lead(mgr) for _ in range(3)]
        r = self._bulk(mgr, [leads[0]["id"], PHANTOM_ID, leads[1]["id"]], p["employee"]["id"], expect=404)
        detail = errors_of(r)
        assert detail["code"] == "leads_not_found" and detail["missing"] == [PHANTOM_ID]
        assert all(get_lead(mgr, lead["id"])["assigned_to"] is None for lead in leads)     # all-or-nothing

    def test_one_archived_lead_fails_the_whole_batch(self, p, mgr):
        a, b = create_lead(mgr), create_lead(mgr)
        api("DELETE", f"/api/sales/leads/{b['id']}", mgr)
        r = self._bulk(mgr, [a["id"], b["id"]], p["employee"]["id"], expect=409)
        assert errors_of(r)["code"] == "lead_archived" and errors_of(r)["archived"] == [b["id"]]
        assert get_lead(mgr, a["id"])["assigned_to"] is None

    def test_an_ineligible_assignee_fails_the_whole_batch(self, p, mgr):
        leads = [create_lead(mgr) for _ in range(2)]
        for who in ("manager", "data_entry", "onboarding"):
            r = self._bulk(mgr, [lead["id"] for lead in leads], p[who]["id"], expect=400)
            assert errors_of(r)["code"] == "assignee_not_eligible"
        assert all(get_lead(mgr, lead["id"])["assigned_to"] is None for lead in leads)

    @pytest.mark.parametrize("payload", [
        {"lead_ids": [], "assigned_to": PHANTOM_ID},
        {"lead_ids": ["not-a-uuid"], "assigned_to": PHANTOM_ID},
        {"lead_ids": [PHANTOM_ID]},
        {"lead_ids": [PHANTOM_ID] * 101, "assigned_to": PHANTOM_ID},
        {"lead_ids": [PHANTOM_ID], "assigned_to": PHANTOM_ID, "confirm": True},
    ])
    def test_bad_payloads_are_422(self, mgr, payload):
        assert api("POST", "/api/sales/leads/bulk-assign", mgr, payload).status_code == 422

    def test_a_larger_batch_in_one_call(self, p, mgr):
        ids = [create_lead(mgr)["id"] for _ in range(20)]
        body = self._bulk(mgr, ids, p["employee2"]["id"]).json()
        assert body["total"] == 20 and body["assigned"] == 20


# =============================================================================
def _assignment_chain_is_consistent(events, final_assignee):
    """Walking the timeline oldest -> newest, every event's `before` must equal the previous event's `after`, and the
    last `after` must be what the lead really holds: proof that concurrent operations were applied one at a time."""
    owner = None
    for event in events:
        if event["event_type"] not in ("lead_assigned", "lead_reassigned", "lead_unassigned"):
            continue
        assert (event["before"]["assigned_to"] or {}).get("id") == owner, event
        owner = (event["after"]["assigned_to"] or {}).get("id")
    assert owner == final_assignee


class TestConcurrency:
    N = 8

    def _burst(self, calls):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(lambda call: call(), calls))

    def test_parallel_assignments_to_the_same_person_change_the_lead_once(self, p, mgr):
        lead = create_lead(mgr)
        results = self._burst([lambda: _assign_raw(mgr, lead["id"], p["employee"]["id"])] * self.N)
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        assert sum(1 for r in results if r.json()["changed"]) == 1
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_assigned", "stage_changed"]

    def test_parallel_assignments_to_different_people_leave_a_consistent_timeline(self, p, mgr):
        lead = create_lead(mgr)
        calls = [lambda w=who: _assign_raw(mgr, lead["id"], p[w]["id"]) for who in ["employee", "employee2"] * (self.N // 2)]
        results = self._burst(calls)
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        final = get_lead(mgr, lead["id"])
        assert final["assigned_to"]["id"] in (p["employee"]["id"], p["employee2"]["id"])
        events = activities(mgr, lead["id"])
        _assignment_chain_is_consistent(events, final["assigned_to"]["id"])
        assert [e["event_type"] for e in events].count("lead_assigned") == 1        # exactly one first assignment, the rest reassignments

    def test_assign_and_unassign_racing_end_in_a_state_the_timeline_explains(self, p, mgr):
        lead = create_lead(mgr)
        calls = []
        for i in range(self.N):
            calls.append(lambda: _assign_raw(mgr, lead["id"], p["employee"]["id"]))
            calls.append(lambda: api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr))
        results = self._burst(calls)
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        final = get_lead(mgr, lead["id"])
        _assignment_chain_is_consistent(activities(mgr, lead["id"]), (final["assigned_to"] or {}).get("id"))
        # the invariant between stage and owner survived the race
        assert (final["pipeline_stage"] == "new") == (final["assigned_to"] is None)

    def test_parallel_bulk_assignments_over_the_same_leads_never_deadlock(self, p, mgr):
        leads = [create_lead(mgr)["id"] for _ in range(6)]
        calls = [lambda w=who, order=order: api("POST", "/api/sales/leads/bulk-assign", mgr, {"lead_ids": order, "assigned_to": p[w]["id"]})
                 for who, order in (("employee", leads), ("employee2", list(reversed(leads))), ("employee", leads[::2] + leads[1::2]),
                                    ("employee2", leads[1::2] + leads[::2]))]
        results = self._burst(calls)
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
        for lead_id in leads:
            final = get_lead(mgr, lead_id)
            _assignment_chain_is_consistent(activities(mgr, lead_id), final["assigned_to"]["id"])
