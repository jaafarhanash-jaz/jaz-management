"""JAZ Sales - Phase 4: the onboarding workflow (HTTP integration tier).

Won -> Assigned -> Contacted -> Setup Started -> Company Configured -> Employees Added -> Training -> Activated:
assigning (and reassigning) an onboarding employee - who can and cannot receive one - the stage machine, the notes, the
customer status that follows the stage, the record's own timeline, the list / filters / counts, and what an Onboarding
Employee can and cannot reach. Who may do what per role is covered by test_sales_customers_rbac.py; this module
concentrates on behavior.
"""
import concurrent.futures

import pytest

from sales_customer_test_utils import (
    legacy_win,
    ONBOARDING_EVENTS,
    PHANTOM_ID,
    api,
    activities,
    assign,
    assign_onboarding,
    convert,
    converted,
    create_lead,
    create_staff,
    customer,
    db_row,
    deactivate,
    errors_of,
    listing,
    make_custom_role,
    make_personas,
    onboarding,
    onboarding_id,
    onboarding_timeline,
    onboarding_worker,
    onboarding_worker_role,
    phase4_events,
    reactivate,
    retire_roles,
    set_onboarding_stage,
    set_stage,
    started,
    uniq,
    won_lead,
)
from sales_test_utils import admin_token, assert_scratch_target, auth, lookup_user, owner_token, owner_user  # noqa: F401

STAGES = ["won", "assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated"]
WORKING = STAGES[2:7]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "ob")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture()
def fresh(p, mgr):
    """A converted customer with an UNASSIGNED onboarding: {"lead", "customer", "body", "onboarding_id"}."""
    made = converted(mgr, p["employee"]["id"])
    return {**made, "onboarding_id": onboarding_id(mgr, made["customer"]["id"])}


@pytest.fixture()
def owned(p, mgr, admin_h):
    """A converted customer whose onboarding is assigned to a fresh onboarding worker."""
    return started(mgr, admin_h, p["employee"]["id"])


def _status(mgr, customer_id):
    return customer(mgr, customer_id)["status"]


# =============================================================================
class TestTheRecordStartsAtWon:
    def test_conversion_starts_an_unowned_onboarding(self, fresh, mgr):
        o = onboarding(mgr, fresh["onboarding_id"])
        assert set(o) == {"id", "stage", "assigned_to", "notes", "started_at", "completed_at", "created_at", "updated_at",
                          "customer", "company", "lead_id", "allowed_stages", "can"}
        assert (o["stage"], o["assigned_to"], o["notes"], o["completed_at"]) == ("won", None, None, None)
        assert o["started_at"] and o["customer"]["id"] == fresh["customer"]["id"] and o["customer"]["status"] == "active"
        assert o["customer"]["business_name"] == fresh["lead"]["business_name"]

    def test_nothing_can_move_before_somebody_owns_it(self, fresh, mgr):
        assert onboarding(mgr, fresh["onboarding_id"])["allowed_stages"] == []
        for stage in ["assigned", "contacted", "activated"]:
            r = api("POST", f"/api/sales/onboarding/{fresh['onboarding_id']}/stage", mgr, {"stage": stage})
            assert r.status_code == 409 and errors_of(r)["code"] == "onboarding_assignee_required", (stage, r.text)
        assert onboarding(mgr, fresh["onboarding_id"])["stage"] == "won"

    def test_exactly_one_onboarding_per_customer(self, fresh, mgr):
        page = listing(mgr, f"/api/sales/onboarding?q={fresh['lead']['business_name']}")
        assert page["total"] == 1 and page["items"][0]["id"] == fresh["onboarding_id"]


# =============================================================================
class TestAssign:
    def test_the_first_assignment_takes_it_out_of_won(self, fresh, mgr, admin_h):
        worker = onboarding_worker(admin_h, "a1")
        o = assign_onboarding(mgr, fresh["onboarding_id"], worker["id"], note="please call them today")
        assert o["stage"] == "assigned" and o["assigned_to"] == {"id": worker["id"], "name": "Test p4-a1", "status": "active"}
        assert _status(mgr, fresh["customer"]["id"]) == "onboarding"                                   # the customer follows the stage
        assert o["allowed_stages"] == ["assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated"][1:]

    def test_the_assignment_is_on_the_timeline_with_before_and_after(self, fresh, mgr, admin_h, p):
        worker = onboarding_worker(admin_h, "a2")
        assign_onboarding(mgr, fresh["onboarding_id"], worker["id"], note="welcome call")
        (event,) = [e for e in activities(mgr, fresh["lead"]["id"]) if e["event_type"] == "onboarding_assigned"]
        assert event["actor"]["id"] == p["manager"]["id"] and event["note"] == "welcome call"
        assert event["before"] == {"assigned_to": None, "stage": "won", "customer_status": "active"}
        assert event["after"] == {"assigned_to": {"id": worker["id"], "name": "Test p4-a2"}, "stage": "assigned", "customer_status": "onboarding"}
        assert event["metadata"]["onboarding_id"] == fresh["onboarding_id"] and event["metadata"]["customer_id"] == fresh["customer"]["id"]
        assert event["metadata"]["reassigned"] is False and event["metadata"]["actor_roles"] == ["sales_manager"]

    def test_reassigning_changes_only_the_owner(self, mgr, admin_h, owned):
        other = onboarding_worker(admin_h, "a3")
        set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "training")
        o = assign_onboarding(mgr, owned["onboarding_id"], other["id"], note="handing over")
        assert o["assigned_to"]["id"] == other["id"] and o["stage"] == "training"                       # the stage is untouched
        assert _status(mgr, owned["customer"]["id"]) == "onboarding"
        event = [e for e in activities(mgr, owned["lead"]["id"]) if e["event_type"] == "onboarding_assigned"][-1]             # the latest one
        assert event["metadata"]["reassigned"] is True and event["before"]["assigned_to"]["id"] == owned["worker"]["id"]
        assert event["before"]["stage"] == "training" and event["after"]["stage"] == "training"

    def test_the_previous_owner_loses_access_and_the_new_one_gains_it(self, mgr, admin_h, owned):
        other = onboarding_worker(admin_h, "a4")
        assert onboarding(owned["worker"]["headers"], owned["onboarding_id"])["stage"] == "assigned"
        assign_onboarding(mgr, owned["onboarding_id"], other["id"])
        onboarding(owned["worker"]["headers"], owned["onboarding_id"], expect=403)
        assert onboarding(other["headers"], owned["onboarding_id"])["assigned_to"]["id"] == other["id"]

    def test_assigning_the_same_person_again_writes_nothing(self, mgr, owned):
        before = phase4_events(mgr, owned["lead"]["id"])
        o = assign_onboarding(mgr, owned["onboarding_id"], owned["worker"]["id"])
        assert o["assigned_to"]["id"] == owned["worker"]["id"] and phase4_events(mgr, owned["lead"]["id"]) == before

    def test_an_unknown_record_is_404_and_a_bad_body_is_refused(self, fresh, mgr, admin_h):
        worker = onboarding_worker(admin_h, "a5")
        assert api("POST", f"/api/sales/onboarding/{PHANTOM_ID}/assign", mgr, {"assigned_to": worker["id"]}).status_code == 404
        assert api("POST", "/api/sales/onboarding/not-a-uuid/assign", mgr, {"assigned_to": worker["id"]}).status_code == 404
        assert api("POST", f"/api/sales/onboarding/{fresh['onboarding_id']}/assign", mgr, {}).status_code == 422
        assert api("POST", f"/api/sales/onboarding/{fresh['onboarding_id']}/assign", mgr, {"assigned_to": "nope"}).status_code == 422
        assert api("POST", f"/api/sales/onboarding/{fresh['onboarding_id']}/assign", mgr, {"assigned_to": worker["id"], "stage": "activated"}).status_code == 422
        assert onboarding(mgr, fresh["onboarding_id"])["assigned_to"] is None


# =============================================================================
class TestWhoCanReceiveAnOnboarding:
    """A deactivated / revoked / wrong-role person cannot be given a NEW onboarding - and every refusal looks the same."""

    def _refused(self, mgr, oid, who_id):
        r = api("POST", f"/api/sales/onboarding/{oid}/assign", mgr, {"assigned_to": who_id})
        assert r.status_code == 400, r.text
        detail = errors_of(r)
        assert detail["field"] == "assigned_to" and detail["code"] == "onboarding_assignee_not_eligible"
        assert onboarding(mgr, oid)["assigned_to"] is None                                              # nothing changed

    def test_a_deactivated_employee_cannot_receive_one_and_can_again_once_reactivated(self, fresh, mgr, admin_h):
        worker = onboarding_worker(admin_h, "d1")
        deactivate(admin_h, worker["id"])
        self._refused(mgr, fresh["onboarding_id"], worker["id"])
        reactivate(admin_h, worker["id"])
        assert assign_onboarding(mgr, fresh["onboarding_id"], worker["id"])["assigned_to"]["id"] == worker["id"]

    def test_an_employee_whose_role_was_revoked_cannot_receive_one_and_can_again_once_regranted(self, fresh, mgr, admin_h):
        worker = onboarding_worker(admin_h, "r1")
        role = onboarding_worker_role()          # the retired system role cannot be granted: the same keys, through a custom role
        assert api("DELETE", f"/api/sales/team/{worker['id']}/roles/{role}", admin_h).status_code == 200
        self._refused(mgr, fresh["onboarding_id"], worker["id"])
        assert api("POST", f"/api/sales/team/{worker['id']}/roles", admin_h, {"role_key": role}).status_code == 200
        assert assign_onboarding(mgr, fresh["onboarding_id"], worker["id"])["assigned_to"]["id"] == worker["id"]

    def test_wrong_role_accounts_and_made_up_ids_get_the_same_answer(self, fresh, mgr, p, owner_user):
        admin_id = lookup_user("admin@jaz.com")["id"]
        for who in (p["employee"]["id"], p["data_entry"]["id"], p["no_roles"]["id"], p["manager"]["id"], admin_id, owner_user["id"], PHANTOM_ID):
            self._refused(mgr, fresh["onboarding_id"], who)

    def test_a_custom_role_needs_both_the_manage_permission_and_the_assigned_scope(self, fresh, mgr, admin_h):
        roles = []
        try:
            for label, keys, eligible in (
                ("both", ["sales.access", "sales.onboarding.manage", "sales.onboarding.scope_assigned"], True),
                ("no-scope", ["sales.access", "sales.onboarding.manage"], False),
                ("no-manage", ["sales.access", "sales.onboarding.view", "sales.onboarding.scope_assigned"], False),
                ("scope-all-only", ["sales.access", "sales.onboarding.manage", "sales.onboarding.scope_all"], False),
            ):
                role = make_custom_role(keys)
                roles.append(role)
                person = create_staff(admin_h, [role], f"p4-{label}", do_login=False)
                if eligible:
                    continue
                self._refused(mgr, fresh["onboarding_id"], person["id"])
            person = create_staff(admin_h, [roles[0]], "p4-custom-ok", do_login=False)
            assert assign_onboarding(mgr, fresh["onboarding_id"], person["id"])["assigned_to"]["id"] == person["id"]
        finally:
            retire_roles(roles)

    def test_the_assignee_list_is_exactly_those_people(self, mgr, admin_h, p):
        good, gone = onboarding_worker(admin_h, "l1"), onboarding_worker(admin_h, "l2")
        deactivate(admin_h, gone["id"])
        page = api("GET", "/api/sales/onboarding/assignees", mgr).json()
        ids = {a["id"] for a in page}
        assert good["id"] in ids and gone["id"] not in ids
        assert not ids & {p["employee"]["id"], p["data_entry"]["id"], p["manager"]["id"], p["no_roles"]["id"]}
        entry = next(a for a in page if a["id"] == good["id"])
        assert set(entry) == {"id", "name", "email", "open_onboarding"} and entry["open_onboarding"] == 0

    def test_the_open_workload_counts_only_unfinished_records(self, p, mgr, admin_h):
        worker = onboarding_worker(admin_h, "l3")
        a, b = converted(mgr, p["employee"]["id"]), converted(mgr, p["employee"]["id"])
        for made in (a, b):
            assign_onboarding(mgr, onboarding_id(mgr, made["customer"]["id"]), worker["id"])
        load = lambda: next(x for x in api("GET", "/api/sales/onboarding/assignees", mgr).json() if x["id"] == worker["id"])["open_onboarding"]  # noqa: E731
        assert load() == 2
        set_onboarding_stage(worker["headers"], onboarding_id(mgr, a["customer"]["id"]), "activated")
        assert load() == 1

    def test_the_records_of_a_deactivated_employee_stay_put_and_can_be_handed_over(self, mgr, admin_h, owned):
        deactivate(admin_h, owned["worker"]["id"])
        o = onboarding(mgr, owned["onboarding_id"])
        assert o["assigned_to"] == {"id": owned["worker"]["id"], "name": "Test p4-w", "status": "inactive"} and o["stage"] == "assigned"
        other = onboarding_worker(admin_h, "h1")
        assert assign_onboarding(mgr, owned["onboarding_id"], other["id"])["assigned_to"]["id"] == other["id"]

    def test_a_deactivated_employee_cannot_touch_their_records(self, admin_h, owned):
        deactivate(admin_h, owned["worker"]["id"])
        assert api("GET", f"/api/sales/onboarding/{owned['onboarding_id']}", owned["worker"]["headers"]).status_code == 403
        assert api("POST", f"/api/sales/onboarding/{owned['onboarding_id']}/stage", owned["worker"]["headers"], {"stage": "contacted"}).status_code == 403


# =============================================================================
class TestStages:
    def test_every_working_stage_is_reachable_from_every_other_in_both_directions(self, mgr, owned):
        headers, oid = owned["worker"]["headers"], owned["onboarding_id"]
        path = ["contacted", "setup_started", "company_configured", "employees_added", "training", "employees_added", "contacted", "training", "setup_started"]
        for stage in path:
            assert set_onboarding_stage(headers, oid, stage)["stage"] == stage

    def test_skipping_stages_is_allowed(self, owned):
        assert set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "training")["stage"] == "training"

    def test_the_same_stage_is_a_conflict(self, owned):
        r = api("POST", f"/api/sales/onboarding/{owned['onboarding_id']}/stage", owned["worker"]["headers"], {"stage": "assigned"})
        assert r.status_code == 409 and errors_of(r)["code"] == "onboarding_same_stage"

    def test_won_can_never_be_chosen(self, owned):
        r = api("POST", f"/api/sales/onboarding/{owned['onboarding_id']}/stage", owned["worker"]["headers"], {"stage": "won"})
        assert r.status_code == 409 and errors_of(r)["code"] == "onboarding_transition_not_allowed"
        assert onboarding(owned["worker"]["headers"], owned["onboarding_id"])["stage"] == "assigned"

    @pytest.mark.parametrize("bad", ["", "done", "Training", "lost", None, 3])
    def test_an_unknown_stage_is_refused(self, owned, bad):
        assert api("POST", f"/api/sales/onboarding/{owned['onboarding_id']}/stage", owned["worker"]["headers"], {"stage": bad}).status_code == 422

    def test_extra_fields_are_refused(self, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        for extra in ({"assigned_to": PHANTOM_ID}, {"completed_at": "2026-01-01T00:00:00Z"}, {"customer_id": PHANTOM_ID}, {"status": "activated"}):
            assert api("POST", f"/api/sales/onboarding/{oid}/stage", h, {"stage": "contacted", **extra}).status_code == 422

    def test_the_customer_status_follows_the_stage(self, mgr, owned):
        h, oid, cid = owned["worker"]["headers"], owned["onboarding_id"], owned["customer"]["id"]
        assert _status(mgr, cid) == "onboarding"
        for stage, expected in (("contacted", "onboarding"), ("training", "onboarding"), ("activated", "activated"), ("training", "onboarding"), ("activated", "activated")):
            set_onboarding_stage(h, oid, stage)
            assert _status(mgr, cid) == expected, stage

    def test_activating_records_the_completion_time_and_reopening_clears_it(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        done = set_onboarding_stage(h, oid, "activated")
        assert done["completed_at"] is not None and done["customer"]["status"] == "activated"
        reopened = set_onboarding_stage(h, oid, "training")
        assert reopened["completed_at"] is None and reopened["customer"]["status"] == "onboarding"
        assert set_onboarding_stage(h, oid, "activated")["completed_at"] is not None                     # and can finish again

    def test_allowed_stages_mirror_the_rules_for_the_caller(self, mgr, owned):
        o = onboarding(mgr, owned["onboarding_id"])
        assert o["stage"] == "assigned" and o["allowed_stages"] == STAGES[2:]                            # everything but itself and `won`
        o = set_onboarding_stage(mgr, owned["onboarding_id"], "training")
        assert o["allowed_stages"] == ["assigned", "contacted", "setup_started", "company_configured", "employees_added", "activated"]

    def test_a_stage_change_is_on_the_timeline_with_the_customer_status(self, p, mgr, owned):
        set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "activated", note="all set up")
        event = [e for e in activities(mgr, owned["lead"]["id"]) if e["event_type"] == "onboarding_stage_changed"][-1]
        assert event["actor"]["id"] == owned["worker"]["id"] and event["note"] == "all set up"
        assert event["before"] == {"stage": "assigned", "customer_status": "onboarding", "completed_at": None}
        assert event["after"]["stage"] == "activated" and event["after"]["customer_status"] == "activated" and event["after"]["completed_at"]
        assert event["metadata"]["actor_roles"] == [onboarding_worker_role()]

    def test_the_database_holds_the_same_state(self, mgr, owned):
        from sales.models import SalesCustomer, SalesOnboarding
        import uuid
        set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "employees_added")
        row = db_row(SalesOnboarding, id=uuid.UUID(owned["onboarding_id"]))
        assert row["stage"] == "employees_added" and str(row["assigned_to"]) == owned["worker"]["id"] and row["completed_at"] is None
        assert db_row(SalesCustomer, id=uuid.UUID(owned["customer"]["id"]))["status"] == "onboarding"

    def test_concurrent_stage_changes_are_applied_one_after_the_other(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        stages = ["contacted", "setup_started", "company_configured", "employees_added", "training", "activated"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda s: api("POST", f"/api/sales/onboarding/{oid}/stage", h, {"stage": s}), stages))
        assert all(r.status_code in (200, 409) for r in results), [r.text[:120] for r in results]
        final = onboarding(mgr, oid)
        events = [e for e in activities(mgr, owned["lead"]["id"]) if e["event_type"] == "onboarding_stage_changed"]
        assert len(events) == sum(r.status_code == 200 for r in results)                                # one event per accepted change ...
        chain = ["assigned"] + [e["after"]["stage"] for e in events]
        assert all(events[i]["before"]["stage"] == chain[i] for i in range(len(events)))                # ... and each one's `before` is the previous `after`
        assert final["stage"] == chain[-1] and final["customer"]["status"] == ("activated" if final["stage"] == "activated" else "onboarding")


# =============================================================================
class TestNotes:
    def test_set_change_and_clear(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "Owner prefers WhatsApp"}).json()["notes"] == "Owner prefers WhatsApp"
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "  Owner prefers calls  "}).json()["notes"] == "Owner prefers calls"      # trimmed
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": None}).json()["notes"] is None
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": ""}).status_code == 200            # blank = clear (already clear)

    def test_each_change_is_on_the_timeline_and_an_unchanged_save_is_not(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "first"})
        api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "first"})                                    # same value: nothing happens
        api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "second"})
        events = [e for e in activities(mgr, owned["lead"]["id"]) if e["event_type"] == "onboarding_notes_updated"]
        assert [(e["before"], e["after"]) for e in events] == [({"notes": None}, {"notes": "first"}), ({"notes": "first"}, {"notes": "second"})]

    def test_the_limits(self, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "x" * 5000}).status_code == 200
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "x" * 5001}).status_code == 422
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {}).status_code == 400                         # nothing to change
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"stage": "activated"}).status_code == 422    # the stage has its own endpoint
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"assigned_to": PHANTOM_ID}).status_code == 422

    def test_a_long_note_is_clipped_on_the_timeline_but_kept_whole_on_the_record(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        long = "n" * 2000
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": long}).json()["notes"] == long
        event = [e for e in activities(mgr, owned["lead"]["id"]) if e["event_type"] == "onboarding_notes_updated"][-1]
        assert len(event["after"]["notes"]) < 400

    def test_notes_can_be_kept_on_a_finished_record(self, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        set_onboarding_stage(h, oid, "activated")
        assert api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "post-launch: all good"}).status_code == 200


# =============================================================================
class TestTimeline:
    def test_the_whole_story_in_order(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        set_onboarding_stage(h, oid, "contacted")
        api("PATCH", f"/api/sales/onboarding/{oid}", h, {"notes": "called"})
        set_onboarding_stage(h, oid, "activated")
        assert [e["event_type"] for e in onboarding_timeline(mgr, oid)] == [
            "onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated", "onboarding_stage_changed",
        ]
        assert phase4_events(mgr, owned["lead"]["id"]) == ["lead_converted"] + [
            "onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated", "onboarding_stage_changed",
        ]

    def test_events_are_immutable_and_carry_no_secrets(self, mgr, owned):
        import json
        set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "contacted", note="ok")
        blob = json.dumps(activities(mgr, owned["lead"]["id"])).lower()
        for secret in ("password", "$2b$", "token", "hash"):
            assert secret not in blob, secret

    def test_an_onboarding_employee_reads_only_the_onboarding_events(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        set_onboarding_stage(h, oid, "training")
        types = {e["event_type"] for e in onboarding_timeline(h, oid)}
        assert types == {"onboarding_assigned", "onboarding_stage_changed"} and types <= set(ONBOARDING_EVENTS)
        assert "lead_converted" not in types and not any(t.startswith(("lead_", "stage_", "call_")) for t in types)

    def test_pagination(self, mgr, owned):
        h, oid = owned["worker"]["headers"], owned["onboarding_id"]
        for stage in ("contacted", "training", "activated"):
            set_onboarding_stage(h, oid, stage)
        page = api("GET", f"/api/sales/onboarding/{oid}/timeline?limit=2&offset=1", mgr).json()
        assert page["total"] == 4 and page["limit"] == 2 and page["offset"] == 1 and len(page["items"]) == 2
        assert api("GET", f"/api/sales/onboarding/{oid}/timeline?limit=0", mgr).status_code == 422

    def test_someone_elses_timeline_is_403_and_an_unknown_one_404(self, mgr, admin_h, owned):
        stranger = onboarding_worker(admin_h, "t1")
        assert api("GET", f"/api/sales/onboarding/{owned['onboarding_id']}/timeline", stranger["headers"]).status_code == 403
        assert api("GET", f"/api/sales/onboarding/{PHANTOM_ID}/timeline", mgr).status_code == 404

    def test_the_sales_employee_sees_the_conversion_but_not_the_onboarding_on_their_lead(self, p, mgr, owned):
        set_onboarding_stage(owned["worker"]["headers"], owned["onboarding_id"], "contacted")
        seen = phase4_events(p["employee"]["headers"], owned["lead"]["id"])
        assert seen == ["lead_converted"]                                                                       # customers.view yes, onboarding.view no

    def test_lead_data_entry_sees_neither_on_a_lead_they_created(self, p, mgr, admin_h):
        lead = create_lead(p["data_entry"]["headers"])                                                          # THEIR lead (in their intake scope) ...
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "contacted")
        legacy_win(mgr, lead["id"])                                                                             # (a lead won before the no-manual-win rule)
        customer_id = convert(mgr, lead)["customer"]["id"]
        assign_onboarding(mgr, onboarding_id(mgr, customer_id), onboarding_worker(admin_h, "e1")["id"])       # so onboarding events exist as well
        seen = {e["event_type"] for e in activities(p["data_entry"]["headers"], lead["id"])}
        assert seen and not seen & {"lead_converted", *ONBOARDING_EVENTS}                                       # ... converted by the manager: they read the lead's own history only
        assert "lead_created" in seen and "lead_marked_won" in seen
@pytest.fixture(scope="module")
def world(p, mgr, admin_h):
    """Six customers, all with one search token in their business name, spread over owners and stages."""
    token = f"P4LIST{uniq(8)}"
    a, b = onboarding_worker(admin_h, "f1"), onboarding_worker(admin_h, "f2")
    rows = []
    for i in range(6):
        lead = won_lead(mgr, p["employee"]["id"], business_name=f"{token} Shop {i}")
        c = convert(mgr, lead)["customer"]
        rows.append({"lead": lead, "customer": c, "oid": c["onboarding"]["id"]})
    for row, worker in ((rows[0], a), (rows[1], a), (rows[2], b), (rows[3], b)):                            # rows 4 and 5 stay unassigned
        assign_onboarding(mgr, row["oid"], worker["id"])
    set_onboarding_stage(a["headers"], rows[0]["oid"], "training")
    set_onboarding_stage(a["headers"], rows[1]["oid"], "activated")
    set_onboarding_stage(b["headers"], rows[2]["oid"], "contacted")
    return {"token": token, "rows": rows, "a": a, "b": b}


# =============================================================================
class TestListFiltersAndCounts:
    def _ids(self, headers, world, query=""):
        page = listing(headers, f"/api/sales/onboarding?q={world['token']}&limit=100{query}")
        return page, [i["id"] for i in page["items"]]

    def test_the_manager_sees_all_of_them(self, mgr, world):
        page, ids = self._ids(mgr, world)
        assert page["total"] == 6 and set(ids) == {r["oid"] for r in world["rows"]}

    def test_filter_by_stage(self, mgr, world):
        r = world["rows"]
        assert self._ids(mgr, world, "&stage=won")[1] and set(self._ids(mgr, world, "&stage=won")[1]) == {r[4]["oid"], r[5]["oid"]}
        assert self._ids(mgr, world, "&stage=training")[1] == [r[0]["oid"]]
        assert set(self._ids(mgr, world, "&stage=training,activated")[1]) == {r[0]["oid"], r[1]["oid"]}
        assert api("GET", "/api/sales/onboarding?stage=nope", mgr).status_code == 400

    def test_filter_by_owner_unassigned_and_open(self, mgr, world):
        r, a = world["rows"], world["a"]
        assert set(self._ids(mgr, world, f"&assigned_to={a['id']}")[1]) == {r[0]["oid"], r[1]["oid"]}
        assert set(self._ids(mgr, world, "&unassigned=true")[1]) == {r[4]["oid"], r[5]["oid"]}
        assert r[1]["oid"] not in self._ids(mgr, world, "&open=true")[1] and len(self._ids(mgr, world, "&open=true")[1]) == 5
        assert api("GET", "/api/sales/onboarding?assigned_to=nope", mgr).status_code == 400

    def test_my_onboarding_is_assigned_to_me_and_open(self, world):
        a = world["a"]
        page = listing(a["headers"], "/api/sales/onboarding?assigned_to=me&open=true&limit=100")
        assert {i["id"] for i in page["items"]} >= {world["rows"][0]["oid"]} and world["rows"][1]["oid"] not in {i["id"] for i in page["items"]}
        assert all(i["assigned_to"]["id"] == a["id"] and i["stage"] != "activated" for i in page["items"])

    def test_search_by_business_or_contact_name(self, mgr, world):
        r = world["rows"]
        assert self._ids(mgr, world)[0]["total"] == 6
        page = listing(mgr, f"/api/sales/onboarding?q={world['token']}%20Shop%203")
        assert [i["id"] for i in page["items"]] == [r[3]["oid"]]
        assert listing(mgr, f"/api/sales/onboarding?q={r[2]['lead']['contact_name']}&limit=100")["total"] >= 1
        assert listing(mgr, "/api/sales/onboarding?q=%25%5F")["total"] == 0                                    # LIKE wildcards are literal

    def test_sorting_and_paging(self, mgr, world):
        _, newest_first = self._ids(mgr, world)
        _, oldest_first = self._ids(mgr, world, "&order=asc")
        assert newest_first == list(reversed(oldest_first))
        page = listing(mgr, f"/api/sales/onboarding?q={world['token']}&limit=4&offset=4&order=asc")
        assert page["total"] == 6 and [i["id"] for i in page["items"]] == oldest_first[4:]
        by_name = [i["customer"]["business_name"] for i in listing(mgr, f"/api/sales/onboarding?q={world['token']}&sort=business_name&order=asc&limit=100")["items"]]
        assert by_name == sorted(by_name, key=str.lower)
        assert api("GET", "/api/sales/onboarding?sort=nope", mgr).status_code == 422
        assert api("GET", "/api/sales/onboarding?limit=101", mgr).status_code == 422

    def test_counts_match_the_lists(self, mgr, world):
        counts = listing(mgr, f"/api/sales/onboarding/counts?q={world['token']}")
        assert counts["total"] == 6 and counts["unassigned"] == 2 and counts["open"] == 5
        assert counts["stages"] == {"won": 2, "assigned": 1, "contacted": 1, "setup_started": 0, "company_configured": 0, "employees_added": 0, "training": 1, "activated": 1}
        assert set(counts) == {"total", "open", "unassigned", "mine", "stages"} and set(counts["stages"]) == set(STAGES)
        assert counts["mine"] == 0                                                                              # the manager owns none of them
        mine = listing(world["a"]["headers"], f"/api/sales/onboarding/counts?q={world['token']}")
        assert (mine["total"], mine["mine"], mine["unassigned"]) == (2, 1, 0)                                   # scope: only the two assigned to worker a

    def test_counts_ignore_the_stage_and_owner_filters(self, mgr, world):
        assert listing(mgr, f"/api/sales/onboarding/counts?q={world['token']}&stage=training&unassigned=true")["total"] == 6


@pytest.fixture(scope="module")
def setup(p, mgr, admin_h):
    mine = started(mgr, admin_h, p["employee"]["id"], "s1")
    theirs = started(mgr, admin_h, p["employee"]["id"], "s2")
    unowned = converted(mgr, p["employee"]["id"])
    return {"mine": mine, "theirs": theirs, "unowned": {**unowned, "onboarding_id": onboarding_id(mgr, unowned["customer"]["id"])}, "h": mine["worker"]["headers"]}


# =============================================================================
class TestWhatAnOnboardingEmployeeReaches:
    def test_only_their_own_records_are_listed(self, setup):
        page = listing(setup["h"], "/api/sales/onboarding?limit=100")
        assert {i["id"] for i in page["items"]} == {setup["mine"]["onboarding_id"]}

    def test_they_can_open_and_work_their_own(self, setup):
        o = onboarding(setup["h"], setup["mine"]["onboarding_id"])
        assert o["can"] == {"update": True, "change_stage": True, "assign": False, "view_customer": False, "view_lead": False}
        assert set_onboarding_stage(setup["h"], setup["mine"]["onboarding_id"], "contacted")["stage"] == "contacted"
        assert api("PATCH", f"/api/sales/onboarding/{setup['mine']['onboarding_id']}", setup["h"], {"notes": "on it"}).status_code == 200

    def test_they_get_the_contact_details_but_not_the_company_or_the_lead(self, setup):
        o = onboarding(setup["h"], setup["mine"]["onboarding_id"])
        assert o["customer"]["contact_name"] and o["customer"]["phone"] and o["customer"]["email"]             # what they need to do the work
        assert o["company"] is None and o["lead_id"] is None
        assert setup["mine"]["customer"]["company"]["id"] not in api("GET", f"/api/sales/onboarding/{setup['mine']['onboarding_id']}", setup["h"]).text
        assert setup["mine"]["lead"]["id"] not in api("GET", f"/api/sales/onboarding/{setup['mine']['onboarding_id']}", setup["h"]).text

    def test_other_peoples_and_unowned_records_are_403(self, setup):
        for other in (setup["theirs"]["onboarding_id"], setup["unowned"]["onboarding_id"]):
            assert api("GET", f"/api/sales/onboarding/{other}", setup["h"]).status_code == 403
            assert api("GET", f"/api/sales/onboarding/{other}/timeline", setup["h"]).status_code == 403
            assert api("PATCH", f"/api/sales/onboarding/{other}", setup["h"], {"notes": "x"}).status_code == 403
            assert api("POST", f"/api/sales/onboarding/{other}/stage", setup["h"], {"stage": "contacted"}).status_code == 403

    def test_they_cannot_assign_or_list_assignees(self, setup, admin_h):
        someone = onboarding_worker(admin_h, "s3")
        assert api("POST", f"/api/sales/onboarding/{setup['mine']['onboarding_id']}/assign", setup["h"], {"assigned_to": someone["id"]}).status_code == 403
        assert api("GET", "/api/sales/onboarding/assignees", setup["h"]).status_code == 403

    def test_they_reach_no_lead_no_customer_no_company(self, setup):
        h, lead_id, customer_id = setup["h"], setup["mine"]["lead"]["id"], setup["mine"]["customer"]["id"]
        for method, path in (
            ("GET", "/api/sales/leads"), ("GET", f"/api/sales/leads/{lead_id}"), ("GET", f"/api/sales/leads/{lead_id}/activities"),
            ("GET", f"/api/sales/leads/{lead_id}/conversion"), ("POST", f"/api/sales/leads/{lead_id}/convert/preflight"),
            ("GET", "/api/sales/customers"), ("GET", f"/api/sales/customers/{customer_id}"), ("GET", "/api/sales/followups"),
            ("GET", "/api/sales/calls"), ("GET", "/api/sales/demos"), ("GET", "/api/sales/trials"), ("GET", "/api/sales/campaigns"),
        ):
            assert api(method, path, h, {} if method == "POST" else None).status_code == 403, path
        assert api("GET", "/api/owner/employees", h).status_code == 403                                         # ... and no tenant (company) API either
        assert api("GET", "/api/owner/dashboard", h).status_code == 403

    def test_their_workspace_shows_onboarding_and_nothing_else(self, setup):
        me = api("GET", "/api/sales/me", setup["h"]).json()
        # simplified workflow: onboarding is no longer a section (the role is dormant); the API below still serves their records
        assert me["modules"] == ["home", "reports"]
        assert {k for k in me["permissions"] if k != "sales.access"} == {
            "sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned",
            "sales.dashboard.view", "sales.reports.view",                 # Phase 5: the dashboard / reports doors - no lead, work-item or customer permission behind them
        }

    def test_a_manager_and_the_super_admin_still_see_everything(self, p, mgr, admin_h, setup):
        for headers in (mgr, admin_h):
            for key in ("mine", "theirs", "unowned"):
                assert onboarding(headers, setup[key]["onboarding_id"])["id"] == setup[key]["onboarding_id"]
        o = onboarding(mgr, setup["theirs"]["onboarding_id"])
        assert o["company"]["id"] == setup["theirs"]["customer"]["company"]["id"] and o["lead_id"] == setup["theirs"]["lead"]["id"]
        assert o["can"]["assign"] is True and o["can"]["view_customer"] is True and o["can"]["view_lead"] is True
