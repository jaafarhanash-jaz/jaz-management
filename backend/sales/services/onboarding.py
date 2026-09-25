"""Onboarding (Phase 4): the workflow that takes a new customer from `won` to `activated`.

    won -> assigned -> contacted -> setup_started -> company_configured -> employees_added -> training -> activated

Authorization is layered like the rest of JAZ Sales, with its OWN row scope (services/onboarding_access.py), because
the people who run onboarding are not salespeople:
  1. the router's require_permission(...) decides whether the caller may perform the ACTION at all
     (sales.onboarding.view / .manage / .assign);
  2. the onboarding scope decides on WHICH records: every record (scope_all - Sales Manager, Super Admin) or only the ones
     assigned to the caller (scope_assigned - Onboarding Employee). A record outside the scope answers 403 "Access denied",
     an unknown or malformed id answers 404. The lead scope plays no part: an Onboarding Employee reaches their records
     without reaching the lead - and the customer's JAZ company is shown only to a caller who may view the customer;
  3. this module enforces the business rules (the stage machine, who may be assigned).

Rules
  * Assigning: needs sales.onboarding.assign. The person must be an ACTIVE staff account holding onboarding.manage and the
    assigned scope through an active role - a deactivated or revoked employee cannot be given a NEW onboarding. Assigning
    the first time moves the record from `won` to `assigned`; reassigning changes only the owner. (An onboarding whose
    owner is deactivated later stays where it is: nothing is lost or silently reassigned - a manager hands it over.)
  * Stage changes are validated by constants.onboarding_transition_error: nothing moves until somebody owns the record,
    `won` can never be chosen, everything else can follow everything else, and a finished record can be reopened.
  * The customer's status follows the stage (constants.customer_status_for_stage) and is written here, under the
    onboarding's row lock, in the same transaction as the stage change.
  * Every change is recorded on the customer's LEAD timeline in the same transaction (onboarding_assigned /
    onboarding_stage_changed / onboarding_notes_updated). Nothing here changes the lead or the JAZ company.
"""
import logging
import uuid
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales import permissions as perms
from sales.constants import (
    ONBOARDING_ASSIGNED,
    ONBOARDING_DONE,
    ONBOARDING_START,
    customer_status_for_stage,
    onboarding_allowed_stages,
    onboarding_transition_error,
)
from sales.models import SalesCustomer, SalesOnboarding
from sales.repositories import customers as customers_repo
from sales.repositories import onboarding as onboarding_repo
from sales.repositories import work as work_repo
from sales.repositories.onboarding import OnboardingFilters, OnboardingRow
from sales.services import activity as activity_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import access_denied, field_error
from sales.services.lead_access import can_see
from sales.services.onboarding_access import can_see_onboarding, onboarding_visibility
from services.admin import parse_uuid

logger = logging.getLogger("sales.onboarding")

# stage-change failures that are about the REQUEST (400); everything else is a conflict with the record's state (409)
_STAGE_BAD_REQUEST = {"onboarding_invalid_stage"}


def onboarding_out(row: OnboardingRow, ctx: StaffContext) -> dict:
    """The API shape of an onboarding record for THIS caller. `allowed_stages` and `can` are what this caller may do to
    this record right now (the UI mirrors them; the endpoints enforce the same rules regardless). The company and the
    lead link are shown only to a caller who may view the customer - i.e. holds sales.customers.view AND can see the
    lead: an Onboarding Employee gets the contact details they need to do the work, and nothing behind them."""
    o = row.onboarding
    manageable = ctx.has(perms.PERM_ONBOARDING_MANAGE) and can_see_onboarding(ctx, assigned_to=o.assigned_to)
    sees_customer = ctx.has(perms.PERM_CUSTOMERS_VIEW) and can_see(ctx, created_by=row.lead_created_by, assigned_to=row.lead_assigned_to)
    return {
        "id": str(o.id),
        "stage": o.stage,
        "assigned_to": work.user_ref(o.assigned_to, row.assignee_name, row.assignee_status),
        "notes": o.notes,
        "started_at": o.started_at,
        "completed_at": o.completed_at,
        "created_at": o.created_at,
        "updated_at": o.updated_at,
        "customer": {
            "id": str(o.customer_id), "status": row.customer_status, "business_name": row.business_name,
            "contact_name": row.contact_name, "contact_position": row.contact_position, "phone": row.phone,
            "whatsapp": row.whatsapp, "email": row.email, "city": row.city, "country": row.country,
            "converted_at": row.converted_at,
        },
        "company": {"id": str(row.company_id), "name": row.company_name} if sees_customer else None,
        "lead_id": str(row.lead_id) if sees_customer and ctx.has(perms.PERM_LEADS_VIEW) else None,
        "allowed_stages": list(onboarding_allowed_stages(o.stage, has_assignee=o.assigned_to is not None)) if manageable else [],
        "can": {
            "update": manageable,
            "change_stage": manageable,
            "assign": ctx.has(perms.PERM_ONBOARDING_ASSIGN) and can_see_onboarding(ctx, assigned_to=o.assigned_to),
            "view_customer": sees_customer,
            "view_lead": sees_customer and ctx.has(perms.PERM_LEADS_VIEW),
        },
    }


async def _one(db: AsyncSession, ctx: StaffContext, onboarding_id: uuid.UUID) -> dict:
    return onboarding_out(await onboarding_repo.get_row(db, onboarding_id), ctx)


def _person(user_id, name) -> Optional[dict]:
    return work.person(user_id, name) if user_id is not None else None


async def _load_visible(db: AsyncSession, ctx: StaffContext, onboarding_id: str) -> OnboardingRow:
    """One record with its joined references, for reading: 404 when it does not exist (or the id is malformed), 403 when
    it is outside the caller's onboarding scope."""
    parsed = parse_uuid(onboarding_id)
    row = await onboarding_repo.get_row(db, parsed) if parsed else None
    if row is None:
        raise work.not_found("Onboarding")
    if not can_see_onboarding(ctx, assigned_to=row.onboarding.assigned_to):
        raise access_denied()
    return row


async def _load_for_update(db: AsyncSession, ctx: StaffContext, onboarding_id: str) -> Tuple[SalesOnboarding, SalesCustomer]:
    """A record locked for a change, and its customer. Order: peek (404 / scope 403), THEN lock and re-read - so the
    stage and owner are checked against what the database really holds, not against what was there before a wait, and
    the scope is judged on the owner as it is NOW (a record reassigned while this request waited is out of scope for its
    previous owner)."""
    parsed = parse_uuid(onboarding_id)
    peek = await onboarding_repo.get(db, parsed) if parsed else None
    if peek is None:
        raise work.not_found("Onboarding")
    if not can_see_onboarding(ctx, assigned_to=peek.assigned_to):
        raise access_denied()
    record = await onboarding_repo.get(db, parsed, for_update=True)
    if record is None:
        raise work.not_found("Onboarding")
    if not can_see_onboarding(ctx, assigned_to=record.assigned_to):
        raise access_denied()
    customer = await customers_repo.get(db, record.customer_id)
    if customer is None:                                          # cannot happen: the FK is RESTRICT
        raise work.not_found("Onboarding")
    return record, customer


# ---- reads -------------------------------------------------------------------------------------------------------

async def list_onboarding(
    db: AsyncSession, ctx: StaffContext, filters: OnboardingFilters, *, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    rows, total = await onboarding_repo.list_rows(
        db, visibility=onboarding_visibility(ctx), filters=filters, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return {"items": [onboarding_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def counts(db: AsyncSession, ctx: StaffContext, filters: OnboardingFilters) -> dict:
    return await onboarding_repo.counts(db, visibility=onboarding_visibility(ctx), filters=filters, me=ctx.user_id)


async def get_onboarding(db: AsyncSession, ctx: StaffContext, onboarding_id: str) -> dict:
    return onboarding_out(await _load_visible(db, ctx, onboarding_id), ctx)


async def list_assignees(db: AsyncSession) -> list:
    return [
        {"id": str(user.id), "name": user.name, "email": user.email, "open_onboarding": open_onboarding}
        for user, open_onboarding in await onboarding_repo.list_assignees(db)
    ]


async def list_timeline(db: AsyncSession, ctx: StaffContext, onboarding_id: str, limit: int, offset: int) -> dict:
    """The record's own timeline: the onboarding events of the customer's lead (and only those - never the lead's own
    history, which an Onboarding Employee is not entitled to), newest first."""
    row = await _load_visible(db, ctx, onboarding_id)
    return await activity_service.list_events(db, row.lead_id, limit, offset, activity_service.ONBOARDING_EVENT_TYPES)


# ---- writes ------------------------------------------------------------------------------------------------------

async def _require_assignee(db: AsyncSession, assignee_id: uuid.UUID) -> User:
    """The user, provided they may be given an onboarding RIGHT NOW (see the module docstring). A deactivated account, a
    revoked grant, a retired role, a Sales Employee or Lead Data Entry account, a customer user, a Super Admin and a
    made-up id all get the same answer."""
    user = await onboarding_repo.get_assignable_user(db, assignee_id, lock=True)
    if user is None:
        raise field_error(
            "assigned_to", "This person cannot be given an onboarding", 400, code="onboarding_assignee_not_eligible"
        )
    return user


async def assign_onboarding(db: AsyncSession, ctx: StaffContext, onboarding_id: str, body, audit: AuditContext) -> dict:
    record, customer = await _load_for_update(db, ctx, onboarding_id)
    assignee = await _require_assignee(db, body.assigned_to)

    if record.assigned_to == assignee.id:
        return await _one(db, ctx, record.id)                     # already theirs: no write, no event

    previous = await work_repo.get_user(db, record.assigned_to) if record.assigned_to is not None else None
    before = {
        "assigned_to": _person(record.assigned_to, previous.name if previous else None),
        "stage": record.stage, "customer_status": customer.status,
    }
    record.assigned_to = assignee.id
    if record.stage == ONBOARDING_START:                          # the first owner takes it out of `won`
        record.stage = ONBOARDING_ASSIGNED
    customer.status = customer_status_for_stage(record.stage)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, customer.lead_id, activity_service.EVENT_ONBOARDING_ASSIGNED,
        before=before,
        after={"assigned_to": work.person(assignee.id, assignee.name), "stage": record.stage, "customer_status": customer.status},
        note=work.clip(body.note),
        metadata={"onboarding_id": str(record.id), "customer_id": str(customer.id), "reassigned": previous is not None},
    )
    logger.info(
        "onboarding_assigned actor=%s onboarding=%s assignee=%s previous=%s",
        ctx.user_id, record.id, assignee.id, previous.id if previous else None,
    )
    return await _one(db, ctx, record.id)


async def change_stage(db: AsyncSession, ctx: StaffContext, onboarding_id: str, body, audit: AuditContext) -> dict:
    record, customer = await _load_for_update(db, ctx, onboarding_id)

    error = onboarding_transition_error(record.stage, body.stage, has_assignee=record.assigned_to is not None)
    if error is not None:
        code, message = error
        raise field_error("stage", message, 400 if code in _STAGE_BAD_REQUEST else 409, code=code)

    before = {"stage": record.stage, "customer_status": customer.status, "completed_at": record.completed_at}
    record.stage = body.stage
    record.completed_at = work.now() if body.stage == ONBOARDING_DONE else None
    customer.status = customer_status_for_stage(record.stage)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, customer.lead_id, activity_service.EVENT_ONBOARDING_STAGE_CHANGED,
        before=before,
        after={"stage": record.stage, "customer_status": customer.status, "completed_at": record.completed_at},
        note=work.clip(body.note),
        metadata={"onboarding_id": str(record.id), "customer_id": str(customer.id)},
    )
    logger.info("onboarding_stage_changed actor=%s onboarding=%s %s->%s", ctx.user_id, record.id, before["stage"], record.stage)
    return await _one(db, ctx, record.id)


async def update_onboarding(db: AsyncSession, ctx: StaffContext, onboarding_id: str, body, audit: AuditContext) -> dict:
    record, customer = await _load_for_update(db, ctx, onboarding_id)

    values = body.model_dump(exclude_unset=True)
    if not values:
        raise work.empty_update()
    if record.notes == values["notes"]:
        return await _one(db, ctx, record.id)                     # nothing actually changes: no write, no event

    before = {"notes": record.notes}
    record.notes = values["notes"]
    await db.flush()

    await activity_service.record(
        db, ctx, audit, customer.lead_id, activity_service.EVENT_ONBOARDING_NOTES_UPDATED,
        before=before, after={"notes": record.notes},
        metadata={"onboarding_id": str(record.id), "customer_id": str(customer.id)},
    )
    logger.info("onboarding_notes_updated actor=%s onboarding=%s", ctx.user_id, record.id)
    return await _one(db, ctx, record.id)
