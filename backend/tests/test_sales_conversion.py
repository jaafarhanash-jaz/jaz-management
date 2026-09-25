"""JAZ Sales - Phase 4: converting a won lead into a customer and a JAZ company (HTTP integration tier).

The preflight (read-only: what would converting do, and what would stop it), the conversion itself (the JAZ company is
created by the platform's own company service; the customer and its onboarding are created with it, in one
transaction), the duplicate rules (an email / phone a JAZ account already uses, or an exact company duplicate, BLOCKS;
a possible duplicate needs an explicit confirmation), idempotency (repeating - or racing - the same conversion creates
nothing more), the timeline and audit trail, and that no secret ever leaves the request. Who may convert is covered
per role in test_sales_customers_rbac.py; this module uses the roles that are allowed and concentrates on behavior.
"""
import concurrent.futures
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from models import Company, SubscriptionPlan, User
from sales.models import SalesCustomer

from sales_customer_test_utils import (
    OWNER_PASSWORD,
    PHANTOM_ID,
    active_plan_id,
    api,
    assign,
    audit_events,
    convert,
    convert_body,
    converted,
    count_companies,
    count_customers,
    count_users,
    create_lead,
    db_row,
    errors_of,
    get_lead,
    make_personas,
    make_plan,
    phase4_events,
    phone_variant,
    preflight,
    run_db,
    set_stage,
    uniq,
    unique_lead_email,
    unique_lead_phone,
    won_lead,
)
from sales_activity_test_utils import activities, utcnow
from sales_test_utils import admin_token, assert_scratch_target, auth, login, lookup_user, owner_token, owner_user  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "cv")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture()
def lead(p, mgr):
    return won_lead(mgr, p["employee"]["id"])


def _snapshot(lead, body):
    return (
        count_companies(body.get("business_name") or lead["business_name"]),
        count_users(body["owner_email"]) if body.get("owner_email") else None,
        count_customers(lead["id"]),
    )


def _nothing_created(lead, body, before=(0, 0, 0)):
    """The conversion left no trace: no company under that name, no new account on that email, no customer for the lead."""
    assert _snapshot(lead, body) == (before[0], before[1] if body.get("owner_email") else None, before[2])


# =============================================================================
class TestPreflight:
    def test_shape_and_the_values_come_from_the_lead(self, lead, mgr):
        pre = preflight(mgr, lead["id"], {})
        assert set(pre) == {"lead", "already_converted", "customer", "values", "blockers", "duplicates", "requires_confirmation", "can_convert", "plans"}
        assert pre["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "won", "archived": False}
        assert pre["values"] == {
            "business_name": lead["business_name"], "owner_name": lead["contact_name"], "owner_email": lead["email"],
            "owner_phone": lead["phone"], "address": lead["address"],
        }
        assert pre["blockers"] == [] and pre["can_convert"] is True and pre["requires_confirmation"] is False
        assert pre["already_converted"] is False and pre["customer"] is None
        assert pre["duplicates"] == {"has_exact": False, "has_possible": False, "companies": []}

    def test_a_missing_body_is_the_same_as_an_empty_one(self, lead, mgr):
        assert preflight(mgr, lead["id"], None)["values"] == preflight(mgr, lead["id"], {})["values"]

    def test_what_the_caller_gives_replaces_the_leads_value(self, lead, mgr):
        pre = preflight(mgr, lead["id"], {"business_name": "Renamed Ltd", "owner_email": "someone@example.org", "owner_phone": "+9647701234567", "owner_name": "Someone", "address": "Street 1"})
        assert pre["values"] == {"business_name": "Renamed Ltd", "owner_name": "Someone", "owner_email": "someone@example.org", "owner_phone": "+9647701234567", "address": "Street 1"}

    def test_the_plans_on_offer_are_the_active_ones(self, lead, mgr):
        inactive, active = make_plan(active=False), make_plan(active=True)
        ids = {plan["id"] for plan in preflight(mgr, lead["id"], {})["plans"]}
        assert active in ids and inactive not in ids
        plan = next(x for x in preflight(mgr, lead["id"], {})["plans"] if x["id"] == active)
        assert set(plan) == {"id", "name", "price", "duration_months", "max_employees"} and plan["max_employees"] == 7 and plan["price"] == 12.5

    def test_it_is_read_only(self, lead, mgr):
        before = (count_customers(lead["id"]), count_companies(lead["business_name"]))
        for _ in range(3):
            preflight(mgr, lead["id"], {})
        assert (count_customers(lead["id"]), count_companies(lead["business_name"])) == before == (0, 0)
        assert phase4_events(mgr, lead["id"]) == []

    @pytest.mark.parametrize("stage", ["new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation"])
    def test_a_lead_that_is_not_won_is_not_convertible(self, p, mgr, stage):
        lead = create_lead(mgr)
        if stage != "new":
            assign(mgr, lead["id"], p["employee"]["id"])
            if stage != "assigned":
                set_stage(mgr, lead["id"], stage)
        pre = preflight(mgr, lead["id"], {})
        assert pre["can_convert"] is False and [b["code"] for b in pre["blockers"]][:1] == ["lead_not_won"]
        assert pre["lead"]["pipeline_stage"] == stage

    def test_a_lost_lead_is_not_convertible(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "lost", lost_reason="no_response")
        assert preflight(mgr, lead["id"], {})["blockers"][0]["code"] == "lead_not_won"

    def test_an_archived_lead_is_not_convertible(self, lead, mgr):
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200
        pre = preflight(mgr, lead["id"], {})
        assert pre["can_convert"] is False and pre["blockers"][0]["code"] == "lead_archived" and pre["lead"]["archived"] is True

    def test_missing_owner_details_are_blockers_with_their_field(self, p, mgr):
        lead = won_lead(mgr, p["employee"]["id"], contact_name=None, phone=None, email=None)
        pre = preflight(mgr, lead["id"], {})
        assert {(b["code"], b["field"]) for b in pre["blockers"]} == {
            ("owner_name_required", "owner_name"), ("owner_email_required", "owner_email"), ("owner_phone_required", "owner_phone"),
        }
        assert pre["can_convert"] is False
        fixed = preflight(mgr, lead["id"], {"owner_name": "Given", "owner_email": unique_lead_email("g"), "owner_phone": unique_lead_phone()})
        assert fixed["blockers"] == [] and fixed["can_convert"] is True

    def test_an_email_or_phone_a_jaz_account_already_uses_is_a_blocker(self, lead, mgr, owner_user):
        email = preflight(mgr, lead["id"], {"owner_email": owner_user["email"]})
        assert ("owner_email_registered", "owner_email") in {(b["code"], b["field"]) for b in email["blockers"]} and email["can_convert"] is False
        phone = preflight(mgr, lead["id"], {"owner_phone": owner_user["phone"]})
        assert ("owner_phone_registered", "owner_phone") in {(b["code"], b["field"]) for b in phone["blockers"]}

    def test_the_email_check_ignores_case(self, lead, mgr, owner_user):
        shouted = preflight(mgr, lead["id"], {"owner_email": owner_user["email"].upper()})
        assert "owner_email_registered" in {b["code"] for b in shouted["blockers"]}

    def test_a_phone_the_platform_cannot_store_is_a_blocker(self, lead, mgr):
        pre = preflight(mgr, lead["id"], {"owner_phone": "07701@234567"})
        assert ("owner_phone_invalid", "owner_phone") in {(b["code"], b["field"]) for b in pre["blockers"]}

    def test_an_existing_company_of_that_owner_is_reported_with_how_it_matched(self, lead, mgr, owner_user):
        pre = preflight(mgr, lead["id"], {"owner_email": owner_user["email"]})
        assert pre["duplicates"]["has_exact"] is True and "company_exists" in {b["code"] for b in pre["blockers"]}
        company = pre["duplicates"]["companies"][0]
        assert set(company) == {"id", "name", "severity", "matches"} and company["severity"] == "exact"
        assert {"field": "email", "kind": "exact", "matched_on": "owner_email"} in company["matches"]

    def test_the_same_number_written_differently_is_an_exact_duplicate_even_though_it_is_not_registered_text(self, p, mgr):
        first = converted(mgr, p["employee"]["id"])
        other = won_lead(mgr, p["employee"]["id"])
        pre = preflight(mgr, other["id"], {"owner_phone": phone_variant(first["body"]["owner_phone"])})
        assert "owner_phone_registered" not in {b["code"] for b in pre["blockers"]}          # different text: the account check passes ...
        assert pre["duplicates"]["has_exact"] is True and "company_exists" in {b["code"] for b in pre["blockers"]}   # ... the duplicate check does not
        assert pre["duplicates"]["companies"][0]["id"] == first["customer"]["company"]["id"]

    def test_the_same_business_name_is_only_a_possible_duplicate_that_needs_confirmation(self, p, mgr):
        first = converted(mgr, p["employee"]["id"])
        other = won_lead(mgr, p["employee"]["id"])
        pre = preflight(mgr, other["id"], {"business_name": first["body"]["business_name"].upper()})
        assert pre["blockers"] == [] and pre["can_convert"] is True                              # nothing blocks ...
        assert pre["duplicates"]["has_possible"] is True and pre["duplicates"]["has_exact"] is False
        assert pre["requires_confirmation"] is True                                              # ... but the caller must confirm
        match = pre["duplicates"]["companies"][0]
        assert match["id"] == first["customer"]["company"]["id"] and match["severity"] == "possible"
        assert {"field": "business_name", "kind": "possible", "matched_on": "company_name"} in match["matches"]

    def test_a_phone_that_matches_only_after_its_country_code_is_a_possible_duplicate(self, p, mgr):
        first = converted(mgr, p["employee"]["id"])
        national = "+964" + first["body"]["owner_phone"][-9:]                                     # the same last nine digits under another country code
        other = won_lead(mgr, p["employee"]["id"])
        pre = preflight(mgr, other["id"], {"owner_phone": national})
        assert pre["duplicates"]["has_possible"] is True and pre["duplicates"]["has_exact"] is False and pre["requires_confirmation"] is True

    def test_an_already_converted_lead_says_so_and_shows_its_customer(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        pre = preflight(mgr, made["lead"]["id"], {})
        assert pre["already_converted"] is True and pre["can_convert"] is False and pre["customer"]["id"] == made["customer"]["id"]
        assert [b["code"] for b in pre["blockers"]] == ["already_converted"] and pre["plans"] == []

    def test_unknown_and_malformed_lead_ids_are_404(self, mgr):
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/convert/preflight", mgr, {}).status_code == 404
        assert api("POST", "/api/sales/leads/not-a-uuid/convert/preflight", mgr, {}).status_code == 404

    def test_unknown_fields_are_refused(self, lead, mgr):
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", mgr, {"company_id": PHANTOM_ID}).status_code == 422
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert/preflight", mgr, {"owner_email": "not-an-email"}).status_code == 422


# =============================================================================
class TestConvert:
    def test_a_won_lead_becomes_a_customer_and_a_company(self, p, mgr, lead):
        out = convert(mgr, lead)
        body = out["_body"]
        assert set(out) == {"customer", "already_converted", "_body"} and out["already_converted"] is False
        c = out["customer"]
        assert set(c) == {"id", "status", "converted_at", "converted_by", "created_at", "updated_at", "lead", "company", "onboarding", "can"}
        assert c["status"] == "active"                                                          # converted; onboarding not started
        assert c["converted_by"]["id"] == p["manager"]["id"]
        assert c["lead"] == {"id": lead["id"], "business_name": lead["business_name"], "pipeline_stage": "won", "archived": False,
                             "contact_name": lead["contact_name"], "phone": lead["phone"], "email": lead["email"], "city": lead["city"]}
        assert c["company"]["name"] == body["business_name"] and c["company"]["owner_email"] == body["owner_email"] and c["company"]["owner_phone"] == body["owner_phone"]
        assert c["company"]["owner_name"] == body["owner_name"] and c["company"]["subscription_status"] == "active" and c["company"]["deleted"] is False
        assert c["onboarding"]["stage"] == "won" and c["onboarding"]["assigned_to"] is None       # started, unowned

    def test_the_stored_links_are_lead_customer_company(self, p, mgr, lead):
        c = convert(mgr, lead)["customer"]
        row = db_row(SalesCustomer, id=uuid.UUID(c["id"]))
        assert str(row["lead_id"]) == lead["id"] and str(row["company_id"]) == c["company"]["id"] and str(row["converted_by"]) == p["manager"]["id"]
        assert row["status"] == "active" and abs(row["converted_at"] - utcnow()) < timedelta(minutes=2)
        status = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert status["converted"] is True and status["customer"]["id"] == c["id"] and status["can_convert"] is False

    def test_the_company_is_an_ordinary_jaz_company_made_by_the_platforms_own_service(self, mgr, lead):
        body = convert(mgr, lead)["_body"]

        async def _read(db):
            company = (await db.execute(select(Company).where(Company.name == body["business_name"]))).scalar_one()
            owner = (await db.execute(select(User).where(User.id == company.owner_id))).scalar_one()
            plan = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == company.subscription_plan_id))).scalar_one()
            return {
                "status": company.subscription_status, "plan": str(plan.id), "max": company.max_employees, "plan_max": plan.max_employees,
                "features": company.subscription_features, "plan_features": plan.features, "qr": bool(company.qr_code and company.qr_token),
                "role": owner.role, "company_id": str(owner.company_id) == str(company.id), "owner_status": owner.status,
                "email": owner.email, "phone": owner.phone, "name": owner.name, "hash_is_not_the_password": owner.password != body["owner_password"] and owner.password.startswith("$2"),
                "start": company.subscription_start_date, "end": company.subscription_end_date, "deleted": company.deleted_at,
            }

        got = run_db(_read)
        assert got["status"] == "active" and got["plan"] == body["subscription_plan_id"]
        assert got["max"] == got["plan_max"] and got["features"] == got["plan_features"]            # the plan's configuration was copied
        assert got["qr"] is True and got["start"] is None and got["end"] is None and got["deleted"] is None
        assert got["role"] == "company_owner" and got["company_id"] and got["owner_status"] == "active"
        assert (got["email"], got["phone"], got["name"]) == (body["owner_email"], body["owner_phone"], body["owner_name"])
        assert got["hash_is_not_the_password"] is True                                              # stored hashed by the core service

    def test_the_owner_can_log_in_with_the_password_the_manager_set(self, mgr, lead):
        body = convert(mgr, lead)["_body"]
        session = login(body["owner_email"], OWNER_PASSWORD)
        assert session["user"]["role"] == "company_owner" and session["user"]["email"] == body["owner_email"]
        assert login(body["owner_phone"], OWNER_PASSWORD)["user"]["email"] == body["owner_email"]   # ... and by phone

    def test_the_lead_is_preserved_exactly(self, p, mgr, lead):
        before = get_lead(mgr, lead["id"])
        convert(mgr, lead)
        after = get_lead(mgr, lead["id"])
        assert after == before                                                                       # every field, incl. updated_at and allowed_stages
        assert after["pipeline_stage"] == "won" and after["assigned_to"]["id"] == p["employee"]["id"]

    def test_the_super_admin_can_convert(self, p, admin_h, mgr):
        lead = won_lead(mgr, p["employee"]["id"])
        c = convert(admin_h, lead)["customer"]
        assert c["converted_by"]["name"] and c["status"] == "active"

    def test_an_optional_address_is_passed_to_the_company(self, mgr, lead):
        out = convert(mgr, lead, address="12 Market Street")
        assert db_row(Company, id=uuid.UUID(out["customer"]["company"]["id"]))["address"] == "12 Market Street"

    def test_the_response_and_the_records_never_carry_the_password(self, p, mgr, lead):
        out = convert(mgr, lead)
        c = out["customer"]
        response = {k: v for k, v in out.items() if k != "_body"}                                    # the helper's own copy of the REQUEST
        assert OWNER_PASSWORD not in json.dumps(response) and "$2" not in json.dumps(response)
        assert OWNER_PASSWORD not in json.dumps(api("GET", f"/api/sales/customers/{c['id']}", mgr).json())
        assert OWNER_PASSWORD not in json.dumps(activities(mgr, lead["id"]))                          # the whole timeline
        events = audit_events(c["id"])
        assert [e["action"] for e in events] == ["customer_converted"] and OWNER_PASSWORD not in json.dumps(events, default=str)
        assert "password" not in json.dumps(events, default=str).lower()

    def test_the_conversion_is_on_the_leads_timeline(self, p, mgr, lead):
        c = convert(mgr, lead)["customer"]
        events = [e for e in activities(mgr, lead["id"]) if e["event_type"] == "lead_converted"]
        assert len(events) == 1
        e = events[0]
        assert e["actor"]["id"] == p["manager"]["id"] and e["before"] is None
        assert e["after"] == {"customer_status": "active", "company_name": lead["business_name"], "plan": e["after"]["plan"], "onboarding_stage": "won"} and e["after"]["plan"]
        assert e["metadata"]["customer_id"] == c["id"] and e["metadata"]["company_id"] == c["company"]["id"] and e["metadata"]["onboarding_id"] == c["onboarding"]["id"]
        assert e["metadata"]["actor_roles"] == ["sales_manager"] and "duplicate_override" not in e["metadata"]
        assert phase4_events(mgr, lead["id"]) == ["lead_converted"]

    def test_the_conversion_is_audited_against_the_new_account(self, p, mgr, lead):
        c = convert(mgr, lead)["customer"]
        (event,) = audit_events(c["id"])
        owner_id = str(db_row(Company, id=uuid.UUID(c["company"]["id"]))["owner_id"])
        assert event["actor_user_id"] == p["manager"]["id"] and event["target_user_id"] == owner_id
        assert event["after"] == {"lead_id": lead["id"], "company_id": c["company"]["id"], "subscription_plan_id": active_plan_id()}

    def test_each_conversion_gets_its_own_company_and_customer(self, p, mgr):
        a, b = converted(mgr, p["employee"]["id"]), converted(mgr, p["employee"]["id"])
        assert a["customer"]["id"] != b["customer"]["id"] and a["customer"]["company"]["id"] != b["customer"]["company"]["id"]


# =============================================================================
class TestIdempotency:
    def test_repeating_the_same_conversion_creates_nothing(self, p, mgr, lead):
        first = convert(mgr, lead)
        again = convert(mgr, lead, expect=200, **{k: v for k, v in first["_body"].items() if k != "business_name"})
        assert again["already_converted"] is True and again["customer"]["id"] == first["customer"]["id"]
        assert again["customer"]["company"]["id"] == first["customer"]["company"]["id"]
        assert count_companies(lead["business_name"]) == 1 and count_customers(lead["id"]) == 1 and count_users(first["_body"]["owner_email"]) == 1
        assert phase4_events(mgr, lead["id"]) == ["lead_converted"]                                  # one event, not two
        assert len(audit_events(first["customer"]["id"])) == 1

    def test_a_repeat_with_different_values_still_returns_the_existing_customer(self, p, mgr, lead):
        first = convert(mgr, lead)
        other = convert(mgr, lead, expect=200)                                                       # a fresh email, phone, password, and another plan
        assert other["already_converted"] is True and other["customer"]["id"] == first["customer"]["id"]
        assert count_users(other["_body"]["owner_email"]) == 0                                       # the new values were not used for anything
        assert count_companies(lead["business_name"]) == 1

    def test_a_repeat_by_somebody_else_who_may_convert_returns_the_same_customer(self, p, admin_h, mgr, lead):
        first = convert(mgr, lead)
        again = convert(admin_h, lead, expect=200)
        assert again["already_converted"] is True and again["customer"]["id"] == first["customer"]["id"]
        assert again["customer"]["converted_by"]["id"] == p["manager"]["id"]                          # the first conversion stands

    def test_racing_requests_create_exactly_one_company_and_customer(self, p, mgr, lead, admin_h):
        bodies = [convert_body(lead) for _ in range(6)]                                              # six different owners: only the first may win
        def go(i):
            return api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr if i % 2 else admin_h, bodies[i])
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(go, range(6)))
        assert sorted(r.status_code for r in results) == [200] * 5 + [201], [r.text[:120] for r in results]
        winners = {r.json()["customer"]["id"] for r in results}
        assert len(winners) == 1 and count_customers(lead["id"]) == 1 and count_companies(lead["business_name"]) == 1
        assert sum(count_users(b["owner_email"]) for b in bodies) == 1                               # exactly one owner account exists
        assert phase4_events(mgr, lead["id"]) == ["lead_converted"]
        assert sum(1 for r in results if r.json()["already_converted"] is False) == 1

    def test_a_lost_race_never_leaves_a_company_behind(self, p, mgr):
        """Two conversions with the SAME owner email for two DIFFERENT leads: one wins, the other is refused - and the
        refused one leaves no company, no account and no customer (its transaction ends with the error)."""
        a, b = won_lead(mgr, p["employee"]["id"]), won_lead(mgr, p["employee"]["id"])
        email, phone = unique_lead_email("shared"), unique_lead_phone()
        def go(lead):
            return api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, convert_body(lead, owner_email=email, owner_phone=phone))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            ra, rb = list(pool.map(go, [a, b]))
        assert sorted((ra.status_code, rb.status_code))[0] == 201 and sorted((ra.status_code, rb.status_code))[1] in (400, 409)
        loser, loser_lead = (rb, b) if ra.status_code == 201 else (ra, a)
        assert count_users(email) == 1 and count_customers(loser_lead["id"]) == 0 and count_companies(loser_lead["business_name"]) == 0
        assert loser.status_code < 500


# =============================================================================
class TestWhichLeadsConvert:
    @pytest.mark.parametrize("stage", ["new", "assigned", "contacted", "negotiation"])
    def test_only_a_won_lead_converts(self, p, mgr, stage):
        lead = create_lead(mgr)
        if stage != "new":
            assign(mgr, lead["id"], p["employee"]["id"])
            if stage != "assigned":
                set_stage(mgr, lead["id"], stage)
        body = convert_body(lead)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_not_won"
        _nothing_created(lead, body)

    def test_a_lost_lead_does_not_convert(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        set_stage(mgr, lead["id"], "lost", lost_reason="too_expensive")
        body = convert_body(lead)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_not_won"
        _nothing_created(lead, body)

    def test_an_archived_lead_does_not_convert_until_restored(self, lead, mgr):
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        body = convert_body(lead)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived"
        _nothing_created(lead, body)
        assert api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr).status_code == 200
        assert convert(mgr, lead)["already_converted"] is False

    def test_a_converted_lead_can_be_archived_and_the_customer_stays(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        assert api("DELETE", f"/api/sales/leads/{made['lead']['id']}", mgr).status_code == 200
        r = api("GET", f"/api/sales/customers/{made['customer']['id']}", mgr)
        assert r.status_code == 200 and r.json()["lead"]["archived"] is True
        again = api("POST", f"/api/sales/leads/{made['lead']['id']}/convert", mgr, convert_body(made["lead"]))
        assert again.status_code == 200 and again.json()["already_converted"] is True                # replay wins over the archive check

    def test_unknown_and_malformed_ids_are_404(self, mgr, lead):
        body = convert_body(lead)
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/convert", mgr, body).status_code == 404
        assert api("POST", "/api/sales/leads/not-a-uuid/convert", mgr, body).status_code == 404
        assert api("GET", f"/api/sales/leads/{PHANTOM_ID}/conversion", mgr).status_code == 404
        assert api("GET", "/api/sales/leads/not-a-uuid/conversion", mgr).status_code == 404


# =============================================================================
class TestTheRequestIsValidatedAndNothingIsWrittenWhenItFails:
    def _refused(self, mgr, lead, expect, code=None, field=None, **overrides):
        body = convert_body(lead, **overrides)
        before = _snapshot(lead, body)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == expect, r.text[:400]
        if code:
            detail = errors_of(r)
            assert detail["code"] == code and (field is None or detail["field"] == field), detail
        _nothing_created(lead, body, before)
        return r

    @pytest.mark.parametrize("missing", ["business_name", "owner_name", "owner_email", "owner_phone", "owner_password", "subscription_plan_id"])
    def test_every_field_the_company_needs_is_required(self, lead, mgr, missing):
        self._refused(mgr, lead, 422, **{missing: None})

    @pytest.mark.parametrize("field", ["business_name", "owner_name", "owner_phone"])
    def test_blank_values_are_refused(self, lead, mgr, field):
        self._refused(mgr, lead, 422, **{field: "   "})

    @pytest.mark.parametrize("extra", [{"company_id": PHANTOM_ID}, {"lead_id": PHANTOM_ID}, {"status": "activated"}, {"converted_by": PHANTOM_ID}, {"role": "super_admin"}])
    def test_unknown_fields_cannot_be_smuggled_in(self, lead, mgr, extra):
        self._refused(mgr, lead, 422, **extra)

    def test_a_malformed_email_or_plan_id_is_refused(self, lead, mgr):
        self._refused(mgr, lead, 422, owner_email="not-an-email")
        self._refused(mgr, lead, 422, subscription_plan_id="not-a-uuid")

    def test_a_weak_password_is_a_field_error(self, lead, mgr):
        self._refused(mgr, lead, 400, code="owner_password_weak", field="owner_password", owner_password="abc")

    def test_an_unknown_or_inactive_plan_is_refused(self, lead, mgr):
        self._refused(mgr, lead, 400, code="plan_not_available", field="subscription_plan_id", subscription_plan_id=PHANTOM_ID)
        self._refused(mgr, lead, 400, code="plan_not_available", field="subscription_plan_id", subscription_plan_id=make_plan(active=False))

    def test_a_registered_email_or_phone_is_refused_with_its_field(self, lead, mgr, owner_user):
        self._refused(mgr, lead, 400, code="owner_email_registered", field="owner_email", owner_email=owner_user["email"])
        self._refused(mgr, lead, 400, code="owner_email_registered", field="owner_email", owner_email=owner_user["email"].upper())
        self._refused(mgr, lead, 400, code="owner_phone_registered", field="owner_phone", owner_phone=owner_user["phone"])

    def test_a_phone_with_an_at_sign_is_refused(self, lead, mgr):
        self._refused(mgr, lead, 400, code="owner_phone_invalid", field="owner_phone", owner_phone="0770@123456")

    def test_every_problem_is_listed_not_only_the_first(self, lead, mgr, owner_user):
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, convert_body(lead, owner_email=owner_user["email"], owner_phone=owner_user["phone"]))
        assert r.status_code == 400
        assert {b["code"] for b in errors_of(r)["blockers"]} == {"owner_email_registered", "owner_phone_registered"}

    def test_after_a_refusal_the_same_lead_still_converts(self, lead, mgr, owner_user):
        self._refused(mgr, lead, 400, code="owner_email_registered", owner_email=owner_user["email"])
        assert convert(mgr, lead)["already_converted"] is False


# =============================================================================
class TestDuplicateCompanies:
    def _first(self, p, mgr):
        return converted(mgr, p["employee"]["id"])

    def test_the_same_business_name_needs_confirmation_and_creates_nothing_until_confirmed(self, p, mgr):
        first = self._first(p, mgr)
        lead = won_lead(mgr, p["employee"]["id"])
        body = convert_body(lead, business_name=first["body"]["business_name"].upper())
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409
        detail = errors_of(r)
        assert detail["code"] == "duplicates_found" and detail["field"] == "duplicates"
        assert detail["duplicates"]["has_possible"] is True and detail["duplicates"]["companies"][0]["id"] == first["customer"]["company"]["id"]
        assert count_customers(lead["id"]) == 0 and count_users(body["owner_email"]) == 0

    def test_confirming_creates_the_company_and_records_the_override(self, p, mgr):
        first = self._first(p, mgr)
        lead = won_lead(mgr, p["employee"]["id"])
        out = convert(mgr, lead, business_name=first["body"]["business_name"], confirm_duplicates=True)
        assert out["already_converted"] is False and out["customer"]["company"]["id"] != first["customer"]["company"]["id"]
        (event,) = [e for e in activities(mgr, lead["id"]) if e["event_type"] == "lead_converted"]
        assert event["metadata"]["duplicate_override"] == {"possible": 1, "company_ids": [first["customer"]["company"]["id"]]}
        (audit,) = audit_events(out["customer"]["id"])
        assert audit["metadata"]["duplicate_override"]["company_ids"] == [first["customer"]["company"]["id"]]

    def test_an_exact_duplicate_blocks_even_when_confirmed(self, p, mgr):
        first = self._first(p, mgr)
        lead = won_lead(mgr, p["employee"]["id"])
        body = convert_body(lead, owner_phone=phone_variant(first["body"]["owner_phone"]), confirm_duplicates=True)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409 and errors_of(r)["code"] == "company_exists" and errors_of(r)["duplicates"]["has_exact"] is True
        assert count_customers(lead["id"]) == 0 and count_users(body["owner_email"]) == 0 and count_companies(lead["business_name"]) == 0

    def test_a_possible_phone_match_needs_confirmation_too(self, p, mgr):
        first = self._first(p, mgr)
        lead = won_lead(mgr, p["employee"]["id"])
        national = "+964" + first["body"]["owner_phone"][-9:]                                     # (a leading "00" would be read as an international prefix and dropped)
        body = convert_body(lead, owner_phone=national)
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, body)
        assert r.status_code == 409 and errors_of(r)["code"] == "duplicates_found", (r.status_code, r.text[:300], body["owner_phone"], first["body"]["owner_phone"])
        assert api("POST", f"/api/sales/leads/{lead['id']}/convert", mgr, {**body, "confirm_duplicates": True}).status_code == 201

    def test_the_super_admin_gets_the_same_rules(self, p, admin_h, mgr):
        first = self._first(p, mgr)
        lead = won_lead(mgr, p["employee"]["id"])
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", admin_h, convert_body(lead, business_name=first["body"]["business_name"]))
        assert r.status_code == 409 and errors_of(r)["code"] == "duplicates_found"

    def test_a_business_nobody_has_is_not_a_duplicate(self, p, mgr):
        lead = won_lead(mgr, p["employee"]["id"], business_name=f"Utterly Unique {uniq(14)} Trading")
        assert preflight(mgr, lead["id"], {})["duplicates"]["companies"] == []
        assert convert(mgr, lead)["already_converted"] is False


# =============================================================================
class TestConversionStatus:
    def test_before_and_after(self, p, mgr, lead):
        before = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert before == {"lead_stage": "won", "archived": False, "converted": False, "customer": None, "can_convert": True, "blocker": None}
        c = convert(mgr, lead)["customer"]
        after = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert after["converted"] is True and after["can_convert"] is False and after["blocker"] is None and after["customer"]["id"] == c["id"]

    def test_why_not(self, p, mgr):
        lead = create_lead(mgr)
        assign(mgr, lead["id"], p["employee"]["id"])
        s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert (s["lead_stage"], s["can_convert"], s["blocker"], s["converted"]) == ("assigned", False, "lead_not_won", False)
        won = won_lead(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{won['id']}", mgr)
        s = api("GET", f"/api/sales/leads/{won['id']}/conversion", mgr).json()
        assert (s["archived"], s["can_convert"], s["blocker"]) == (True, False, "lead_archived")

    def test_a_caller_who_may_only_view_customers_cannot_convert(self, p, mgr, lead):
        s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", p["employee"]["headers"]).json()      # the owner of the lead, a Sales Employee
        assert s["lead_stage"] == "won" and s["converted"] is False and s["can_convert"] is False
