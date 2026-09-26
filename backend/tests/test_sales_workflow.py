"""JAZ Sales - simplified workflow (HTTP integration tier, scratch backend only).

  * the Sales Manager's distribution setting: who may read / change it (the round-robin itself is proven at the database tier,
    test_sales_distribution_db.py - switching the ONE global setting on here would reach the other test worker's leads);
  * the Sales Employee's work queue: only their own open assigned leads, the longest-waiting first;
  * "Not Interested": the lead is lost with a reason, kept with its whole timeline, and the queue moves on;
  * "Agreed": the Customer Setup - Lead -> Customer -> JAZ Company -> Subscription (trial / paid), optional employees and
    tasks, the duplicate / blocking checks, idempotency, all-or-nothing, and who may do it to which lead;
  * only the Customer Setup wins a lead: the pipeline's own `won` move (POST /leads/{id}/stage) is closed to EVERY caller - Sales
    Employee, Sales Manager, Super Admin, a custom role holding every permission - and `won` is never offered (the retired
    conversion endpoints: test_sales_conversion.py);
  * Lead Data Entry's edit window (their latest 10 leads, and only while they are still unassigned) and the Sales Manager's
    override - enforced by the server;
  * the lead export: every matching lead (not one page), the filters, the scope, no internal fields, no formula injection,
    audited;
  * onboarding is no longer a workspace section.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target()

import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from models import Company, PaymentTransaction, Task, User
from sales.models import SalesCustomer, SalesOnboarding, StaffAuditEvent, StaffRole, StaffRolePermission
from sales_customer_test_utils import (
    agree,
    legacy_win,
    OWNER_PASSWORD,
    PHANTOM_ID,
    activities,
    api,
    assign,
    audit_events,
    convert,
    count_companies,
    count_customers,
    count_users,
    create_lead,
    create_staff,
    db_row,
    errors_of,
    get_lead,
    make_custom_role,
    make_personas,
    make_plan,
    onboarding_worker,
    phone_variant,
    retire_roles,
    run_db,
    set_stage,
    uniq,
    unique_lead_email,
    unique_lead_phone,
    won_lead,
)
from sales_lead_test_utils import create_campaign, lead_body
from sales_test_utils import ALL_PERMISSION_KEYS, admin_token, auth, mint_token  # noqa: F401  (the token fixture mints instead of logging in)

EMPLOYEE_PASSWORD = "Emp#Pass-2026"


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "wf")


def H(p, who):
    return p[who]["headers"]


@pytest.fixture(scope="module")
def mgr(p):
    return H(p, "manager")


@pytest.fixture(scope="module")
def plan():
    """A throwaway active plan: 7 employees at most, 3 months."""
    return make_plan()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def assigned_lead(mgr, employee_id, **overrides) -> dict:
    lead = create_lead(mgr, **overrides)
    assign(mgr, lead["id"], employee_id)
    return get_lead(mgr, lead["id"])


def employee_entry(**overrides) -> dict:
    body = {"name": f"Staff {uniq(6)}", "email": unique_lead_email("emp"), "phone": unique_lead_phone(), "password": EMPLOYEE_PASSWORD,
            "position": "Cashier"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def setup_body(lead: dict, plan_id: str, **overrides) -> dict:
    body = {
        "business_name": lead["business_name"], "owner_name": "Owner Person", "owner_email": unique_lead_email("owner"),
        "owner_phone": unique_lead_phone(), "owner_password": OWNER_PASSWORD, "subscription_plan_id": plan_id,
        "subscription_type": "trial",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def setup(headers, lead, plan_id, expect=201, **overrides):
    body = setup_body(lead, plan_id, **overrides)
    r = api("POST", f"/api/sales/leads/{lead['id']}/setup", headers, body)
    assert r.status_code == expect, f"setup: expected {expect}, got {r.status_code} {r.text[:600]}"
    return r, body


def queue(headers, position=0, expect=200) -> dict:
    r = api("GET", f"/api/sales/queue?position={position}", headers)
    assert r.status_code == expect, r.text[:300]
    return r.json()


# =============================================================================
# the distribution setting
# =============================================================================
class TestDistributionSetting:
    def test_the_manager_reads_it(self, mgr, p):
        r = api("GET", "/api/sales/settings/distribution", mgr)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"auto_distribution_enabled", "distribution_mode", "updated_at", "updated_by", "rotation", "next_assignee"}
        assert body["distribution_mode"] == "equal"
        members = {m["id"] for m in body["rotation"]}
        assert {p["employee"]["id"], p["employee2"]["id"]} <= members                # active Sales Employees are in it
        for who in ("manager", "data_entry", "onboarding", "no_roles"):
            assert p[who]["id"] not in members, who                                   # nobody who may not receive leads
        assert all(set(m) == {"id", "name", "email", "open_leads"} for m in body["rotation"])

    def test_a_deactivated_employee_leaves_the_rotation(self, mgr, admin_h):
        person = create_staff(admin_h, ["sales_employee"], "wf-leaver")
        ids = lambda: {m["id"] for m in api("GET", "/api/sales/settings/distribution", mgr).json()["rotation"]}  # noqa: E731
        assert person["id"] in ids()
        assert api("PATCH", f"/api/sales/team/{person['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert person["id"] not in ids()
        other = create_staff(admin_h, ["sales_employee"], "wf-revoked")
        assert other["id"] in ids()
        assert api("DELETE", f"/api/sales/team/{other['id']}/roles/sales_employee", admin_h).status_code == 200
        assert other["id"] not in ids()

    def test_the_manager_changes_it_idempotently(self, mgr):
        # OFF -> OFF only: switching the ONE global setting ON here would hand the other test worker's leads to strangers
        # (switching on and the round-robin are proven in a never-committed transaction: test_sales_distribution_db.py)
        current = api("GET", "/api/sales/settings/distribution", mgr).json()
        assert current["auto_distribution_enabled"] is False
        r = api("PUT", "/api/sales/settings/distribution", mgr, {"auto_distribution_enabled": False, "distribution_mode": "equal"})
        assert r.status_code == 200 and r.json()["auto_distribution_enabled"] is False

    @pytest.mark.parametrize("body", [
        {"auto_distribution_enabled": True, "distribution_mode": "by_region"},
        {"auto_distribution_enabled": "maybe"},
        {"auto_distribution_enabled": False, "last_assigned_user_id": PHANTOM_ID},
        {},
    ])
    def test_a_bad_body_is_422(self, mgr, body):
        assert api("PUT", "/api/sales/settings/distribution", mgr, body).status_code == 422

    @pytest.mark.parametrize("who", ["employee", "data_entry", "onboarding", "no_roles"])
    def test_nobody_else_reads_or_changes_it(self, p, who):
        assert api("GET", "/api/sales/settings/distribution", H(p, who)).status_code == 403
        r = api("PUT", "/api/sales/settings/distribution", H(p, who), {"auto_distribution_enabled": True})
        assert r.status_code == 403 and r.json()["detail"] == "Access denied"

    def test_the_super_admin_may(self, admin_h):
        assert api("GET", "/api/sales/settings/distribution", admin_h).status_code == 200


# =============================================================================
# the work queue and "Not Interested"
# =============================================================================
class TestQueue:
    def test_only_my_open_leads_the_longest_waiting_first(self, admin_h, mgr):
        me = create_staff(admin_h, ["sales_employee"], "wf-queue")
        other = create_staff(admin_h, ["sales_employee"], "wf-queue-other")
        a, b, c = (assigned_lead(mgr, me["id"]) for _ in range(3))
        assigned_lead(mgr, other["id"])
        first = queue(me["headers"])
        assert first["total"] == 3 and first["position"] == 0 and first["lead"]["id"] == a["id"]
        assert first["lead"]["contact_name"] and first["lead"]["phone"]              # the whole lead, ready to call
        assert queue(me["headers"], 1)["lead"]["id"] == b["id"]
        clamped = queue(me["headers"], 99)
        assert clamped["position"] == 2 and clamped["lead"]["id"] == c["id"]
        assert queue(other["headers"])["total"] == 1

    def test_decided_or_archived_leads_leave_the_queue(self, admin_h, mgr):
        me = create_staff(admin_h, ["sales_employee"], "wf-queue2")
        won = assigned_lead(mgr, me["id"])
        agree(me["headers"], won)                                                     # decided: "Agreed" - the Customer Setup wins it
        archived = assigned_lead(mgr, me["id"])
        api("DELETE", f"/api/sales/leads/{archived['id']}", mgr)
        working = assigned_lead(mgr, me["id"])
        set_stage(me["headers"], working["id"], "interested")
        q = queue(me["headers"])
        assert q["total"] == 1 and q["lead"]["id"] == working["id"]

    def test_an_empty_queue(self, p, mgr):
        assert queue(mgr) == {"lead": None, "position": 0, "total": 0}               # nobody assigns leads to a manager
        assert queue(H(p, "data_entry"))["total"] == 0

    @pytest.mark.parametrize("who", ["onboarding", "no_roles"])
    def test_callers_without_lead_access_are_denied(self, p, who):
        queue(H(p, who), expect=403)

    def test_not_interested_keeps_the_lead_and_moves_on(self, admin_h, mgr):
        me = create_staff(admin_h, ["sales_employee"], "wf-notint")
        first, second = assigned_lead(mgr, me["id"]), assigned_lead(mgr, me["id"])
        assert queue(me["headers"])["lead"]["id"] == first["id"]
        r = api("POST", f"/api/sales/leads/{first['id']}/stage", me["headers"],
                {"stage": "lost", "lost_reason": "not_interested", "note": "Uses paper, no budget this year"})
        assert r.status_code == 200 and r.json()["pipeline_stage"] == "lost" and r.json()["lost_reason"] == "not_interested"
        after = queue(me["headers"])
        assert after["total"] == 1 and after["lead"]["id"] == second["id"]           # the next lead
        kept = get_lead(mgr, first["id"])                                             # preserved, not archived
        assert kept["archived_at"] is None and kept["pipeline_stage"] == "lost" and kept["closed_at"]
        lost = [e for e in activities(mgr, first["id"]) if e["event_type"] == "lead_marked_lost"]
        assert len(lost) == 1 and lost[0]["note"] == "Uses paper, no budget this year" and lost[0]["actor"]["id"] == me["id"]
        # a reason is required
        r = api("POST", f"/api/sales/leads/{second['id']}/stage", me["headers"], {"stage": "lost"})
        assert r.status_code == 400 and errors_of(r)["code"] == "lost_reason_required"


# =============================================================================
# "Agreed": the Customer Setup
# =============================================================================
class TestCustomerSetup:
    def test_preflight(self, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", H(p, "employee"), {})
        assert r.status_code == 200
        pre = r.json()
        assert pre["can_setup"] is True and pre["marks_won"] is True and pre["already_converted"] is False
        assert pre["lead"]["assigned_to"]["id"] == p["employee"]["id"]
        assert {o["type"]: o["days"] for o in pre["subscription_options"]} == {"trial": 7, "paid": None}
        assert plan in {x["id"] for x in pre["plans"]} and pre["today"] == today_utc().isoformat()
        from sales.timezone import app_today
        assert pre["local_today"] == app_today().isoformat()                     # the date task due dates are checked against
        assert pre["values"]["business_name"] == lead["business_name"]
        taken = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", H(p, "employee"), {"owner_email": p["manager"]["email"]}).json()
        assert taken["can_setup"] is False and "owner_email_registered" in {b["code"] for b in taken["blockers"]}

    def test_trial_without_employees_or_tasks(self, p, mgr, plan):
        emp = H(p, "employee")
        lead = assigned_lead(mgr, p["employee"]["id"])
        before = datetime.now(timezone.utc).replace(microsecond=0)
        r, body = setup(emp, lead, plan)
        after = datetime.now(timezone.utc)
        out = r.json()
        assert out["already_converted"] is False and out["employees_added"] == 0 and out["tasks_created"] == 0
        sub = out["subscription"]
        assert sub["type"] == "trial" and sub["ends_exactly"] is True
        starts_at, ends_at = datetime.fromisoformat(sub["starts_at"]), datetime.fromisoformat(sub["ends_at"])
        assert before <= starts_at <= after                                  # the creation instant - not midnight
        assert ends_at - starts_at == timedelta(hours=7 * 24)                # exactly 7 x 24 hours
        assert sub["start_date"] == starts_at.date().isoformat() and sub["end_date"] == ends_at.date().isoformat()
        customer = out["customer"]
        assert customer["status"] == "activated" and customer["company"]["name"] == lead["business_name"]
        assert customer["converted_by"]["id"] == p["employee"]["id"]
        assert OWNER_PASSWORD not in r.text

        row = db_row(SalesCustomer, id=uuid.UUID(customer["id"]))
        assert row["subscription_type"] == "trial" and row["status"] == "activated"
        assert db_row(SalesOnboarding, customer_id=uuid.UUID(customer["id"])) is None     # no onboarding record
        company = db_row(Company, id=uuid.UUID(customer["company"]["id"]))
        assert company["subscription_status"] == "active" and company["subscription_plan_id"] == uuid.UUID(plan)
        assert company["subscription_start_date"] == starts_at and company["subscription_end_date"] == ends_at
        assert company["subscription_ends_exactly"] is True
        owner = db_row(User, id=company["owner_id"])
        assert owner["role"] == "company_owner" and owner["email"] == body["owner_email"] and owner["password"] != OWNER_PASSWORD

        # the lead: won by the setup, with the whole trail
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "won"
        events = activities(mgr, lead["id"])
        kinds = [e["event_type"] for e in events]
        assert kinds[-3:] == ["stage_changed", "lead_marked_won", "lead_converted"]
        assert events[-3]["before"]["pipeline_stage"] == "assigned" and events[-3]["metadata"]["cause"] == "customer_setup"
        converted = events[-1]
        assert converted["metadata"]["flow"] == "customer_setup" and converted["metadata"]["won_from"] == "assigned"
        assert converted["after"]["subscription_type"] == "trial" and converted["after"]["employees_added"] == 0
        audit = audit_events(customer["id"])
        assert [e["action"] for e in audit] == ["customer_setup_completed"]
        assert audit[0]["actor_user_id"] == p["employee"]["id"] and audit[0]["target_user_id"] == str(owner["id"])
        assert "password" not in json.dumps(events).lower() and "password" not in json.dumps(audit).lower()
        assert OWNER_PASSWORD not in json.dumps(events) + json.dumps(audit)
        assert queue(emp)["lead"] is None or queue(emp)["lead"]["id"] != lead["id"]  # decided: out of the queue

    def test_paid_with_employees_and_a_task(self, p, mgr, plan):
        emp = H(p, "employee")
        lead = assigned_lead(mgr, p["employee"]["id"])
        set_stage(emp, lead["id"], "interested")
        people = [employee_entry(), employee_entry(position=None, department="Sales")]
        due = (today_utc() + timedelta(days=3)).isoformat()
        tasks = [{"title": "Upload the product catalogue", "description": "Photos and prices", "priority": "high", "assignee": 1, "due_date": due}]
        r, body = setup(emp, lead, plan, subscription_type="paid", employees=people, tasks=tasks)
        out = r.json()
        assert out["employees_added"] == 2 and out["tasks_created"] == 1
        sub = out["subscription"]
        assert sub["type"] == "paid" and sub["ends_exactly"] is False                # calendar dates, the end day included
        assert sub["start_date"] == today_utc().isoformat()
        assert sub["end_date"] == (today_utc() + timedelta(days=90)).isoformat()      # the plan's 3 months x 30 days
        assert EMPLOYEE_PASSWORD not in r.text and OWNER_PASSWORD not in r.text
        company_id = uuid.UUID(out["customer"]["company"]["id"])
        company = db_row(Company, id=company_id)
        assert company["subscription_end_date"].date() == today_utc() + timedelta(days=90)
        assert company["subscription_ends_exactly"] is False
        accounts = [db_row(User, email=person["email"]) for person in people]
        for account, person in zip(accounts, people):
            assert account["role"] == "employee" and account["company_id"] == company_id and account["status"] == "active"
            assert account["password"] != EMPLOYEE_PASSWORD and account["phone"] == person["phone"]
        assert accounts[1]["department"] == "Sales" and accounts[0]["position"] == "Cashier"

        async def _load(db):
            from sqlalchemy import select
            rows = (await db.execute(select(Task).where(Task.company_id == company_id))).scalars().all()
            return [(t.title, t.assigned_to, t.created_by, t.priority, t.due_date, t.status) for t in rows]

        [(title, assignee, creator, priority, due_date, status)] = run_db(_load)
        assert title == "Upload the product catalogue" and assignee == accounts[1]["id"] and priority == "high"
        assert creator == company["owner_id"] and due_date.isoformat() == due and status == "new"
        events = activities(mgr, lead["id"])
        assert events[-1]["metadata"]["won_from"] == "interested" and events[-1]["after"]["tasks_created"] == 1
        audit = audit_events(out["customer"]["id"])[0]
        assert sorted(audit["metadata"]["employee_user_ids"]) == sorted(str(a["id"]) for a in accounts)

    def test_a_won_lead_is_set_up_without_moving_it_again(self, p, mgr, plan):
        lead = won_lead(mgr, p["employee"]["id"])
        pre = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", mgr, {}).json()
        assert pre["marks_won"] is False and pre["can_setup"] is True
        before = [e["event_type"] for e in activities(mgr, lead["id"])]
        setup(mgr, lead, plan)
        after = [e["event_type"] for e in activities(mgr, lead["id"])]
        assert after == before + ["lead_converted"]

    def test_repeating_it_creates_nothing(self, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), lead, plan)
        again = api("POST", f"/api/sales/leads/{lead['id']}/setup", H(p, "employee"), setup_body(lead, plan))
        assert again.status_code == 200 and again.json()["already_converted"] is True
        assert again.json()["customer"]["id"] == r.json()["customer"]["id"]
        assert count_companies(lead["business_name"]) == 1
        pre = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", H(p, "employee"), {}).json()
        assert pre["already_converted"] is True and pre["can_setup"] is False

    # ---- what stops it (and then nothing at all is written) ------------------------------------------------------
    def _untouched(self, mgr, lead, body, existing_emails=()):
        assert count_companies(body["business_name"]) == 0
        assert count_users(body["owner_email"]) == (1 if body["owner_email"] in existing_emails else 0)
        for person in body.get("employees", []):
            assert count_users(person["email"]) == (1 if person["email"] in existing_emails else 0)   # no account was added
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == lead["pipeline_stage"]

    def test_tasks_need_an_employee(self, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), lead, plan, expect=400, tasks=[{"title": "x", "assignee": 0, "due_date": "2030-01-01"}])
        assert errors_of(r)["code"] == "tasks_need_employees"
        self._untouched(mgr, lead, body)

    @pytest.mark.parametrize("case", ["repeated_owner_email", "repeated_between_employees", "registered_email", "registered_phone",
                                      "invalid_phone", "weak_password"])
    def test_every_employee_problem_is_reported_and_nothing_is_created(self, p, mgr, plan, case):
        lead = assigned_lead(mgr, p["employee"]["id"])
        owner_email = unique_lead_email("owner")
        shared = unique_lead_email("dup")
        people = {
            "repeated_owner_email": [employee_entry(email=owner_email.upper())],
            "repeated_between_employees": [employee_entry(email=shared), employee_entry(email=shared)],
            "registered_email": [employee_entry(email=p["manager"]["email"])],
            "registered_phone": [employee_entry(phone=p["manager"]["phone"])],
            "invalid_phone": [employee_entry(phone="abc@x")],
            "weak_password": [employee_entry(password="123")],
        }[case]
        r, body = setup(H(p, "employee"), lead, plan, expect=400, owner_email=owner_email, employees=people)
        detail = errors_of(r)
        assert detail["code"] == "employees_invalid"
        codes = {x["code"] for x in detail["problems"]}
        expected = {
            "repeated_owner_email": "employee_email_repeated", "repeated_between_employees": "employee_email_repeated",
            "registered_email": "employee_email_registered", "registered_phone": "employee_phone_registered",
            "invalid_phone": "employee_phone_invalid", "weak_password": "employee_password_weak",
        }[case]
        assert expected in codes
        assert "123" not in json.dumps(detail) and EMPLOYEE_PASSWORD not in r.text
        self._untouched(mgr, lead, body, existing_emails={p["manager"]["email"]})

    def test_the_plans_employee_limit(self, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), lead, plan, expect=400, employees=[employee_entry() for _ in range(8)])   # plan: 7
        assert errors_of(r)["code"] == "employees_over_plan_limit" and errors_of(r)["max_employees"] == 7
        self._untouched(mgr, lead, body)

    @pytest.mark.parametrize("task, code", [
        ({"title": "x", "assignee": 3, "due_date": "2030-01-01"}, "task_assignee_invalid"),
        ({"title": "x", "assignee": 0, "due_date": "2001-01-01"}, "task_due_date_past"),
    ])
    def test_task_problems(self, p, mgr, plan, task, code):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), lead, plan, expect=400, employees=[employee_entry()], tasks=[task])
        assert errors_of(r)["code"] == "tasks_invalid" and errors_of(r)["problems"][0]["code"] == code
        self._untouched(mgr, lead, body)

    def test_the_owner_account_checks(self, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), lead, plan, expect=400, owner_email=p["manager"]["email"])
        assert errors_of(r)["code"] == "owner_email_registered"
        self._untouched(mgr, lead, body, existing_emails={p["manager"]["email"]})
        r, body = setup(H(p, "employee"), lead, plan, expect=400, owner_password="short")
        assert errors_of(r)["code"] == "owner_password_weak"
        r, body = setup(H(p, "employee"), lead, plan, expect=400, subscription_plan_id=make_plan(active=False))
        assert errors_of(r)["code"] == "plan_not_available"
        self._untouched(mgr, lead, body)

    def test_an_existing_company_blocks_and_a_possible_duplicate_needs_confirmation(self, p, mgr, plan):
        first = assigned_lead(mgr, p["employee"]["id"])
        r, body = setup(H(p, "employee"), first, plan)
        # the same owner phone, written differently: the same person -> an exact duplicate, blocked
        second = assigned_lead(mgr, p["employee"]["id"])
        r, body2 = setup(H(p, "employee"), second, plan, expect=409, owner_phone=phone_variant(body["owner_phone"]))
        assert errors_of(r)["code"] == "company_exists" and errors_of(r)["duplicates"]["has_exact"] is True
        self._untouched(mgr, second, body2)
        # the same business name only: possibly the same business -> confirm to continue
        third = assigned_lead(mgr, p["employee"]["id"])
        r, body3 = setup(H(p, "employee"), third, plan, expect=409, business_name=first["business_name"])
        assert errors_of(r)["code"] == "duplicates_found"
        assert count_users(body3["owner_email"]) == 0 and get_lead(mgr, third["id"])["pipeline_stage"] == "assigned"
        r, _ = setup(H(p, "employee"), third, plan, business_name=first["business_name"], confirm_duplicates=True)
        assert activities(mgr, third["id"])[-1]["metadata"]["duplicate_override"]["possible"] >= 1

    @pytest.mark.parametrize("state, code", [("lost", "lead_lost"), ("unassigned", "lead_unassigned"), ("archived", "lead_archived")])
    def test_a_lead_that_cannot_be_set_up(self, p, mgr, plan, state, code):
        lead = create_lead(mgr)
        if state != "unassigned":
            assign(mgr, lead["id"], p["employee"]["id"])
        if state == "lost":
            set_stage(mgr, lead["id"], "lost", lost_reason="other")
        if state == "archived":
            api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        r = api("POST", f"/api/sales/leads/{lead['id']}/setup", mgr, setup_body(lead, plan))
        assert r.status_code == 409 and errors_of(r)["code"] == code
        pre = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", mgr, {}).json()
        assert pre["can_setup"] is False and code in {b["code"] for b in pre["blockers"]}
        assert count_companies(lead["business_name"]) == 0

    # ---- who may do it, to which lead ----------------------------------------------------------------------------
    @pytest.mark.parametrize("who", ["employee2", "data_entry", "onboarding", "no_roles"])
    def test_nobody_else_sets_up_this_lead(self, p, mgr, plan, who):
        lead = assigned_lead(mgr, p["employee"]["id"])                                # employee's lead
        body = setup_body(lead, plan, employees=[employee_entry()])
        for path, payload in ((f"/api/sales/leads/{lead['id']}/setup", body), (f"/api/sales/leads/{lead['id']}/setup/preflight", {})):
            r = api("POST", path, H(p, who), payload)
            assert r.status_code == 403 and r.json()["detail"] == "Access denied", (who, path)
        self._untouched(mgr, lead, body)

    def test_the_phase_4_conversion_stays_the_managers(self, p, mgr, plan):
        """The Customer Setup has its own key; the Phase-4 conversion (a company with NO subscription dates - never expiring)
        is not open to a Sales Employee, not even on their own won lead."""
        me = api("GET", "/api/sales/me", H(p, "employee")).json()["permissions"]
        assert "sales.customers.setup" in me and "sales.customers.convert" not in me
        lead = won_lead(mgr, p["employee"]["id"])
        body = {"business_name": lead["business_name"], "owner_name": "O", "owner_email": unique_lead_email("owner"),
                "owner_phone": unique_lead_phone(), "owner_password": OWNER_PASSWORD, "subscription_plan_id": plan}
        for path, payload in ((f"/api/sales/leads/{lead['id']}/convert", body), (f"/api/sales/leads/{lead['id']}/convert/preflight", {})):
            r = api("POST", path, H(p, "employee"), payload)
            assert r.status_code == 403 and r.json() == {"detail": "Access denied"}, path
        assert count_companies(lead["business_name"]) == 0 and count_users(body["owner_email"]) == 0
        assert api("GET", f"/api/sales/leads/{lead['id']}/conversion", H(p, "employee")).json()["can_convert"] is False

    @pytest.mark.parametrize("keys, allowed", [
        (["sales.access", "sales.leads.view", "sales.leads.scope_all", "sales.customers.setup", "sales.leads.change_stage"], True),
        (["sales.access", "sales.leads.view", "sales.leads.scope_all", "sales.customers.setup"], False),         # cannot win the lead
        (["sales.access", "sales.leads.view", "sales.leads.scope_all", "sales.leads.change_stage"], False),      # no setup key
        (["sales.access", "sales.leads.view", "sales.leads.scope_all", "sales.leads.change_stage", "sales.customers.convert"], False),
    ])
    def test_the_setup_needs_both_of_its_keys(self, admin_h, p, mgr, plan, keys, allowed):
        role = make_custom_role(keys)
        try:
            person = create_staff(admin_h, [role], "wf-setup-keys")
            lead = assigned_lead(mgr, p["employee"]["id"])
            pre = api("POST", f"/api/sales/leads/{lead['id']}/setup/preflight", person["headers"], {})
            r = api("POST", f"/api/sales/leads/{lead['id']}/setup", person["headers"], setup_body(lead, plan))
            if allowed:
                assert pre.status_code == 200 and r.status_code == 201
            else:
                assert pre.status_code == 403 and r.status_code == 403 and r.json() == {"detail": "Access denied"}
                assert count_companies(lead["business_name"]) == 0
        finally:
            retire_roles([role])

    def test_unknown_and_malformed_leads(self, mgr, plan):
        body = setup_body({"business_name": f"Ghost {uniq()}"}, plan)
        for lead_id in (PHANTOM_ID, "not-a-uuid"):
            assert api("POST", f"/api/sales/leads/{lead_id}/setup", mgr, body).status_code == 404
            assert api("POST", f"/api/sales/leads/{lead_id}/setup/preflight", mgr, {}).status_code == 404

    @pytest.mark.parametrize("field", ["company_id", "status", "customer_status", "role", "assigned_to", "converted_by"])
    def test_smuggled_fields_are_refused_without_echoing_passwords(self, p, mgr, plan, field):
        lead = assigned_lead(mgr, p["employee"]["id"])
        body = setup_body(lead, plan, employees=[employee_entry()], **{field: "x"})
        r = api("POST", f"/api/sales/leads/{lead['id']}/setup", H(p, "employee"), body)
        assert r.status_code == 422
        assert OWNER_PASSWORD not in r.text and EMPLOYEE_PASSWORD not in r.text
        self._untouched(mgr, lead, body)

    def test_the_super_admin_may(self, admin_h, p, mgr, plan):
        lead = assigned_lead(mgr, p["employee2"]["id"])
        setup(admin_h, lead, plan)


# =============================================================================
# a lead is won ONLY by the Customer Setup - no Sales role can move it to `won` by hand
# =============================================================================
def stage_change(headers, lead_id, stage, **extra):
    return api("POST", f"/api/sales/leads/{lead_id}/stage", headers, {"stage": stage, **extra})


def in_stage(mgr, p, stage, employee="employee", **overrides) -> dict:
    """A fresh lead owned by the employee and moved (by the manager) to a working `stage`."""
    lead = assigned_lead(mgr, p[employee]["id"], **overrides)
    if stage != "assigned":
        set_stage(mgr, lead["id"], stage)
    return get_lead(mgr, lead["id"])


def attempts(mgr, lead_id) -> list:
    """The lead's decisions as the Sales Manager's trace shows them (accepted / rejected / wait list)."""
    r = api("GET", f"/api/sales/batches/trace/{lead_id}", mgr)
    assert r.status_code == 200, r.text[:200]
    return r.json()["attempts"]


@pytest.fixture(scope="module")
def powers(admin_h):
    """Two custom-role accounts - the most permissive ones a role can be: one holding EVERY permission of the catalog (all that the
    Super Admin has), one holding what the removed manual-win privilege needed (change stages, the all-leads scope and the
    sales.customers.convert key). Neither may win a lead by hand: the rule is not a permission."""
    roles = [
        make_custom_role(sorted(ALL_PERMISSION_KEYS)),
        make_custom_role(["sales.access", "sales.leads.view", "sales.leads.scope_all", "sales.leads.change_stage", "sales.customers.convert"]),
    ]
    people = {"everything": create_staff(admin_h, [roles[0]], "wf-everything"), "converter": create_staff(admin_h, [roles[1]], "wf-converter")}
    yield people
    retire_roles(roles)


def headers_of(who, p, admin_h, powers):
    if who == "admin":
        return admin_h
    return powers[who]["headers"] if who in powers else H(p, who)


# who is refused BY THE RULE (they may move stages on this lead, `won` is what is not allowed) ...
RULE_ROLES = ["manager", "admin", "employee", "everything", "converter"]
# ... and who is refused earlier still, by the ordinary guards (no stage permission at all, or somebody else's lead)
GUARDED_ROLES = ["employee2", "data_entry", "data_entry2", "onboarding", "no_roles"]


class TestOnlyTheCustomerSetupWinsALead:
    """A lead becomes `won` in exactly one way: the Customer Setup ("Agreed"), which wins it in the same transaction that creates its
    customer, its JAZ company and its dated subscription. The pipeline's own `won` move (POST /leads/{id}/stage) is closed to EVERY
    caller - Sales Employee, Sales Manager, Super Admin, a custom role holding every permission - and no lead lists `won` among its
    allowed stages. The refusal is a rule of the pipeline, not a permission: nothing a role can be granted opens it."""

    WORKING = ["assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation"]

    @pytest.mark.parametrize("stage", WORKING)
    @pytest.mark.parametrize("who", RULE_ROLES)
    def test_nobody_who_moves_stages_can_move_a_lead_to_won(self, admin_h, p, mgr, powers, who, stage):
        lead = in_stage(mgr, p, stage)
        timeline = [e["seq"] for e in activities(mgr, lead["id"])]
        r = stage_change(headers_of(who, p, admin_h, powers), lead["id"], "won", note="signed")
        assert r.status_code == 403, (who, r.text[:200])
        detail = errors_of(r)
        assert detail["field"] == "stage" and detail["code"] == "won_requires_customer_setup" and "Customer Setup" in detail["message"]
        assert get_lead(mgr, lead["id"]) == lead                                          # not one field moved: stage, closed_at, updated_at ...
        assert [e["seq"] for e in activities(mgr, lead["id"])] == timeline               # ... nothing on the timeline ...
        assert attempts(mgr, lead["id"]) == [] and count_customers(lead["id"]) == 0      # ... no accepted decision, no customer

    @pytest.mark.parametrize("who", GUARDED_ROLES)
    def test_everybody_else_is_stopped_by_the_ordinary_guards(self, admin_h, p, mgr, powers, who):
        lead = in_stage(mgr, p, "negotiation")
        r = stage_change(headers_of(who, p, admin_h, powers), lead["id"], "won")
        assert r.status_code == 403 and r.json() == {"detail": "Access denied"}          # nothing learned about the lead or the rule
        assert get_lead(mgr, lead["id"]) == lead and attempts(mgr, lead["id"]) == [] and count_customers(lead["id"]) == 0

    def test_the_refusal_comes_before_every_other_check(self, admin_h, p, mgr):
        # not from a state where `won` was ever "allowed": a new lead, a lost one, a won one, an archived one - and with a reason attached
        new, lost = create_lead(mgr), in_stage(mgr, p, "contacted")
        set_stage(mgr, lost["id"], "lost", lost_reason="not_interested")
        won = won_lead(mgr, p["employee"]["id"])
        archived = in_stage(mgr, p, "contacted")
        assert api("DELETE", f"/api/sales/leads/{archived['id']}", mgr).status_code == 200
        for headers in (mgr, admin_h):
            for lead in (new, lost, won, archived):
                r = stage_change(headers, lead["id"], "won")
                assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup", lead["pipeline_stage"]
            r = stage_change(headers, in_stage(mgr, p, "contacted")["id"], "won", lost_reason="other")     # even a malformed ask gets the same answer
            assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup"

    @pytest.mark.parametrize("who", ["manager", "admin", "employee", "everything"])
    def test_won_is_offered_to_nobody_on_any_lead(self, admin_h, p, mgr, powers, who):
        headers = headers_of(who, p, admin_h, powers)
        campaign = create_campaign(mgr)
        leads = {stage: in_stage(mgr, p, stage, campaign_id=campaign["id"]) for stage in self.WORKING}
        leads["new"] = create_lead(mgr, campaign_id=campaign["id"])
        leads["lost"] = in_stage(mgr, p, "contacted", campaign_id=campaign["id"])
        set_stage(mgr, leads["lost"]["id"], "lost", lost_reason="not_interested")
        leads["won"] = won_lead(mgr, p["employee"]["id"], campaign_id=campaign["id"])
        listed = {i["id"]: i for i in api("GET", f"/api/sales/leads?campaign_id={campaign['id']}&limit=100", headers).json()["items"]}
        assert len(leads) == 10
        for stage, lead in leads.items():
            r = api("GET", f"/api/sales/leads/{lead['id']}", headers)
            if r.status_code == 403:                                                       # an employee does not see a lead nobody owns
                assert who == "employee" and stage == "new" and lead["id"] not in listed
                continue
            detail = r.json()
            assert "won" not in detail["allowed_stages"], (who, stage)
            if lead["id"] in listed:                                                       # (an employee lists only their own leads)
                assert listed[lead["id"]]["allowed_stages"] == detail["allowed_stages"], (who, stage)
        # not vacuous: they can still move it - just never to won
        negotiation = get_lead(headers, leads["negotiation"]["id"])["allowed_stages"]
        assert negotiation == ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "lost"]

    def test_the_employees_queue_offers_no_won_either(self, admin_h, mgr):
        solo = create_staff(admin_h, ["sales_employee"], "wf-solo")                       # their queue holds exactly this lead
        lead = assigned_lead(mgr, solo["id"])
        set_stage(mgr, lead["id"], "negotiation")
        queued = queue(solo["headers"])["lead"]
        assert queued["id"] == lead["id"] and queued["allowed_stages"] == ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "lost"]

    @pytest.mark.parametrize("who", ["employee", "manager", "admin"])
    def test_the_customer_setup_wins_it_and_creates_the_customer_in_the_same_step(self, admin_h, p, mgr, who):
        headers = headers_of(who, p, admin_h, {})
        lead = in_stage(mgr, p, "negotiation")
        body = agree(headers, lead)
        assert body["already_converted"] is False and count_customers(lead["id"]) == 1
        won = get_lead(mgr, lead["id"])
        assert won["pipeline_stage"] == "won" and won["closed_at"] and won["allowed_stages"] == []
        events = activities(mgr, lead["id"])
        assert [e["event_type"] for e in events][-3:] == ["stage_changed", "lead_marked_won", "lead_converted"]
        assert events[-2]["metadata"]["cause"] == "customer_setup"
        actor = {"employee": p["employee"]["id"], "manager": p["manager"]["id"]}.get(who)
        if actor:
            assert events[-2]["actor"]["id"] == actor
        accepted = attempts(mgr, lead["id"])                                                # the OWNER's accepted decision, whoever pressed it
        assert [a["path"] for a in accepted] == ["direct_accepted"] and accepted[0]["employee"]["id"] == p["employee"]["id"]
        if actor:
            assert accepted[0]["recorded_by"]["id"] == actor
        customer = db_row(SalesCustomer, id=uuid.UUID(body["customer"]["id"]))
        assert customer["lead_id"] == uuid.UUID(lead["id"]) and customer["subscription_type"] == "trial" and customer["status"] == "activated"
        assert audit_events(body["customer"]["id"])[0]["action"] == "customer_setup_completed"

    def test_every_lead_the_customer_setup_won_has_its_customer_and_a_dated_subscription(self):
        """The database-wide invariant behind "won only once the customer exists": a lead whose win carries the Customer Setup's cause
        has its customer - written in the same transaction, so never one without the other - and that customer's company has a start
        AND an end date. (Leads won by hand before the rule have no such cause; they are not part of it.)"""
        from sqlalchemy import text

        async def _orphans(db):
            return (await db.execute(text(
                "SELECT count(*) FROM sales_lead_activities a "
                "WHERE a.event_type = 'lead_marked_won' AND a.metadata ->> 'cause' = 'customer_setup' AND NOT EXISTS ("
                "  SELECT 1 FROM sales_customers c JOIN companies co ON co.id = c.company_id "
                "  WHERE c.lead_id = a.lead_id AND co.subscription_start_date IS NOT NULL AND co.subscription_end_date IS NOT NULL)"
            ))).scalar_one()

        assert run_db(_orphans) == 0

    def test_a_setup_that_fails_wins_nothing(self, p, mgr):
        lead = in_stage(mgr, p, "negotiation")
        timeline = [e["seq"] for e in activities(mgr, lead["id"])]
        agree(H(p, "employee"), lead, expect=400, owner_password="weak")                   # refused: the owner's password is too weak
        assert get_lead(mgr, lead["id"]) == lead and [e["seq"] for e in activities(mgr, lead["id"])] == timeline
        assert attempts(mgr, lead["id"]) == [] and count_customers(lead["id"]) == 0        # not won, no decision, no customer
        agree(H(p, "employee"), lead)                                                       # ... and the same lead completes once it is right
        assert get_lead(mgr, lead["id"])["pipeline_stage"] == "won" and count_customers(lead["id"]) == 1

    def test_a_lead_won_before_the_rule_can_still_get_its_customer(self, p, mgr):
        """Leads won by hand BEFORE the rule exist in every deployed database: the Customer Setup still completes them (it does not win
        them a second time), and the historical accepted decision stays the only one."""
        lead = won_lead(mgr, p["employee"]["id"])                                           # seeded: won by hand, no customer
        assert [a["path"] for a in attempts(mgr, lead["id"])] == ["direct_accepted"]
        events_before = [e["event_type"] for e in activities(mgr, lead["id"])]
        body = agree(H(p, "employee"), lead)
        assert body["already_converted"] is False and count_customers(lead["id"]) == 1
        assert [e["event_type"] for e in activities(mgr, lead["id"])] == events_before + ["lead_converted"]     # no second win, no second event
        assert [a["path"] for a in attempts(mgr, lead["id"])] == ["direct_accepted"]

    def test_the_other_moves_are_unchanged(self, p, mgr):
        lead = in_stage(mgr, p, "assigned")
        e = H(p, "employee")
        assert stage_change(e, lead["id"], "contacted").status_code == 200
        assert stage_change(e, lead["id"], "negotiation").status_code == 200
        assert stage_change(e, lead["id"], "won", note="x").status_code == 403
        assert stage_change(e, lead["id"], "lost", lost_reason="not_interested").status_code == 200      # "Not Interested" is still theirs
        assert stage_change(e, lead["id"], "assigned").status_code == 200                                  # ... and so is reopening it
        assert stage_change(mgr, lead["id"], "trial").status_code == 200                                   # ... and the manager's moves
        assert stage_change(mgr, lead["id"], "lost", lost_reason="other").status_code == 200

    def test_a_wait_listed_lead_cannot_be_won_by_hand_but_the_setup_ends_the_wait(self, p, mgr):
        lead = in_stage(mgr, p, "contacted")
        e = H(p, "employee")
        assert api("POST", f"/api/sales/leads/{lead['id']}/wait-list", e).status_code == 200
        for headers in (e, mgr):
            r = stage_change(headers, lead["id"], "won")
            assert r.status_code == 403 and errors_of(r)["code"] == "won_requires_customer_setup"
        assert [a["path"] for a in attempts(mgr, lead["id"])] == ["wait_pending"]         # still waiting: the refused moves changed nothing
        assert get_lead(e, lead["id"])["wait_listed_at"] is not None
        agree(e, get_lead(mgr, lead["id"]))
        assert [a["path"] for a in attempts(mgr, lead["id"])] == ["wait_accepted"]        # the Customer Setup is what accepts it

    def test_a_reopened_lost_lead_still_cannot_be_won_by_hand(self, p, mgr):
        lead = in_stage(mgr, p, "contacted")
        e = H(p, "employee")
        assert stage_change(e, lead["id"], "lost", lost_reason="not_interested").status_code == 200
        assert stage_change(e, lead["id"], "assigned").status_code == 200
        assert stage_change(e, lead["id"], "contacted").status_code == 200
        for headers in (e, mgr):
            assert errors_of(stage_change(headers, lead["id"], "won"))["code"] == "won_requires_customer_setup"

    @pytest.mark.parametrize("who", ["manager", "admin", "employee"])
    def test_no_other_route_can_set_a_leads_stage(self, admin_h, p, mgr, who):
        """Not the edit (the schema refuses unknown fields), not the create, not the retired conversion."""
        headers = headers_of(who, p, admin_h, {})
        lead = in_stage(mgr, p, "negotiation")
        for forbidden in ({"pipeline_stage": "won"}, {"stage": "won"}, {"closed_at": "2026-01-01T00:00:00Z"}):
            assert api("PATCH", f"/api/sales/leads/{lead['id']}", headers, forbidden).status_code == 422, forbidden
        if who != "employee":                                                              # (an employee cannot create leads)
            assert api("POST", "/api/sales/leads", headers, {**lead_body(), "pipeline_stage": "won"}).status_code == 422
        r = api("POST", f"/api/sales/leads/{lead['id']}/convert", headers, {"business_name": lead["business_name"]})
        assert r.status_code in (403, 410) and count_customers(lead["id"]) == 0
        assert get_lead(mgr, lead["id"]) == lead


# =============================================================================
# Lead Data Entry: the latest 10 leads
# =============================================================================
class TestDataEntryWindow:
    @pytest.fixture(scope="class")
    def entry(self, admin_h):
        """A Lead Data Entry user and the 11 leads they created, oldest first."""
        person = create_staff(admin_h, ["lead_data_entry"], "wf-entry")
        return {"person": person, "leads": [create_lead(person["headers"]) for _ in range(11)]}

    def test_the_oldest_is_manager_only(self, entry):
        h, oldest = entry["person"]["headers"], entry["leads"][0]
        seen = get_lead(h, oldest["id"])
        assert seen["can"]["update"] is False and seen["can"]["archive"] is False and seen["can"]["edit_locked"] is True
        r = api("PATCH", f"/api/sales/leads/{oldest['id']}", h, {"notes": "too late"})
        assert r.status_code == 403 and errors_of(r)["code"] == "lead_edit_window"
        r = api("DELETE", f"/api/sales/leads/{oldest['id']}", h)
        assert r.status_code == 403 and errors_of(r)["code"] == "lead_edit_window"
        assert get_lead(h, oldest["id"])["notes"] is None and get_lead(h, oldest["id"])["archived_at"] is None

    def test_the_latest_ten_are_theirs_to_edit_archive_and_restore(self, entry):
        h = entry["person"]["headers"]
        for lead in entry["leads"][1:]:
            assert get_lead(h, lead["id"])["can"] == {"update": True, "change_stage": False, "assign": False, "archive": True,
                                                      "restore": False, "edit_locked": False, "wait_list": False}
        newest = entry["leads"][-1]
        r = api("PATCH", f"/api/sales/leads/{newest['id']}", h, {"notes": "typo fixed"})
        assert r.status_code == 200 and r.json()["notes"] == "typo fixed"
        r = api("DELETE", f"/api/sales/leads/{newest['id']}", h)
        assert r.status_code == 200 and r.json()["changed"] is True and r.json()["lead"]["can"]["restore"] is True
        r = api("POST", f"/api/sales/leads/{newest['id']}/restore", h)                  # archiving does not push it out of the window
        assert r.status_code == 200 and r.json()["changed"] is True

    def test_the_list_says_the_same(self, entry):
        h = entry["person"]["headers"]
        mine = {lead["id"] for lead in entry["leads"]}
        items = [i for i in api("GET", "/api/sales/leads?limit=100", h).json()["items"] if i["id"] in mine]
        locked = {i["id"] for i in items if i["can"]["edit_locked"]}
        assert locked == {entry["leads"][0]["id"]}

    def test_the_manager_overrides(self, entry, mgr):
        oldest = entry["leads"][0]
        r = api("PATCH", f"/api/sales/leads/{oldest['id']}", mgr, {"notes": "manager fixed it"})
        assert r.status_code == 200 and r.json()["can"]["edit_locked"] is False
        assert api("DELETE", f"/api/sales/leads/{oldest['id']}", mgr).status_code == 200
        assert api("POST", f"/api/sales/leads/{oldest['id']}/restore", mgr).status_code == 200
        # ...and a manager-archived old lead stays out of Data Entry's reach
        api("DELETE", f"/api/sales/leads/{oldest['id']}", mgr)
        r = api("POST", f"/api/sales/leads/{oldest['id']}/restore", entry["person"]["headers"])
        assert r.status_code == 403 and errors_of(r)["code"] == "lead_edit_window"
        api("POST", f"/api/sales/leads/{oldest['id']}/restore", mgr)

    def test_somebody_elses_lead_is_never_in_the_window(self, entry, p):
        other = create_lead(H(p, "data_entry2"))                                          # unassigned: visible to every intake user
        h = entry["person"]["headers"]
        assert api("GET", f"/api/sales/leads/{other['id']}", h).status_code == 200
        assert api("PATCH", f"/api/sales/leads/{other['id']}", h, {"notes": "x"}).status_code == 403
        assert api("DELETE", f"/api/sales/leads/{other['id']}", h).status_code == 403

    def test_the_window_follows_new_leads(self, entry):
        h = entry["person"]["headers"]
        second = entry["leads"][1]
        assert api("PATCH", f"/api/sales/leads/{second['id']}", h, {"notes": "still mine"}).status_code == 200
        create_lead(h)                                                                    # one more: the second-oldest leaves the window
        r = api("PATCH", f"/api/sales/leads/{second['id']}", h, {"notes": "not any more"})
        assert r.status_code == 403 and errors_of(r)["code"] == "lead_edit_window"

    def test_a_sales_employee_keeps_editing_their_assigned_leads(self, p, mgr):
        lead = assigned_lead(mgr, p["employee"]["id"])
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", H(p, "employee"), {"notes": "called"}).status_code == 200


class TestDataEntryLosesALeadOnceItIsAssigned:
    """Data Entry edits, archives, deletes and restores only THEIR OWN latest ten leads AND only while such a lead is still
    UNASSIGNED. Once a salesperson holds it (a manager assigned it, a bulk assignment, ...) it is theirs: Data Entry still SEES the
    lead they entered (read-only, `edit_locked`), and the server refuses every change - including a restore. Unassigning it gives
    it back (inside the latest ten). The Sales Manager's permissions do not change."""

    LOCKED = {"update": False, "change_stage": False, "assign": False, "archive": False, "restore": False, "edit_locked": True, "wait_list": False}
    OPEN = {"update": True, "change_stage": False, "assign": False, "archive": True, "restore": False, "edit_locked": False, "wait_list": False}

    @pytest.fixture()
    def entry(self, admin_h):
        return create_staff(admin_h, ["lead_data_entry"], "wf-de-assign")

    @staticmethod
    def refused(response):
        assert response.status_code == 403 and errors_of(response)["code"] == "lead_edit_window", response.text[:200]

    def test_an_unassigned_recent_lead_is_theirs_to_edit_archive_and_restore(self, entry):
        h = entry["headers"]
        lead = create_lead(h)
        assert get_lead(h, lead["id"])["can"] == self.OPEN
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "typo fixed"}).status_code == 200
        r = api("DELETE", f"/api/sales/leads/{lead['id']}", h)
        assert r.status_code == 200 and r.json()["changed"] is True and r.json()["lead"]["can"]["restore"] is True
        r = api("POST", f"/api/sales/leads/{lead['id']}/restore", h)
        assert r.status_code == 200 and r.json()["changed"] is True
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "and again"}).status_code == 200

    def test_assigning_it_takes_it_out_of_their_hands_at_once(self, p, mgr, entry):
        h = entry["headers"]
        lead = create_lead(h)
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "while unassigned"}).status_code == 200
        assign(mgr, lead["id"], p["employee"]["id"])
        seen = api("GET", f"/api/sales/leads/{lead['id']}", h)                            # still theirs to SEE ...
        assert seen.status_code == 200 and seen.json()["can"] == self.LOCKED
        before, timeline = get_lead(mgr, lead["id"]), [e["seq"] for e in activities(mgr, lead["id"])]
        self.refused(api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "too late"}))         # ... not to change
        self.refused(api("DELETE", f"/api/sales/leads/{lead['id']}", h))
        self.refused(api("POST", f"/api/sales/leads/{lead['id']}/restore", h))
        assert get_lead(mgr, lead["id"]) == before                                        # nothing was written ...
        assert [e["seq"] for e in activities(mgr, lead["id"])] == timeline               # ... and nothing recorded
        listed = next(i for i in api("GET", "/api/sales/leads?limit=100", h).json()["items"] if i["id"] == lead["id"])
        assert listed["can"] == self.LOCKED                                               # the list says the same as the detail

    def test_an_assigned_archived_lead_cannot_be_restored_by_data_entry(self, p, mgr, entry):
        h = entry["headers"]
        lead = create_lead(h)
        assign(mgr, lead["id"], p["employee"]["id"])
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200    # a manager archives it
        self.refused(api("POST", f"/api/sales/leads/{lead['id']}/restore", h))
        assert get_lead(mgr, lead["id"])["archived_at"] is not None
        assert api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr).status_code == 200      # the manager restores it: unchanged

    def test_a_lead_archived_while_unassigned_is_still_theirs_to_restore(self, mgr, entry):
        h = entry["headers"]
        lead = create_lead(h)
        assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200    # archived by a manager, nobody holds it
        r = api("POST", f"/api/sales/leads/{lead['id']}/restore", h)
        assert r.status_code == 200 and r.json()["changed"] is True

    def test_a_bulk_assignment_takes_every_lead_out_of_their_hands(self, p, mgr, entry):
        h = entry["headers"]
        leads = [create_lead(h) for _ in range(3)]
        r = api("POST", "/api/sales/leads/bulk-assign", mgr, {"lead_ids": [x["id"] for x in leads], "assigned_to": p["employee"]["id"]})
        assert r.status_code == 200 and r.json()["assigned"] == 3
        for lead in leads:
            assert get_lead(h, lead["id"])["can"] == self.LOCKED
            self.refused(api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "x"}))

    def test_unassigning_it_gives_it_back_inside_the_latest_ten_only(self, p, mgr, entry):
        h = entry["headers"]
        lead = create_lead(h)
        assign(mgr, lead["id"], p["employee"]["id"])
        self.refused(api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "locked"}))
        assert api("POST", f"/api/sales/leads/{lead['id']}/unassign", mgr).status_code == 200
        assert get_lead(h, lead["id"])["can"] == self.OPEN
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "back in their hands"}).status_code == 200
        for _ in range(10):
            create_lead(h)                                                                # ten newer leads: this one leaves the window
        self.refused(api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "outside the latest ten"}))

    def test_the_window_counts_the_latest_ten_they_created_whatever_became_of_them(self, p, mgr, entry):
        """The ten are the newest they CREATED; assigned ones keep their slot (and are locked anyway), so an older unassigned lead
        does not slide back into the window when newer ones are handed to salespeople."""
        h = entry["headers"]
        oldest = create_lead(h)
        newer = [create_lead(h) for _ in range(10)]
        assert api("POST", "/api/sales/leads/bulk-assign", mgr, {"lead_ids": [x["id"] for x in newer], "assigned_to": p["employee"]["id"]}).status_code == 200
        self.refused(api("PATCH", f"/api/sales/leads/{oldest['id']}", h, {"notes": "eleventh"}))        # unassigned, but the 11th newest
        for lead in newer:
            self.refused(api("PATCH", f"/api/sales/leads/{lead['id']}", h, {"notes": "assigned"}))     # in the window, but assigned

    def test_somebody_elses_lead_is_never_theirs_assigned_or_not(self, p, mgr, entry):
        other = create_lead(H(p, "data_entry2"))
        h = entry["headers"]
        assert api("GET", f"/api/sales/leads/{other['id']}", h).status_code == 200        # unassigned: visible to every intake user
        assert api("PATCH", f"/api/sales/leads/{other['id']}", h, {"notes": "x"}).status_code == 403
        assign(mgr, other["id"], p["employee"]["id"])
        assert api("GET", f"/api/sales/leads/{other['id']}", h).status_code == 403        # assigned and not theirs: not even visible
        assert api("PATCH", f"/api/sales/leads/{other['id']}", h, {"notes": "x"}).status_code == 403

    def test_the_sales_manager_is_unchanged(self, p, mgr, entry):
        h = entry["headers"]
        lead = create_lead(h)
        for assigned in (False, True):
            if assigned:
                assign(mgr, lead["id"], p["employee"]["id"])
            r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"notes": f"manager edit, assigned={assigned}"})
            assert r.status_code == 200 and r.json()["can"]["edit_locked"] is False and r.json()["can"]["update"] is True
            assert api("DELETE", f"/api/sales/leads/{lead['id']}", mgr).status_code == 200
            assert api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr).status_code == 200

    def test_the_sales_employee_keeps_editing_the_lead_that_is_now_theirs(self, p, mgr, entry):
        lead = create_lead(entry["headers"])
        assign(mgr, lead["id"], p["employee"]["id"])
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", H(p, "employee"), {"notes": "called them"})
        assert r.status_code == 200 and r.json()["notes"] == "called them"


# =============================================================================
# the export
# =============================================================================
def lookup_owner_email() -> str:
    from sales_test_utils import lookup_user
    return lookup_user("owner@demo.com")["email"]


def xlsx_sheet_text(content: bytes, sheet: int) -> str:
    """Every string of one worksheet (shared strings resolved), joined - enough to assert what a sheet mentions."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        strings = [re.sub(r"<[^>]+>", "", si) for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S)]
        xml = z.read(f"xl/worksheets/sheet{sheet}.xml").decode()
    return " | ".join(strings[int(i)] for i in re.findall(r'<c [^>]*t="s"[^>]*><v>(\d+)</v>', xml))


def xlsx_rows(content: bytes):
    """The first sheet's rows as lists of cell texts (shared strings resolved)."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            xml = z.read("xl/sharedStrings.xml").decode()
            strings = [re.sub(r"<[^>]+>", "", si) for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    unescape = lambda s: s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")  # noqa: E731
    rows = []
    for row in re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S):
        cells = []
        for attrs, value in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
            v = re.search(r"<v>(.*?)</v>", value or "")
            if 't="s"' in attrs and v:
                cells.append(unescape(strings[int(v.group(1))]))
            else:
                cells.append(unescape(v.group(1)) if v else "")
        rows.append(cells)
    return rows, sheet


@pytest.fixture(scope="module")
def exported(p, mgr):
    """A campaign of 30 leads (more than one page of 25), a few worked, one with notes and one with a formula-like name."""
    campaign = create_campaign(mgr)
    token = uniq(8)
    leads = [create_lead(mgr, campaign_id=campaign["id"], business_name=f"Export {token} {i:02d}") for i in range(28)]
    leads.append(create_lead(mgr, campaign_id=campaign["id"], business_name=f"=HYPERLINK(\"http://evil.example\",\"x\") {token}"))
    leads.append(create_lead(mgr, campaign_id=campaign["id"], business_name=f"Noted {token}", notes=f"SECRET-NOTE-{token}"))
    for lead in leads[:3]:
        assign(mgr, lead["id"], p["employee"]["id"])
    set_stage(mgr, leads[0]["id"], "lost", lost_reason="too_expensive")
    set_stage(mgr, leads[1]["id"], "lost", lost_reason="other")
    return {"campaign": campaign["id"], "token": token, "ids": [lead["id"] for lead in leads], "leads": leads}


def export(headers, expect=200, **params):
    r = api("GET", "/api/sales/leads/export", headers, None, params=params)
    assert r.status_code == expect, f"export: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r


class TestExport:
    def test_excel_holds_every_matching_lead_not_one_page(self, mgr, exported):
        r = export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"])
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert r.headers["x-export-row-count"] == "30" and "attachment" in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
        rows, sheet = xlsx_rows(r.content)
        header, body = rows[0], rows[1:]
        assert header[:4] == ["Lead ID", "Company", "Business type", "Contact"]
        for column in ("Phone", "City", "Source", "Campaign", "Assigned employee", "Stage / Status", "Created date", "Updated date"):
            assert column in header
        assert len(body) == 30 and {row[0] for row in body} == set(exported["ids"])
        assert "<f>" not in sheet                                                          # "=HYPERLINK(...)" stayed text
        assert any(row[1].startswith("=HYPERLINK") for row in body)

    def test_the_filters_and_the_search_apply(self, p, mgr, exported):
        lost = xlsx_rows(export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"], pipeline_stage="lost").content)[0][1:]
        assert {row[0] for row in lost} == set(exported["ids"][:2])
        owned = xlsx_rows(export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"], assigned_to=p["employee"]["id"]).content)[0][1:]
        assert {row[0] for row in owned} == set(exported["ids"][:3])
        unassigned = export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"], assigned_to="unassigned")
        assert unassigned.headers["x-export-row-count"] == "27"
        assert export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"], pipeline_stage="assigned").headers["x-export-row-count"] == "1"
        searched = xlsx_rows(export(mgr, format="xlsx", lang="en", q=f"Export {exported['token']} 1").content)[0][1:]
        assert len(searched) == 10 and all(row[1].startswith(f"Export {exported['token']} 1") for row in searched)
        assert export(mgr, format="pdf", lang="en", campaign_id=exported["campaign"], pipeline_stage="lost").headers["x-export-row-count"] == "2"

    def test_the_sort_applies(self, mgr, exported):
        rows = xlsx_rows(export(mgr, format="xlsx", lang="en", q=f"Export {exported['token']}", sort="business_name", order="asc").content)[0][1:]
        names = [row[1] for row in rows]
        assert names == sorted(names) and len(names) == 28

    def test_no_internal_or_free_text_fields(self, mgr, exported):
        r = export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"])
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            everything = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist())
        assert f"SECRET-NOTE-{exported['token']}" not in everything
        for leaked in ("_norm", "password", "Staff#12345"):
            assert leaked not in everything

    def test_arabic_excel_is_right_to_left(self, mgr, exported):
        rows, sheet = xlsx_rows(export(mgr, format="xlsx", lang="ar", campaign_id=exported["campaign"]).content)
        assert 'rightToLeft="1"' in sheet and rows[0][1] == "الشركة" and len(rows) == 31
        stages = {row[rows[0].index("المرحلة / الحالة")] for row in rows[1:]}
        assert "خسارة" in stages

    @pytest.mark.parametrize("lang", ["ar", "en"])
    def test_pdf(self, mgr, exported, lang):
        r = export(mgr, format="pdf", lang=lang, campaign_id=exported["campaign"])
        assert r.headers["content-type"] == "application/pdf" and r.headers["x-export-row-count"] == "30"
        assert r.content.startswith(b"%PDF-") and b"IBMPlexSansArabic" in r.content
        assert re.search(rb"/Count (\d+)", r.content)                                     # a page tree (30 rows fit on one or two pages)

    def test_the_filter_summary_names_only_staff(self, mgr, exported, admin_h):
        """Filtering the export by some platform user's id must not turn that id into their name in the file."""
        company_owner = db_row(User, role="company_owner", email=lookup_owner_email())
        r = export(mgr, format="xlsx", lang="en", assigned_to=str(company_owner["id"]))
        assert r.headers["x-export-row-count"] == "0"
        info = xlsx_sheet_text(r.content, 2)
        assert company_owner["name"] not in info and str(company_owner["id"]) in info

    def test_the_export_is_audited(self, p, mgr, exported):
        export(mgr, format="xlsx", lang="en", campaign_id=exported["campaign"], pipeline_stage="lost")

        async def _go(db):
            from sqlalchemy import select
            row = (await db.execute(
                select(StaffAuditEvent).where(StaffAuditEvent.action == "leads_exported", StaffAuditEvent.actor_user_id == uuid.UUID(p["manager"]["id"]))
                .order_by(StaffAuditEvent.seq.desc()).limit(1)
            )).scalar_one()
            return row.event_metadata, row.target_type

        metadata, target = run_db(_go)
        assert target == "sales_leads" and metadata["format"] == "xlsx" and metadata["rows"] == 2
        assert metadata["filters"]["campaign_id"] == exported["campaign"] and metadata["filters"]["stages"] == ["lost"]

    def test_the_export_stays_inside_the_callers_lead_scope(self, admin_h, p, mgr, exported):
        role = make_custom_role(["sales.access", "sales.leads.view", "sales.leads.export", "sales.leads.scope_assigned"])
        try:
            person = create_staff(admin_h, [role], "wf-exporter")
            assign(mgr, exported["ids"][5], person["id"])
            r = export(person["headers"], format="xlsx", lang="en", campaign_id=exported["campaign"])
            assert [row[0] for row in xlsx_rows(r.content)[0][1:]] == [exported["ids"][5]]
            assert export(person["headers"], format="xlsx", lang="en", assigned_to=p["employee"]["id"]).headers["x-export-row-count"] == "0"
        finally:
            retire_roles([role])

    @pytest.mark.parametrize("who", ["employee", "data_entry", "onboarding", "no_roles"])
    def test_only_the_manager_exports(self, p, who):
        r = export(H(p, who), expect=403, format="xlsx")
        assert r.json()["detail"] == "Access denied"
        export(H(p, who), expect=403, format="pdf")

    def test_the_super_admin_may(self, admin_h, exported):
        assert export(admin_h, format="xlsx", lang="en", campaign_id=exported["campaign"]).headers["x-export-row-count"] == "30"

    @pytest.mark.parametrize("params, status", [
        ({}, 422), ({"format": "csv"}, 422), ({"format": "xlsx", "lang": "fr"}, 422), ({"format": "xlsx", "sort": "password"}, 422),
        ({"format": "xlsx", "pipeline_stage": "nonsense"}, 400),
    ])
    def test_bad_requests(self, mgr, params, status):
        export(mgr, expect=status, **params)


# =============================================================================
# onboarding is no longer a section
# =============================================================================
class TestOnboardingHidden:
    def test_no_onboarding_section_for_anybody(self, p, admin_h):
        for who in ("manager", "employee", "data_entry", "onboarding"):
            assert "onboarding" not in api("GET", "/api/sales/me", H(p, who)).json()["modules"], who
        assert "onboarding" not in api("GET", "/api/sales/me", admin_h).json()["modules"]

    def test_the_underlying_api_is_still_there_for_a_later_expansion(self, mgr):
        assert api("GET", "/api/sales/onboarding?limit=1", mgr).status_code == 200


# =============================================================================
# the subscription a setup starts: Paid (no payment step) and Trial (exactly 7 x 24 hours), as the PLATFORM enforces them
# =============================================================================
def set_company(company_id, **values):
    from sqlalchemy import update

    async def _go(db):
        await db.execute(update(Company).where(Company.id == company_id).values(**values))

    run_db(_go)


def owner_access(company_id):
    """What the company's owner gets from an ordinary owner endpoint (every owner / employee request runs the platform's
    subscription check in get_current_user -> enforce_company_access)."""
    owner_id = db_row(Company, id=company_id)["owner_id"]
    return api("GET", "/api/owner/subscription", auth(mint_token(owner_id)))


def wait_past_utc_midnight():
    """Keep a test off the UTC date change: several assertions compare the server's 'today' with the test's. Near 00:00 UTC
    (15 s before, 5 s after) wait until the new day has safely begun."""
    import time
    now = datetime.now(timezone.utc)
    to_midnight = (datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) - now).total_seconds()
    since_midnight = now.hour * 3600 + now.minute * 60 + now.second
    if to_midnight < 15:
        time.sleep(to_midnight + 6)
    elif since_midnight < 5:
        time.sleep(6 - since_midnight)


@pytest.fixture(autouse=True)
def _off_the_utc_date_change():
    wait_past_utc_midnight()


class TestSubscriptionPeriods:
    def _setup(self, p, mgr, plan, kind):
        lead = assigned_lead(mgr, p["employee"]["id"])
        r, _ = setup(H(p, "employee"), lead, plan, subscription_type=kind)
        return uuid.UUID(r.json()["customer"]["company"]["id"]), r.json()

    def test_paid_needs_no_payment_and_the_owner_works_at_once(self, p, mgr, plan):
        company_id, out = self._setup(p, mgr, plan, "paid")
        r = owner_access(company_id)
        assert r.status_code == 200 and r.json()["subscription_status"] == "active"
        company = db_row(Company, id=company_id)
        assert company["subscription_status"] == "active" and company["subscription_ends_exactly"] is False
        assert company["subscription_start_date"].date() == today_utc()

        async def _payments(db):
            from sqlalchemy import func, select
            return (await db.execute(select(func.count()).select_from(PaymentTransaction).where(PaymentTransaction.company_id == company_id))).scalar_one()

        assert run_db(_payments) == 0                                      # no payment record was needed, or made

    @pytest.mark.parametrize("field", ["payment_reference", "paid", "payment_method", "amount_paid", "invoice_number"])
    def test_there_is_no_payment_field_to_send(self, p, mgr, plan, field):
        lead = assigned_lead(mgr, p["employee"]["id"])
        body = setup_body(lead, plan, subscription_type="paid", **{field: "x"})
        assert api("POST", f"/api/sales/leads/{lead['id']}/setup", H(p, "employee"), body).status_code == 422

    def test_an_exact_trial_ends_at_its_instant_for_the_owner(self, p, mgr, plan):
        company_id, _ = self._setup(p, mgr, plan, "trial")
        assert owner_access(company_id).status_code == 200
        wait_past_utc_midnight()
        now = datetime.now(timezone.utc)
        set_company(company_id, subscription_end_date=now + timedelta(hours=1))
        assert owner_access(company_id).status_code == 200                    # an hour left: still on
        ended = now - timedelta(seconds=2)
        assert ended.date() == now.date()                                     # the SAME calendar day: a day-level rule would allow it
        set_company(company_id, subscription_end_date=ended)
        r = owner_access(company_id)
        assert r.status_code == 403 and r.json()["detail"]["field"] == "subscription" and r.json()["detail"]["status"] == "expired"
        # decided afresh on every request (the core's self-heal write is rolled back with the refused request): still refused
        assert owner_access(company_id).status_code == 403

    def test_sales_shows_the_status_the_platform_enforces(self, p, mgr, plan):
        """The customer page must not keep saying 'active' after an exact trial has ended (before any owner request)."""
        company_id, out = self._setup(p, mgr, plan, "trial")
        customer_id = out["customer"]["id"]
        assert api("GET", f"/api/sales/customers/{customer_id}", mgr).json()["company"]["subscription_status"] == "active"
        wait_past_utc_midnight()
        set_company(company_id, subscription_end_date=datetime.now(timezone.utc) - timedelta(seconds=2))
        assert db_row(Company, id=company_id)["subscription_status"] == "active"                   # nothing wrote it yet
        assert api("GET", f"/api/sales/customers/{customer_id}", mgr).json()["company"]["subscription_status"] == "expired"
        listed = api("GET", f"/api/sales/customers?q={out['customer']['company']['name']}&limit=100", mgr).json()["items"]
        assert [c["company"]["subscription_status"] for c in listed if c["id"] == customer_id] == ["expired"]

    def test_a_paid_activation_of_a_trial_runs_to_a_calendar_date(self, p, mgr, plan):
        """The owner's own payment path (services/subscriptions -> repositories/companies.activate_subscription) ends the
        trial's exact-instant rule, like the Super Admin's Activate / Renew."""
        company_id, _ = self._setup(p, mgr, plan, "trial")
        assert db_row(Company, id=company_id)["subscription_ends_exactly"] is True

        async def _pay(db):
            import repositories.companies as companies_repo
            await companies_repo.activate_subscription(db, company_id, uuid.UUID(plan), datetime.now(timezone.utc) + timedelta(days=90))

        run_db(_pay)
        assert db_row(Company, id=company_id)["subscription_ends_exactly"] is False

    def test_a_calendar_day_subscription_still_lasts_its_whole_last_day(self, p, mgr, plan):
        company_id, _ = self._setup(p, mgr, plan, "paid")
        wait_past_utc_midnight()
        midnight = datetime.combine(today_utc(), datetime.min.time(), tzinfo=timezone.utc)
        set_company(company_id, subscription_end_date=midnight)             # "ends today" - already past midnight
        assert owner_access(company_id).status_code == 200
        set_company(company_id, subscription_end_date=midnight - timedelta(seconds=1))   # ended yesterday
        assert owner_access(company_id).status_code == 403

    def test_the_super_admins_activate_and_renew_run_to_calendar_dates(self, admin_h, p, mgr, plan):
        company_id, _ = self._setup(p, mgr, plan, "trial")
        start, end = today_utc().isoformat(), (today_utc() + timedelta(days=30)).isoformat()
        r = api("POST", f"/api/admin/companies/{company_id}/activate", admin_h, {"subscription_start_date": start, "subscription_end_date": end})
        assert r.status_code == 200 and db_row(Company, id=company_id)["subscription_ends_exactly"] is False
        company_id, _ = self._setup(p, mgr, plan, "trial")
        assert api("POST", f"/api/admin/companies/{company_id}/renew", admin_h, {}).status_code == 200
        renewed = db_row(Company, id=company_id)
        assert renewed["subscription_ends_exactly"] is False and renewed["subscription_status"] == "active"

    def test_an_ended_exact_trial_cannot_simply_be_reactivated(self, admin_h, p, mgr, plan):
        company_id, _ = self._setup(p, mgr, plan, "trial")
        wait_past_utc_midnight()
        set_company(company_id, subscription_end_date=datetime.now(timezone.utc) - timedelta(seconds=2))
        assert api("POST", f"/api/admin/companies/{company_id}/suspend", admin_h).status_code == 200
        r = api("POST", f"/api/admin/companies/{company_id}/reactivate", admin_h)
        assert r.status_code == 400 and "renewed" in r.text                 # ended at its instant - renew, not reactivate
        # a calendar-day subscription ending today may still be reactivated today
        company_id, _ = self._setup(p, mgr, plan, "paid")
        set_company(company_id, subscription_end_date=datetime.combine(today_utc(), datetime.min.time(), tzinfo=timezone.utc))
        assert api("POST", f"/api/admin/companies/{company_id}/suspend", admin_h).status_code == 200
        assert api("POST", f"/api/admin/companies/{company_id}/reactivate", admin_h).status_code == 200


# =============================================================================
# the retired Onboarding Employee role: nothing in the active workflow, nothing in the onboarding API
# =============================================================================
class TestRetiredOnboardingRole:
    @pytest.fixture(scope="class")
    def record(self, p, mgr):
        """A (legacy) onboarding record ASSIGNED to the retired-role holder - as if it had been assigned before retirement."""
        made = convert(mgr, won_lead(mgr, p["employee"]["id"]))
        oid = api("GET", f"/api/sales/customers/{made['customer']['id']}", mgr).json()["onboarding"]["id"]

        async def _go(db):
            from sqlalchemy import update
            await db.execute(update(SalesOnboarding).where(SalesOnboarding.id == uuid.UUID(oid))
                             .values(assigned_to=uuid.UUID(p["onboarding"]["id"]), stage="assigned"))

        run_db(_go)
        return oid

    def test_their_session_has_no_role_and_no_permission(self, p):
        me = api("GET", "/api/sales/me", H(p, "onboarding"))
        assert me.status_code == 200
        assert me.json()["roles"] == [] and me.json()["permissions"] == [] and me.json()["modules"] == []

    def test_every_onboarding_endpoint_refuses_them_even_for_their_own_record(self, p, mgr, record):
        before = api("GET", f"/api/sales/onboarding/{record}", mgr).json()
        h = H(p, "onboarding")
        for method, path, body in (
            ("GET", "/api/sales/onboarding", None), ("GET", "/api/sales/onboarding?assigned_to=me", None),
            ("GET", "/api/sales/onboarding/counts", None), ("GET", "/api/sales/onboarding/assignees", None),
            ("GET", f"/api/sales/onboarding/{record}", None), ("GET", f"/api/sales/onboarding/{record}/timeline", None),
            ("PATCH", f"/api/sales/onboarding/{record}", {"notes": "still working it"}),
            ("POST", f"/api/sales/onboarding/{record}/stage", {"stage": "contacted"}),
            ("POST", f"/api/sales/onboarding/{record}/assign", {"assigned_to": p["onboarding"]["id"]}),
            ("GET", f"/api/sales/onboarding/{PHANTOM_ID}", None),
        ):
            r = api(method, path, h, body)
            assert r.status_code == 403 and r.json() == {"detail": "Access denied"}, (method, path, r.status_code)
        after = api("GET", f"/api/sales/onboarding/{record}", mgr).json()
        assert after == before                                             # nothing changed

    def test_the_active_workflow_refuses_them(self, p, mgr):
        lead = assigned_lead(mgr, p["employee"]["id"])
        h = H(p, "onboarding")
        for method, path in (
            ("GET", "/api/sales/dashboard"), ("GET", "/api/sales/reports/onboarding"), ("GET", "/api/sales/reports/leads"),
            ("GET", "/api/sales/leads"), ("GET", "/api/sales/queue"), ("GET", "/api/sales/customers"),
            ("GET", f"/api/sales/leads/{lead['id']}"), ("POST", f"/api/sales/leads/{lead['id']}/setup/preflight"),
            ("GET", "/api/sales/settings/distribution"), ("GET", "/api/sales/leads/export?format=xlsx"),
            ("GET", "/api/sales/team"), ("GET", "/api/sales/roles"),
        ):
            r = api(method, path, h, {} if method == "POST" else None)
            assert r.status_code == 403, (path, r.status_code)

    def test_a_manager_cannot_offer_or_give_them_an_onboarding(self, p, mgr, record):
        """Who may RECEIVE an onboarding is its own query (repositories/onboarding.py) - a retired role counts for nothing there
        either, although its grants are still in the database."""
        listed = {a["id"] for a in api("GET", "/api/sales/onboarding/assignees", mgr).json()}
        assert p["onboarding"]["id"] not in listed
        made = convert(mgr, won_lead(mgr, p["employee"]["id"]))
        oid = api("GET", f"/api/sales/customers/{made['customer']['id']}", mgr).json()["onboarding"]["id"]
        r = api("POST", f"/api/sales/onboarding/{oid}/assign", mgr, {"assigned_to": p["onboarding"]["id"]})
        assert r.status_code == 400 and errors_of(r)["code"] == "onboarding_assignee_not_eligible"

    def test_the_role_can_no_longer_be_granted_or_listed(self, admin_h, p):
        assert "onboarding_employee" not in {r["key"] for r in api("GET", "/api/sales/roles", admin_h).json()}
        body = {"name": "x", "email": f"ob-{uuid.uuid4().hex[:8]}@example.com", "phone": unique_lead_phone(), "password": "Staff#12345",
                "role_keys": ["onboarding_employee"]}
        r = api("POST", "/api/sales/team", admin_h, body)
        assert r.status_code == 400 and errors_of(r)["field"] == "role_keys" and count_users(body["email"]) == 0
        someone = create_staff(admin_h, [], "wf-no-role")
        r = api("POST", f"/api/sales/team/{someone['id']}/roles", admin_h, {"role_key": "onboarding_employee"})
        assert r.status_code == 400 and errors_of(r)["field"] == "role_key"
        assert api("GET", "/api/sales/me", someone["headers"]).json()["permissions"] == []

    def test_the_old_grant_can_still_be_revoked(self, admin_h):
        from sales_test_utils import create_retired_onboarding_employee
        holder = create_retired_onboarding_employee(admin_h, "wf-revoke")
        r = api("DELETE", f"/api/sales/team/{holder['id']}/roles/onboarding_employee", admin_h)
        assert r.status_code == 200 and r.json()["changed"] is True           # the history is kept; nothing is deleted

    def test_the_architecture_is_intact_for_a_later_re_enabling(self, admin_h, mgr, record):
        from server import app
        paths = {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/sales/onboarding")}
        assert {"/api/sales/onboarding", "/api/sales/onboarding/counts", "/api/sales/onboarding/assignees", "/api/sales/onboarding/{onboarding_id}",
                "/api/sales/onboarding/{onboarding_id}/assign", "/api/sales/onboarding/{onboarding_id}/stage",
                "/api/sales/onboarding/{onboarding_id}/timeline"} <= paths
        assert api("GET", f"/api/sales/onboarding/{record}", mgr).status_code == 200      # the manager's dormant API still works
        # the role itself: still there, still a system role, grants untouched - only is_active is off
        async def _role(db):
            from sqlalchemy import func, select
            role = (await db.execute(select(StaffRole).where(StaffRole.key == "onboarding_employee"))).scalar_one()
            grants = (await db.execute(select(func.count()).select_from(StaffRolePermission).where(StaffRolePermission.role_id == role.id))).scalar_one()
            return role.is_system, role.is_active, grants
        assert run_db(_role) == (True, False, 6)
        # and the same permission keys, held through a custom role, still work the onboarding records assigned to them
        worker = onboarding_worker(admin_h, "wf-dormant")

        async def _assign(db):
            from sqlalchemy import update
            await db.execute(update(SalesOnboarding).where(SalesOnboarding.id == uuid.UUID(record)).values(assigned_to=uuid.UUID(worker["id"])))

        run_db(_assign)
        r = api("GET", f"/api/sales/onboarding/{record}", worker["headers"])
        assert r.status_code == 200 and r.json()["assigned_to"]["id"] == worker["id"]

    def test_re_enabling_is_one_flag(self, p):
        """In a transaction that is never committed: set is_active back to true and the old holder has the old permissions."""
        import asyncio
        from sqlalchemy import update
        from database import SessionLocal, engine
        from sales.repositories import staff as staff_repo

        async def _go():
            try:
                async with SessionLocal() as db:
                    try:
                        _, before = await staff_repo.get_permission_profile(db, uuid.UUID(p["onboarding"]["id"]))
                        await db.execute(update(StaffRole).where(StaffRole.key == "onboarding_employee").values(is_active=True))
                        _, after = await staff_repo.get_permission_profile(db, uuid.UUID(p["onboarding"]["id"]))
                        return before, after
                    finally:
                        await db.rollback()
            finally:
                await engine.dispose()

        before, after = asyncio.run(_go())
        assert before == set()
        assert {"sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"} <= after
