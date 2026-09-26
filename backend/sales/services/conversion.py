"""The lead-to-customer helpers the Customer Setup shares - and the RETIRED Phase-4 conversion.

Phase 4 turned a WON lead into a customer and a JAZ company (POST /leads/{id}/convert, with a preflight). That flow is RETIRED:
both endpoints answer 410 `conversion_retired` to everybody (conversion_retired() below), because the Customer Setup
(services/customer_setup.py - the salesperson's "Agreed") replaces it and is the one place the Trial / Paid rules are applied.
The retired flow created the company with an active subscription and NO start or end date - a subscription that never ends - so
nothing in Sales may create a company any other way: the Customer Setup is the ONLY caller of the platform's company service
(services/admin.create_company) and it activates a dated period in the same transaction. The historical architecture stays: the
customers and onboarding tables and models, the customer / onboarding endpoints, the conversion-status read and the shared
helpers below.

What the Customer Setup and the customer views still use from here:
  * the lead loader with the lead-scope check (404 unknown / malformed id, 403 outside the caller's scope);
  * the owner-account blockers (an email or phone a JAZ account already uses, the shared phone rule) and the company-duplicate
    checks (services/duplicates.py): an exact match BLOCKS, a possible one needs confirm_duplicates;
  * the translation of the platform's own company-service errors and of a lost race on the database's unique constraints into
    the clean field errors the UI understands;
  * conversion_status(): where a lead stands (converted - and to which customer - or whether the Customer Setup may start).
"""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import STAGE_LOST
from sales.models import SalesLead
from sales.repositories import customers as customers_repo
from sales.repositories import leads as leads_repo
from sales.services import customers as customers_service
from sales.services import duplicates as duplicates_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.common import field_error
from services.admin import parse_uuid
from services.identifiers import phone_error

# The Customer Setup - POST /leads/{id}/setup - is what replaced the retired conversion.
CONVERSION_REPLACEMENT = "POST /api/sales/leads/{lead_id}/setup"


def conversion_retired() -> HTTPException:
    """The one controlled answer to any attempt to use the retired Phase-4 conversion: 410 Gone with a stable code and where to go."""
    return field_error(
        "lead",
        "The old lead conversion has been retired. Complete the Customer Setup (the salesperson's \"Agreed\") instead: it creates "
        "the customer, the company and its Trial or Paid subscription.",
        410, code="conversion_retired", replacement=CONVERSION_REPLACEMENT,
    )


_MESSAGES = {
    "owner_name_required": "The owner's name is required",
    "owner_email_required": "The owner's email is required",
    "owner_phone_required": "The owner's phone number is required",
    "owner_email_registered": "This email is already registered to a JAZ account",
    "owner_phone_registered": "This phone number is already registered to a JAZ account",
}


def _blocker(code: str, field: Optional[str], message: str) -> dict:
    return {"code": code, "field": field, "message": message}


# The simplified workflow's Customer Setup (services/customer_setup.py) starts from an OPEN lead too: the salesperson's
# "Agreed" is what wins it. It needs the lead to be live, not lost, and owned (the owner is who won it).
SETUP_LEAD_BLOCKERS = {
    "lead_archived": "This lead is archived. Restore it to set up the customer.",
    "lead_lost": "This lead is marked as lost. Reopen it to set up the customer.",
    "lead_unassigned": "Assign this lead to a Sales Employee before setting up the customer.",
}


def setup_lead_blocker(lead: SalesLead) -> Optional[str]:
    """Why the Customer Setup cannot start from this lead right now, or None (won, or open and assigned)."""
    if lead.archived_at is not None:
        return "lead_archived"
    if lead.pipeline_stage == STAGE_LOST:
        return "lead_lost"
    if lead.assigned_to is None:
        return "lead_unassigned"
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
    """Where this lead stands: converted (and to which customer), or whether the Customer Setup may start. `can_convert` is
    always false - the Phase-4 conversion is retired (`blocker` says so until the lead has a customer); `can_setup` is the answer."""
    lead = await _load_lead(db, ctx, lead_id, for_update=False)
    row = await customers_repo.get_row_by_lead(db, lead.id)
    setup_blocker = None if row is not None else setup_lead_blocker(lead)
    return {
        "lead_stage": lead.pipeline_stage,
        "archived": lead.archived_at is not None,
        "converted": row is not None,
        "customer": customers_service.customer_out(row, ctx) if row is not None else None,
        "can_convert": False,
        "blocker": None if row is not None else "conversion_retired",
        # the Customer Setup (simplified workflow): its own permission plus moving the lead to won
        "can_setup": row is None and setup_blocker is None
        and ctx.has(perms.PERM_CUSTOMERS_SETUP, perms.PERM_LEADS_CHANGE_STAGE),
        "setup_blocker": setup_blocker,
    }


# ---- translating failures ---------------------------------------------------------------------------------------

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
