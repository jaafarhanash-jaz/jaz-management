"""JAZ Sales - Phase 2: the lead activity timeline (HTTP integration tier + direct reads of the table).

An immutable, structured history: what happened, who did it, when, and the relevant before/after values - written in
the same transaction as the change it records, never containing secrets, and never edited or deleted.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    activities,
    assign,
    create_campaign,
    create_lead,
    event_types,
    get_lead,
    lead_body,
    make_personas,
    run_db,
    set_stage,
    uniq,
)
from sales_test_utils import admin_token, api, assert_scratch_target, auth  # noqa: F401  (admin_token: mints instead of logging in)

# every key a before/after payload may carry: lead fields, plus the few derived facts events record about them
LEAD_FIELDS = {
    "business_name", "business_type", "description", "contact_name", "contact_position", "phone", "whatsapp", "email", "website",
    "country", "city", "address", "latitude", "longitude", "source", "campaign_id", "priority", "estimated_value", "notes",
}
PAYLOAD_KEYS = LEAD_FIELDS | {"pipeline_stage", "lost_reason", "assigned_to", "campaign_name", "archived"}
SECRETISH = ("password", "passwd", "token", "secret", "hash", "authorization", "api_key", "credential")


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "act")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _keys(item)


# =============================================================================
class TestEveryEventAnswersWhatWhoWhen:
    def test_the_shape_of_an_event(self, p, mgr):
        lead = create_lead(mgr)
        (event,) = activities(mgr, lead["id"])
        assert set(event) == {"id", "seq", "event_type", "occurred_at", "actor", "before", "after", "note", "metadata"}
        assert event["event_type"] == "lead_created"
        assert event["actor"] == {"id": p["manager"]["id"], "name": "Test act-manager"}          # who
        occurred = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))            # when (recorded by the database clock)
        assert abs(datetime.now(timezone.utc) - occurred) < timedelta(minutes=2)
        assert uuid.UUID(event["id"]) and isinstance(event["seq"], int)
        assert event["metadata"]["actor_roles"] == ["sales_manager"]                              # under what authority

    def test_the_actor_is_whoever_made_the_call(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(p["employee"]["headers"], lead["id"], "contacted")
        api("PATCH", f"/api/sales/leads/{lead['id']}", p["employee"]["headers"], {"notes": "spoke to the owner"})
        events = activities(mgr, lead["id"])
        assert [(e["event_type"], e["actor"]["name"]) for e in events] == [
            ("lead_created", "Test act-manager"), ("lead_assigned", "Test act-manager"), ("stage_changed", "Test act-manager"),
            ("stage_changed", "Test act-employee"), ("lead_updated", "Test act-employee"),
        ]
        assert events[-1]["metadata"]["actor_roles"] == ["sales_employee"]

    def test_a_super_admin_actor_is_recorded_with_no_staff_roles(self, admin_h):
        lead = create_lead(admin_h)
        (event,) = activities(admin_h, lead["id"])
        assert event["actor"]["name"] == "Super Admin" and event["metadata"]["actor_roles"] == []


class TestCreatedEvent:
    def test_records_what_the_lead_started_as(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"], description="long text stays on the lead", notes="so do notes",
                           estimated_value=1200.5, source="referral", priority="high", website=f"https://{uniq()}.example.com")
        (event,) = activities(mgr, lead["id"])
        assert event["before"] is None
        after = event["after"]
        assert after["business_name"] == lead["business_name"] and after["source"] == "referral" and after["priority"] == "high"
        assert after["pipeline_stage"] == "new" and after["estimated_value"] == 1200.5
        assert after["campaign_id"] == campaign["id"] and after["campaign_name"] == campaign["name"]
        assert "description" not in after and "notes" not in after                                # long free text is not copied into history
        assert all(v is not None for v in after.values())                                          # only what was actually set
        assert set(after) <= PAYLOAD_KEYS

    def test_a_lead_created_with_an_owner_records_the_assignment_too(self, p, mgr):
        lead = create_lead(mgr, assigned_to=p["employee"]["id"])
        created, assigned = activities(mgr, lead["id"])
        assert created["after"]["pipeline_stage"] == "assigned"
        assert assigned["event_type"] == "lead_assigned" and assigned["metadata"]["at_creation"] is True
        assert assigned["after"]["assigned_to"]["id"] == p["employee"]["id"]


class TestUpdatedEvent:
    def test_records_only_what_changed_before_and_after(self, mgr):
        lead = create_lead(mgr, priority="low", contact_name="Old Name")
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": "high", "contact_name": "New Name", "city": lead["city"]})   # city: unchanged
        event = activities(mgr, lead["id"])[-1]
        assert event["event_type"] == "lead_updated"
        assert event["before"] == {"priority": "low", "contact_name": "Old Name"}
        assert event["after"] == {"priority": "high", "contact_name": "New Name"}
        assert event["metadata"]["fields"] == ["contact_name", "priority"]

    def test_clearing_a_field_is_recorded_as_a_change_to_null(self, mgr):
        lead = create_lead(mgr, contact_name="Someone")
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"contact_name": None})
        event = activities(mgr, lead["id"])[-1]
        assert event["before"] == {"contact_name": "Someone"} and event["after"] == {"contact_name": None}

    def test_a_campaign_change_names_both_campaigns(self, mgr):
        first, second = create_campaign(mgr), create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=first["id"])
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": second["id"]})
        event = activities(mgr, lead["id"])[-1]
        assert event["before"] == {"campaign_id": first["id"], "campaign_name": first["name"]}
        assert event["after"] == {"campaign_id": second["id"], "campaign_name": second["name"]}
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": None})
        assert activities(mgr, lead["id"])[-1]["after"] == {"campaign_id": None, "campaign_name": None}

    def test_numbers_are_stored_as_numbers(self, mgr):
        lead = create_lead(mgr, estimated_value=10)
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"estimated_value": "2500.75", "latitude": 33.5, "longitude": 44.25})
        event = activities(mgr, lead["id"])[-1]
        assert event["before"]["estimated_value"] == 10 and event["after"]["estimated_value"] == 2500.75
        assert event["after"]["latitude"] == 33.5

    def test_long_values_are_clipped_so_history_stays_small(self, mgr):
        lead = create_lead(mgr)
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"notes": "n" * 3000, "description": "d" * 1800})
        event = activities(mgr, lead["id"])[-1]
        assert len(event["after"]["notes"]) <= 301 and event["after"]["notes"].endswith("…")
        assert len(event["after"]["description"]) <= 301
        assert get_lead(mgr, lead["id"])["notes"] == "n" * 3000                                   # the lead itself keeps the full text

    def test_no_change_no_event(self, mgr):
        lead = create_lead(mgr)
        before = event_types(mgr, lead["id"])
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": lead["priority"]})
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {}).status_code == 400
        assert event_types(mgr, lead["id"]) == before


class TestAWholeLifeStory:
    def test_the_timeline_of_a_lead_from_creation_to_archive_and_back(self, p, mgr):
        emp = p["employee"]["headers"]
        lead = create_lead(mgr)
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": "high"})
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(emp, lead["id"], "contacted", note="first call")
        set_stage(emp, lead["id"], "interested")
        assign(mgr, lead["id"], p["employee2"]["id"])
        set_stage(p["employee2"]["headers"], lead["id"], "lost", lost_reason="delayed_decision", note="wants to wait")
        set_stage(mgr, lead["id"], "assigned", note="he called back")
        api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr)
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr)
        assert event_types(mgr, lead["id"]) == [
            "lead_created", "lead_updated",
            "lead_assigned", "stage_changed",                       # employee gets it; new -> assigned automatically
            "stage_changed", "stage_changed",                       # contacted, interested
            "lead_reassigned",                                      # -> employee2 (stage untouched)
            "stage_changed", "lead_marked_lost",                    # lost with a reason
            "stage_changed",                                        # reopened by the manager
            "lead_unassigned", "stage_changed",                     # unassigned; assigned -> new automatically
            "lead_archived", "lead_restored",
        ]
        events = activities(mgr, lead["id"])
        lost = [e for e in events if e["event_type"] == "lead_marked_lost"][0]
        assert lost["note"] == "wants to wait" and lost["after"]["lost_reason"] == "delayed_decision"
        assert lost["actor"]["name"] == "Test act-employee2"
        reopened = [e for e in events if e["event_type"] == "stage_changed" and e["metadata"].get("reopened")][0]
        assert reopened["note"] == "he called back"
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


# =============================================================================
class TestReading:
    def test_newest_first_with_pagination(self, mgr):
        lead = create_lead(mgr)
        for i in range(6):
            api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"notes": f"edit {i}"})
        page = api("GET", f"/api/sales/leads/{lead['id']}/activities?limit=3&offset=0", mgr).json()
        assert page["total"] == 7 and (page["limit"], page["offset"]) == (3, 0)
        assert [e["after"].get("notes") for e in page["items"]] == ["edit 5", "edit 4", "edit 3"]
        rest = api("GET", f"/api/sales/leads/{lead['id']}/activities?limit=3&offset=3", mgr).json()
        assert [e["after"].get("notes") for e in rest["items"]][:3] == ["edit 2", "edit 1", "edit 0"]
        last = api("GET", f"/api/sales/leads/{lead['id']}/activities?limit=3&offset=6", mgr).json()
        assert [e["event_type"] for e in last["items"]] == ["lead_created"]
        seqs = [e["seq"] for e in page["items"]]
        assert seqs == sorted(seqs, reverse=True)

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "limit=x"])
    def test_bad_paging(self, mgr, query):
        lead = create_lead(mgr)
        assert api("GET", f"/api/sales/leads/{lead['id']}/activities?{query}", mgr).status_code == 422

    def test_a_timeline_is_scoped_like_the_lead(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        assert api("GET", f"/api/sales/leads/{lead['id']}/activities", p["employee"]["headers"]).status_code == 200
        for outsider in ("employee2", "onboarding", "no_roles"):
            assert api("GET", f"/api/sales/leads/{lead['id']}/activities", p[outsider]["headers"]).status_code == 403
        assert api("GET", f"/api/sales/leads/{PHANTOM_ID}/activities", mgr).status_code == 404

    def test_history_survives_archiving(self, mgr):
        lead = create_lead(mgr)
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_archived"]


class TestImmutability:
    def test_there_is_no_way_to_change_or_remove_an_event_through_the_api(self, mgr):
        lead = create_lead(mgr)
        (event,) = activities(mgr, lead["id"])
        for method in ("PATCH", "PUT", "DELETE", "POST"):
            for path in (f"/api/sales/leads/{lead['id']}/activities", f"/api/sales/leads/{lead['id']}/activities/{event['id']}"):
                r = api(method, path, mgr, {"note": "rewritten"})
                assert r.status_code in (404, 405), (method, path, r.status_code)
        assert activities(mgr, lead["id"]) == [event]

    def test_the_lead_row_carries_no_history_blob(self, mgr):
        lead = create_lead(mgr)
        assert not {"activities", "history", "timeline", "events", "log"} & set(get_lead(mgr, lead["id"]))

    def test_the_database_itself_refuses_updates_and_deletes(self, mgr):
        from sqlalchemy import delete, text, update
        from sqlalchemy.exc import DBAPIError
        from sales.models import SalesLeadActivity

        lead = create_lead(mgr)
        (event,) = activities(mgr, lead["id"])

        async def _attack(db):
            refused = 0
            for statement in (update(SalesLeadActivity).where(SalesLeadActivity.id == uuid.UUID(event["id"])).values(event_type="lead_archived"),
                              delete(SalesLeadActivity).where(SalesLeadActivity.id == uuid.UUID(event["id"])),
                              text("TRUNCATE sales_lead_activities")):
                try:
                    async with db.begin_nested():
                        await db.execute(statement)
                except DBAPIError as exc:
                    assert "append-only" in str(exc)
                    refused += 1
            return refused

        assert run_db(_attack) == 3
        assert activities(mgr, lead["id"]) == [event]


class TestNoSecrets:
    def test_no_payload_key_looks_like_a_secret_and_every_key_is_allowlisted(self, p, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"], notes="password: hunter2 (free text is the user's own data)")
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(p["employee"]["headers"], lead["id"], "contacted", note="token: abc")
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": "high", "notes": "another note", "email": f"n-{uniq()}@example.com"})
        api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr)
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        for event in activities(mgr, lead["id"]):
            for section in ("before", "after"):
                assert set(event[section] or {}) <= PAYLOAD_KEYS, (event["event_type"], section, event[section])
            everything = list(_keys(event))
            assert not [k for k in everything if any(s in k.lower() for s in SECRETISH)], (event["event_type"], everything)
            assert set(event["metadata"]) <= {"actor_roles", "automatic", "cause", "fields", "reopened", "at_creation", "duplicate_override"}

    def test_the_timeline_never_contains_anything_about_the_actors_credentials(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        blob = json.dumps(activities(mgr, lead["id"])).lower()
        for staff in (p["manager"], p["employee"]):
            for secret in (staff["password"].lower(), "bcrypt", "$2b$", "$2a$", staff["token"].lower()):
                assert secret not in blob

    def test_the_stored_rows_are_clean_too(self, p, mgr):
        from sqlalchemy import text

        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])

        async def _raw(db):
            rows = (await db.execute(text(
                "SELECT COALESCE(before_data::text,'') || COALESCE(after_data::text,'') || COALESCE(note,'') || metadata::text "
                "FROM sales_lead_activities WHERE lead_id = :l"), {"l": uuid.UUID(lead["id"])})).scalars().all()
            return " ".join(rows).lower()

        stored = run_db(_raw)
        assert "password" not in stored and "token" not in stored and "$2b$" not in stored
        for staff in (p["manager"], p["employee"]):
            assert staff["password"].lower() not in stored


class TestRecordedInTheSameTransaction:
    def test_a_refused_operation_leaves_no_event_and_no_change(self, p, mgr):
        lead = create_lead(mgr)
        events = event_types(mgr, lead["id"])
        before = get_lead(mgr, lead["id"])
        for method, path, body in [
            ("POST", f"/api/sales/leads/{lead['id']}/stage", {"stage": "won"}),                        # not allowed from `new`
            ("POST", f"/api/sales/leads/{lead['id']}/stage", {"stage": "lost"}),                       # reason missing
            ("POST", f"/api/sales/leads/{lead['id']}/assign", {"assigned_to": p["manager"]["id"]}),    # ineligible assignee
            ("PATCH", f"/api/sales/leads/{lead['id']}", {"source": "nope", "priority": "high"}),       # one bad field spoils the request
            ("PATCH", f"/api/sales/leads/{lead['id']}", {"campaign_id": PHANTOM_ID}),
        ]:
            assert api(method, path, mgr, body).status_code in (400, 409), (method, path, body)
        assert event_types(mgr, lead["id"]) == events and get_lead(mgr, lead["id"]) == before

    def test_a_blocked_duplicate_creates_neither_lead_nor_events(self, mgr):
        phone = f"+1{uniq(9).translate(str.maketrans('abcdef', '123456'))}0"
        create_lead(mgr, phone=phone)
        body = lead_body(phone=phone)
        assert api("POST", "/api/sales/leads", mgr, body).status_code == 409
        assert api("GET", "/api/sales/leads", mgr, None, params={"q": body["business_name"]}).json()["total"] == 0

    def test_the_events_of_one_request_share_a_correlation_id_and_different_requests_do_not(self, p, mgr):
        from sqlalchemy import text

        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])                       # one request -> lead_assigned + an automatic stage_changed
        set_stage(mgr, lead["id"], "contacted")                            # another request

        async def _rows(db):
            rows = (await db.execute(text(
                "SELECT event_type, correlation_id FROM sales_lead_activities WHERE lead_id = :l ORDER BY seq"), {"l": uuid.UUID(lead["id"])})).all()
            return [(t, str(c)) for t, c in rows]

        rows = run_db(_rows)
        assert [t for t, _ in rows] == ["lead_created", "lead_assigned", "stage_changed", "stage_changed"]
        created, assigned, auto_stage, manual_stage = [c for _, c in rows]
        assert assigned == auto_stage                                       # same request
        assert len({created, assigned, manual_stage}) == 3                  # three requests, three ids

    def test_seq_orders_events_across_leads_and_requests(self, mgr):
        a, b = create_lead(mgr), create_lead(mgr)
        api("PATCH", f"/api/sales/leads/{a['id']}", mgr, {"priority": "high"})
        seq_of = lambda lead_id: [e["seq"] for e in activities(mgr, lead_id)]  # noqa: E731
        assert seq_of(a["id"])[0] < seq_of(b["id"])[0] < seq_of(a["id"])[1]
