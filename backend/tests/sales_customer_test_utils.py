"""Shared helpers for the JAZ Sales Phase-4 (customers, onboarding) test modules.

Like the Phase-1/2/3 helpers they only ever run against the disposable scratch database
(sales_test_utils.assert_scratch_target). Setting a customer up creates a REAL JAZ company and its owner account, and none of
that is ever deleted (companies are soft-deleted at most), so the scratch DB accumulates them across runs: every test
uses its OWN leads, whose business names, owner emails and owner phones are generated unique per call, and asserts on
its own customers - never on "the whole table".

The Phase-4 conversion endpoint (POST /leads/{id}/convert) is RETIRED and answers 410. The customers and onboarding records it
used to create are still HISTORICAL DATA that the dormant onboarding architecture, the customer views and the reports handle, so
`convert()` below is a LEGACY DATA FIXTURE: it seeds that data straight into the scratch database, never through the API.
"""
import asyncio
import random
import uuid
from typing import List, Optional

from sales_activity_test_utils import (  # noqa: F401  (re-exported for the Phase-4 test modules)
    PHANTOM_ID,
    act,
    activities,
    assign,
    create_lead,
    create_staff,
    errors_of,
    get_lead,
    ids_of,
    listing,
    make_custom_role,
    make_personas,
    retire_roles,
    run_db,
    set_stage,
    uniq,
    worked_lead,
)
from sales_lead_test_utils import legacy_win, unique_lead_email, unique_lead_phone  # noqa: F401
from sales_test_utils import ONBOARDING_WORKER_KEYS, api  # noqa: F401

OWNER_PASSWORD = "Owner#Pass-2026"
ONBOARDING_EVENTS = ("onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated")
PHASE4_EVENTS = ("lead_converted",) + ONBOARDING_EVENTS


# ---- leads ----------------------------------------------------------------------------------------------------------
def won_lead(manager_headers: dict, employee_id: str, **overrides) -> dict:
    """A fresh lead assigned to `employee_id` that was WON BY HAND before the rule that ended it - a won lead with no customer, the
    historical state the customer / onboarding / report code still handles and the Customer Setup can still complete (see
    sales_lead_test_utils.legacy_win: nobody can move a lead to `won` any more, so this is seeded, not done through the API)."""
    lead = worked_lead(manager_headers, employee_id, **overrides)
    set_stage(manager_headers, lead["id"], "contacted")
    legacy_win(manager_headers, lead["id"])
    return get_lead(manager_headers, lead["id"])


# ---- plans ---------------------------------------------------------------------------------------------------------
_PLAN_ID: Optional[str] = None


def active_plan_id() -> str:
    """An active subscription plan of the scratch DB (any; conversion copies its configuration)."""
    global _PLAN_ID
    if _PLAN_ID is None:
        from sqlalchemy import select
        from models import SubscriptionPlan

        async def _go(db):
            found = (await db.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.name).limit(1))).scalar_one_or_none()
            return str(found) if found is not None else None

        _PLAN_ID = run_db(_go) or make_plan()      # a database with no plan at all (a schema-only restore) gets one
    return _PLAN_ID


def make_plan(*, active: bool = True) -> str:
    """A throwaway plan (direct DB write; scratch only). Returns its id."""
    from models import SubscriptionPlan

    plan_id = uuid.uuid4()

    async def _go(db):
        db.add(SubscriptionPlan(id=plan_id, name=f"P4 plan {uniq()}", max_employees=7, price=12.5, duration_months=3, features=["a", "b"], is_active=active))

    run_db(_go)
    return str(plan_id)


# ---- conversion ----------------------------------------------------------------------------------------------------
def convert_body(lead: dict, **overrides) -> dict:
    """A valid conversion request whose owner email / phone are unique. Override anything; None removes a key."""
    body = {
        "business_name": lead["business_name"],
        "owner_name": lead.get("contact_name") or "Owner Person",
        "owner_email": unique_lead_email("owner"),
        "owner_phone": unique_lead_phone(),
        "owner_password": OWNER_PASSWORD,
        "subscription_plan_id": active_plan_id(),
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def convert(headers: dict, lead: dict, **overrides) -> dict:
    """LEGACY DATA FIXTURE - seeds what the RETIRED conversion endpoint used to write (the endpoint now answers 410).

    Straight into the scratch database, through the platform's own services and never through the API: the company and its owner
    account (core create_company - an ACTIVE subscription with NO start or end date, the very shape the retirement exists to stop,
    so it is confined to this fixture), the customer, its unowned onboarding at the starting stage, the `lead_converted` timeline
    event and the `customer_converted` audit event. `headers` names the manager who "converted". Returns what the endpoint returned:
    {"customer": <GET /customers/{id}>, "already_converted": False}, plus what was "sent" under "_body"."""
    body = convert_body(lead, **overrides)
    me = api("GET", "/api/sales/me", headers).json()["user"]

    async def _seed(db):
        from types import SimpleNamespace

        import repositories.subscription_plans as plans_repo
        import services.admin as admin_service
        from models import User
        from sales.constants import ONBOARDING_START, STAGE_WON, customer_status_for_stage
        from sales.models import SalesCustomer, SalesLead, SalesOnboarding
        from sales.services import activity as activity_service
        from sales.services import audit as audit_service
        from sales.services import customers as customers_service
        from sales.services.access import resolve_staff_context

        user = await db.get(User, uuid.UUID(me["id"]))
        ctx = await resolve_staff_context(
            db, {"id": str(user.id), "name": user.name, "email": user.email, "phone": user.phone, "role": user.role, "status": user.status},
        )
        audit = audit_service.new_audit_context("pytest-legacy-fixture")
        lead_row = await db.get(SalesLead, uuid.UUID(lead["id"]))
        assert lead_row.pipeline_stage == STAGE_WON and lead_row.archived_at is None, "the legacy fixture converts only a live, won lead"
        plan = await plans_repo.get_by_id(db, uuid.UUID(body["subscription_plan_id"]))
        company = await admin_service.create_company(db, SimpleNamespace(
            name=body["business_name"], owner_email=body["owner_email"], owner_name=body["owner_name"], owner_password=body["owner_password"],
            owner_phone=body["owner_phone"], address=body.get("address"), subscription_plan_id=str(plan.id),
        ))
        customer_row = SalesCustomer(
            id=uuid.uuid4(), lead_id=lead_row.id, company_id=uuid.UUID(company["id"]), converted_by=ctx.user_id,
            status=customer_status_for_stage(ONBOARDING_START),
        )
        onboarding_row = SalesOnboarding(id=uuid.uuid4(), customer_id=customer_row.id, stage=ONBOARDING_START)
        db.add(customer_row)
        await db.flush()
        db.add(onboarding_row)
        await db.flush()
        await activity_service.record(
            db, ctx, audit, lead_row.id, activity_service.EVENT_LEAD_CONVERTED,
            after={"customer_status": customer_row.status, "company_name": company["name"], "plan": plan.name, "onboarding_stage": onboarding_row.stage},
            metadata={"customer_id": str(customer_row.id), "company_id": company["id"], "onboarding_id": str(onboarding_row.id)},
        )
        await audit_service.record(
            db, ctx, audit,
            action=audit_service.ACTION_CUSTOMER_CONVERTED, target_type=audit_service.TARGET_SALES_CUSTOMER, target_id=customer_row.id,
            target_user_id=uuid.UUID(company["owner_id"]),
            after={"lead_id": str(lead_row.id), "company_id": company["id"], "subscription_plan_id": str(plan.id)},
            metadata={"onboarding_id": str(onboarding_row.id)},
        )
        return await customers_service.one(db, ctx, customer_row.id)        # what the endpoint answered with (the list shape)

    from sales.customer_schemas import CustomerOut

    return {"customer": CustomerOut.model_validate(run_db(_seed)).model_dump(mode="json"), "already_converted": False, "_body": body}


def agree(headers: dict, lead: dict, *, plan_id: Optional[str] = None, expect: int = 201, **overrides) -> dict:
    """The salesperson's "Agreed": the REAL Customer Setup (POST /leads/{id}/setup) - the only way a Sales Employee wins a lead
    (moving a lead to `won` by hand is the team level's tool). Creates the customer, the JAZ company, its owner and a dated Trial
    unless overridden (None removes a key). Returns the response body."""
    body = {
        "business_name": lead["business_name"], "owner_name": "Owner Person", "owner_email": unique_lead_email("owner"),
        "owner_phone": unique_lead_phone(), "owner_password": OWNER_PASSWORD, "subscription_plan_id": plan_id or active_plan_id(),
        "subscription_type": "trial",
    }
    body.update(overrides)
    r = api("POST", f"/api/sales/leads/{lead['id']}/setup", headers, {k: v for k, v in body.items() if v is not None})
    assert r.status_code == expect, f"agree: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def retired_conversion(headers: dict, lead_id: str, path: str = "convert", body: Optional[dict] = None):
    """POST to one of the RETIRED conversion endpoints ("convert" or "convert/preflight"); returns the raw response."""
    return api("POST", f"/api/sales/leads/{lead_id}/{path}", headers, body)


def converted(manager_headers: dict, employee_id: str, **overrides) -> dict:
    """A won lead converted by the manager: {"lead": ..., "customer": ..., "body": ...}."""
    lead = won_lead(manager_headers, employee_id)
    out = convert(manager_headers, lead, **overrides)
    return {"lead": lead, "customer": out["customer"], "body": out["_body"]}


def customer(headers: dict, customer_id: str, expect: int = 200) -> dict:
    r = api("GET", f"/api/sales/customers/{customer_id}", headers)
    assert r.status_code == expect, f"customer: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


# ---- onboarding ----------------------------------------------------------------------------------------------------
_WORKER_ROLE: Optional[str] = None


def onboarding_worker(admin_headers: dict, label: str = "worker") -> dict:
    """Somebody who works onboarding records. The Onboarding Employee SYSTEM role is retired (migration c5e1b9a4d2f7), so the
    dormant onboarding architecture is exercised through a custom role holding exactly its permission keys - what re-enabling
    the role would give back (one role per test process)."""
    return create_staff(admin_headers, [onboarding_worker_role()], f"p4-{label}")


def onboarding_worker_role() -> str:
    """The key of this test process's custom onboarding-worker role (created on first use)."""
    global _WORKER_ROLE
    if _WORKER_ROLE is None:
        _WORKER_ROLE = make_custom_role(sorted(ONBOARDING_WORKER_KEYS))
    return _WORKER_ROLE


def onboarding_id(manager_headers: dict, customer_id: str) -> str:
    """The onboarding record of a customer (read through the customer as a caller who may view both)."""
    return customer(manager_headers, customer_id)["onboarding"]["id"]


def onboarding(headers: dict, onboarding_id_: str, expect: int = 200) -> dict:
    r = api("GET", f"/api/sales/onboarding/{onboarding_id_}", headers)
    assert r.status_code == expect, f"onboarding: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def assign_onboarding(headers: dict, onboarding_id_: str, user_id: str, expect: int = 200, **extra) -> dict:
    r = api("POST", f"/api/sales/onboarding/{onboarding_id_}/assign", headers, {"assigned_to": user_id, **extra})
    assert r.status_code == expect, f"assign onboarding: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def set_onboarding_stage(headers: dict, onboarding_id_: str, stage: str, expect: int = 200, **extra) -> dict:
    r = api("POST", f"/api/sales/onboarding/{onboarding_id_}/stage", headers, {"stage": stage, **extra})
    assert r.status_code == expect, f"onboarding stage {stage}: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def onboarding_timeline(headers: dict, onboarding_id_: str, expect: int = 200) -> List[dict]:
    """The record's own timeline, OLDEST first (the API returns newest first)."""
    r = api("GET", f"/api/sales/onboarding/{onboarding_id_}/timeline?limit=200", headers)
    assert r.status_code == expect, f"onboarding timeline: expected {expect}, got {r.status_code} {r.text[:300]}"
    return list(reversed(r.json()["items"])) if expect == 200 else []


def phase4_events(headers: dict, lead_id: str) -> List[str]:
    """The Phase-4 events on a lead's timeline, oldest first."""
    return [e["event_type"] for e in activities(headers, lead_id) if e["event_type"] in PHASE4_EVENTS]


def started(manager_headers: dict, admin_headers: dict, employee_id: str, label: str = "w") -> dict:
    """A converted customer whose onboarding is assigned to a fresh onboarding worker:
    {"lead", "customer", "onboarding_id", "worker"}."""
    made = converted(manager_headers, employee_id)
    oid = onboarding_id(manager_headers, made["customer"]["id"])
    worker = onboarding_worker(admin_headers, label)
    assign_onboarding(manager_headers, oid, worker["id"])
    return {**made, "onboarding_id": oid, "worker": worker}


# ---- direct-DB facts (scratch only) --------------------------------------------------------------------------------
def count_companies(name: str) -> int:
    from sqlalchemy import func, select
    from models import Company

    async def _go(db):
        return (await db.execute(select(func.count()).select_from(Company).where(Company.name == name))).scalar_one()

    return run_db(_go)


def count_users(email: str) -> int:
    from sqlalchemy import func, select
    from models import User

    async def _go(db):
        return (await db.execute(select(func.count()).select_from(User).where(func.lower(User.email) == email.lower()))).scalar_one()

    return run_db(_go)


def count_customers(lead_id: str) -> int:
    from sqlalchemy import func, select
    from sales.models import SalesCustomer

    async def _go(db):
        return (await db.execute(select(func.count()).select_from(SalesCustomer).where(SalesCustomer.lead_id == uuid.UUID(lead_id)))).scalar_one()

    return run_db(_go)


def db_row(model, **where):
    """One row of `model` (a detached snapshot of its columns as a dict), or None."""
    from sqlalchemy import select

    async def _go(db):
        row = (await db.execute(select(model).filter_by(**where))).scalar_one_or_none()
        return None if row is None else {c.name: getattr(row, c.name) for c in row.__table__.columns}

    return run_db(_go)


def audit_events(target_id: str) -> List[dict]:
    """The staff audit events whose target is `target_id`, oldest first."""
    from sqlalchemy import select
    from sales.models import StaffAuditEvent

    async def _go(db):
        rows = (await db.execute(select(StaffAuditEvent).where(StaffAuditEvent.target_id == uuid.UUID(target_id)).order_by(StaffAuditEvent.seq))).scalars().all()
        return [{"action": r.action, "actor_user_id": str(r.actor_user_id), "target_user_id": str(r.target_user_id) if r.target_user_id else None,
                 "before": r.before_data, "after": r.after_data, "metadata": r.event_metadata} for r in rows]

    return run_db(_go)


def deactivate(admin_headers: dict, staff_id: str) -> None:
    assert api("PATCH", f"/api/sales/team/{staff_id}", admin_headers, {"status": "inactive"}).status_code == 200


def reactivate(admin_headers: dict, staff_id: str) -> None:
    assert api("PATCH", f"/api/sales/team/{staff_id}", admin_headers, {"status": "active"}).status_code == 200


def phone_variant(phone: str) -> str:
    """The same number written differently (spaces, dashes, parentheses) - equal after normalization, never equal as text."""
    digits = phone.removeprefix("+")
    return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
