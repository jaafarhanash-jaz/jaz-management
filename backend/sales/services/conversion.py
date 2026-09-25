"""Conversion (Phase 4): a WON lead becomes a customer, and the customer becomes a JAZ company.

The company is created by the platform's OWN company-creation service (services/admin.create_company) - the same
function the Super Admin's "add company" screen calls - so the company, its owner account, the attendance QR and the
plan configuration are produced by exactly one piece of code. Nothing of that is reimplemented here. Around it this
module adds only what Sales needs: who may convert, a preflight that shows what would happen (and any JAZ company the
new one could duplicate), idempotency, the customer + onboarding records, and the audit / timeline trail.

  * WHO. sales.customers.convert (Sales Manager, Super Admin) on a lead within the caller's lead scope. A Sales
    Employee, Lead Data Entry and Onboarding Employee cannot convert.
  * WHICH LEADS. Won, not archived, not converted yet. Converting never changes the lead: it stays exactly as it was and
    the customer points back at it.
  * NEVER A SILENT DUPLICATE. Before anything is written the owner's email and phone and the business name are compared
    with every live JAZ company (the same matching as lead duplicates, services/duplicates.py):
        - an email or phone that a live account already uses, or that equals an existing company owner's, BLOCKS the
          conversion (the platform cannot have two accounts on one email/phone, and the same person twice is a
          duplicate customer) - change the value to convert;
        - the same business name, or a phone that matches only after its country-code prefix, is only a WARNING: the
          caller reviews it and repeats the request with confirm_duplicates=true. The override is written into the
          lead's timeline with the ids of the companies it was made past.
  * IDEMPOTENT. The lead row is locked for the whole operation and a customer is unique per lead (and per company) in
    the database, so repeating the request - a double click, a retry after a lost response, two managers at once -
    finds the existing customer and returns it (already_converted=true) instead of creating a company. The first
    request decides; later ones create nothing and write nothing.
  * ALL OR NOTHING. Company, owner account, customer, onboarding, timeline event and audit event are written in the
    request's single transaction: if any step fails - including the timeline write - none of them exists afterwards.
  * NO SECRETS. The owner's initial password is a SecretStr in the request, is handed to the core service and to nothing
    else: it is never returned, logged, recorded on the timeline or audited.
"""
import logging
import uuid
from types import SimpleNamespace
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.subscription_plans as plans_repo
import services.admin as admin_service
from sales import permissions as perms
from sales.constants import ONBOARDING_START, STAGE_WON, customer_status_for_stage
from sales.models import SalesCustomer, SalesLead, SalesOnboarding
from sales.repositories import customers as customers_repo
from sales.repositories import leads as leads_repo
from sales.services import activity as activity_service
from sales.services import audit as audit_service
from sales.services import customers as customers_service
from sales.services import duplicates as duplicates_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from services.admin import parse_uuid
from services.auth import validate_password_strength
from services.identifiers import phone_error

logger = logging.getLogger("sales.conversion")

# Why a lead cannot be converted (code -> message). Codes are stable identifiers for clients and tests.
_LEAD_BLOCKERS = {
    "lead_not_won": "Only a won lead can be converted into a customer",
    "lead_archived": "This lead is archived. Restore it to convert it.",
}
_MESSAGES = {
    "owner_name_required": "The owner's name is required",
    "owner_email_required": "The owner's email is required",
    "owner_phone_required": "The owner's phone number is required",
    "owner_email_registered": "This email is already registered to a JAZ account",
    "owner_phone_registered": "This phone number is already registered to a JAZ account",
}


def _blocker(code: str, field: Optional[str], message: str) -> dict:
    return {"code": code, "field": field, "message": message}


def lead_blocker(lead: SalesLead) -> Optional[str]:
    """Why this lead cannot be converted right now, or None."""
    if lead.archived_at is not None:
        return "lead_archived"
    if lead.pipeline_stage != STAGE_WON:
        return "lead_not_won"
    return None


async def _load_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, *, for_update: bool) -> SalesLead:
    """The lead: 404 when it does not exist (or the id is malformed), 403 when it is outside the caller's scope."""
    parsed = parse_uuid(lead_id)
    lead = await leads_repo.get_lead(db, parsed, for_update=for_update) if parsed else None
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    work.ensure_visible(ctx, lead.created_by, lead.assigned_to)
    return lead


# ---- the values the company would be created from -----------------------------------------------------------------

def _effective_values(lead: SalesLead, body) -> dict:
    """The preflight's values: what the caller gave, else what the lead holds."""
    return {
        "business_name": body.business_name or lead.business_name,
        "owner_name": body.owner_name or lead.contact_name,
        "owner_email": str(body.owner_email) if body.owner_email else lead.email,
        "owner_phone": body.owner_phone or lead.phone,
        "address": body.address or lead.address,
    }


async def _account_blockers(db: AsyncSession, values: dict) -> List[dict]:
    """Everything about the owner account that would make company creation fail, found BEFORE anything is written, each in
    the {code, field, message} shape the UI attaches to an input."""
    out: List[dict] = []
    if not values["owner_name"]:
        out.append(_blocker("owner_name_required", "owner_name", _MESSAGES["owner_name_required"]))
    if not values["owner_email"]:
        out.append(_blocker("owner_email_required", "owner_email", _MESSAGES["owner_email_required"]))
    elif await customers_repo.email_registered(db, values["owner_email"]):
        out.append(_blocker("owner_email_registered", "owner_email", _MESSAGES["owner_email_registered"]))
    if not values["owner_phone"]:
        out.append(_blocker("owner_phone_required", "owner_phone", _MESSAGES["owner_phone_required"]))
    else:
        problem = phone_error(values["owner_phone"])           # the shared Phase-1 phone rule
        if problem is not None:
            out.append(_blocker("owner_phone_invalid", "owner_phone", problem))
        elif await customers_repo.phone_registered(db, values["owner_phone"]):
            out.append(_blocker("owner_phone_registered", "owner_phone", _MESSAGES["owner_phone_registered"]))
    return out


async def _company_duplicates(db: AsyncSession, values: dict) -> List[duplicates_service.CompanyMatch]:
    keys = duplicates_service.Keys.of(
        business_name=values["business_name"], phone=values["owner_phone"], email=values["owner_email"],
    )
    return await duplicates_service.check_companies(db, keys)


def _duplicates_out(matches: List[duplicates_service.CompanyMatch]) -> dict:
    return {
        "has_exact": any(m.severity == duplicates_service.EXACT for m in matches),
        "has_possible": any(m.severity == duplicates_service.POSSIBLE for m in matches),
        "companies": [
            {
                "id": str(m.company.company_id), "name": m.company.company_name, "severity": m.severity,
                "matches": [{"field": x.field, "kind": x.kind, "matched_on": x.matched_on} for x in m.matches],
            }
            for m in matches
        ],
    }


def _exact_blocker(matches) -> Optional[dict]:
    if any(m.severity == duplicates_service.EXACT for m in matches):
        return _blocker(
            "company_exists", "duplicates",
            "A JAZ company already exists for this owner's email or phone. Review it - a second company would be a duplicate.",
        )
    return None


# ---- reads -------------------------------------------------------------------------------------------------------

async def conversion_status(db: AsyncSession, ctx: StaffContext, lead_id: str) -> dict:
    """Where this lead stands: converted (and to which customer), convertible by this caller, or why not."""
    lead = await _load_lead(db, ctx, lead_id, for_update=False)
    row = await customers_repo.get_row_by_lead(db, lead.id)
    blocker = None if row is not None else lead_blocker(lead)
    return {
        "lead_stage": lead.pipeline_stage,
        "archived": lead.archived_at is not None,
        "converted": row is not None,
        "customer": customers_service.customer_out(row, ctx) if row is not None else None,
        "can_convert": row is None and blocker is None and ctx.has(perms.PERM_CUSTOMERS_CONVERT),
        "blocker": blocker,
    }


async def preflight(db: AsyncSession, ctx: StaffContext, lead_id: str, body) -> dict:
    """What converting this lead with these values would do - without doing any of it. Read-only."""
    lead = await _load_lead(db, ctx, lead_id, for_update=False)
    lead_out = {
        "id": str(lead.id), "business_name": lead.business_name, "pipeline_stage": lead.pipeline_stage,
        "archived": lead.archived_at is not None,
    }
    values = _effective_values(lead, body)

    existing = await customers_repo.get_row_by_lead(db, lead.id)
    if existing is not None:
        return {
            "lead": lead_out, "already_converted": True, "customer": customers_service.customer_out(existing, ctx),
            "values": values, "blockers": [_blocker("already_converted", "lead", "This lead has already been converted")],
            "duplicates": _duplicates_out([]), "requires_confirmation": False, "can_convert": False, "plans": [],
        }

    blockers: List[dict] = []
    lead_problem = lead_blocker(lead)
    if lead_problem:
        blockers.append(_blocker(lead_problem, "lead", _LEAD_BLOCKERS[lead_problem]))
    blockers += await _account_blockers(db, values)
    matches = await _company_duplicates(db, values)
    exact = _exact_blocker(matches)
    if exact:
        blockers.append(exact)

    plans = sorted(await plans_repo.list_active(db), key=lambda p: (float(p.price or 0), p.name))
    return {
        "lead": lead_out, "already_converted": False, "customer": None, "values": values, "blockers": blockers,
        "duplicates": _duplicates_out(matches),
        "requires_confirmation": not blockers and any(m.severity == duplicates_service.POSSIBLE for m in matches),
        "can_convert": not blockers,
        "plans": [
            {"id": str(p.id), "name": p.name, "price": float(p.price or 0), "duration_months": p.duration_months, "max_employees": p.max_employees}
            for p in plans
        ],
    }


# ---- the conversion ----------------------------------------------------------------------------------------------

_CORE_ERRORS = {   # the plain-string details the core company service raises -> the Sales field error the UI understands
    "Email already registered": ("owner_email", "owner_email_registered"),
    "Phone number already registered": ("owner_phone", "owner_phone_registered"),
}


def _from_core_error(exc: HTTPException) -> HTTPException:
    detail = exc.detail
    if isinstance(detail, str) and detail in _CORE_ERRORS:
        field, code = _CORE_ERRORS[detail]
        return field_error(field, _MESSAGES[code], exc.status_code, code=code)
    if isinstance(detail, dict) and detail.get("message") == "Phone number already registered":
        return field_error("owner_phone", _MESSAGES["owner_phone_registered"], exc.status_code, code="owner_phone_registered")
    return exc


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """A concurrent request can win a race the pre-checks lose; the database's own constraints are the real guarantee.
    Turn that into the clean answer the pre-check would have produced, never a 500 - and because the exception ends the
    request, nothing of it (not even the company) is committed."""
    text = str(getattr(exc, "orig", exc))
    if "uq_sales_customers_lead" in text:
        return field_error("lead", "This lead has just been converted", 409, code="already_converted")
    if "uq_users_email" in text or "users_email" in text:
        return field_error("owner_email", _MESSAGES["owner_email_registered"], 400, code="owner_email_registered")
    if "uq_users_phone" in text or "ck_users_phone_no_at" in text:
        return field_error("owner_phone", _MESSAGES["owner_phone_registered"], 400, code="owner_phone_registered")
    raise exc


async def convert_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, body, audit: AuditContext) -> dict:
    lead = await _load_lead(db, ctx, lead_id, for_update=True)      # serialises every conversion of this lead

    existing = await customers_repo.get_row_by_lead(db, lead.id)
    if existing is not None:                                        # idempotent: the first conversion stands
        return {"customer": customers_service.customer_out(existing, ctx), "already_converted": True}

    problem = lead_blocker(lead)
    if problem:
        raise field_error("lead", _LEAD_BLOCKERS[problem], 409, code=problem)

    values = {
        "business_name": body.business_name, "owner_name": body.owner_name, "owner_email": str(body.owner_email),
        "owner_phone": body.owner_phone, "address": body.address,
    }
    blockers = await _account_blockers(db, values)
    if blockers:
        first = blockers[0]
        raise field_error(first["field"], first["message"], 400, code=first["code"], blockers=blockers)

    password = body.owner_password.get_secret_value()
    try:
        validate_password_strength(password)                        # the core rule; checked here for a field-shaped error
    except HTTPException as exc:
        raise field_error("owner_password", str(exc.detail), 400, code="owner_password_weak")

    plan = await plans_repo.get_by_id(db, body.subscription_plan_id)
    if plan is None or not plan.is_active:
        raise field_error("subscription_plan_id", "Selected subscription plan is not available", 400, code="plan_not_available")

    matches = await _company_duplicates(db, values)
    exact = _exact_blocker(matches)
    if exact:
        raise field_error(exact["field"], exact["message"], 409, code=exact["code"], duplicates=_duplicates_out(matches))
    override = None
    if matches:
        if not body.confirm_duplicates:
            raise field_error(
                "duplicates", "Possible duplicates found. Review them, then confirm to continue.", 409,
                code="duplicates_found", duplicates=_duplicates_out(matches),
            )
        override = {"possible": len(matches), "company_ids": [str(m.company.company_id) for m in matches]}

    # -- the company: created by the platform's own service, in a savepoint so a lost race is a clean error --
    company_input = SimpleNamespace(
        name=values["business_name"], owner_email=values["owner_email"], owner_name=values["owner_name"],
        owner_password=password, owner_phone=values["owner_phone"], address=values["address"],
        subscription_plan_id=str(plan.id),
    )
    try:
        async with db.begin_nested():
            company = await admin_service.create_company(db, company_input)
    except HTTPException as exc:
        raise _from_core_error(exc)
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    # -- the customer and its onboarding (unowned, at its starting stage) --
    customer = SalesCustomer(
        id=uuid.uuid4(), lead_id=lead.id, company_id=uuid.UUID(company["id"]), converted_by=ctx.user_id,
        status=customer_status_for_stage(ONBOARDING_START),
    )
    onboarding = SalesOnboarding(id=uuid.uuid4(), customer_id=customer.id, stage=ONBOARDING_START)
    try:
        async with db.begin_nested():
            db.add(customer)
            await db.flush()
            db.add(onboarding)
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_CONVERTED,
        after={
            "customer_status": customer.status, "company_name": company["name"], "plan": plan.name,
            "onboarding_stage": onboarding.stage,
        },
        metadata={
            "customer_id": str(customer.id), "company_id": company["id"], "onboarding_id": str(onboarding.id),
            **({"duplicate_override": override} if override else {}),
        },
    )
    await audit_service.record(
        db, ctx, audit,
        action=audit_service.ACTION_CUSTOMER_CONVERTED, target_type=audit_service.TARGET_SALES_CUSTOMER, target_id=customer.id,
        target_user_id=uuid.UUID(company["owner_id"]),
        after={"lead_id": str(lead.id), "company_id": company["id"], "subscription_plan_id": str(plan.id)},
        metadata={"onboarding_id": str(onboarding.id), **({"duplicate_override": override} if override else {})},
    )
    logger.info(
        "customer_converted actor=%s lead=%s customer=%s company=%s override=%s",
        ctx.user_id, lead.id, customer.id, company["id"], override is not None,
    )
    return {"customer": await customers_service.one(db, ctx, customer.id), "already_converted": False}
