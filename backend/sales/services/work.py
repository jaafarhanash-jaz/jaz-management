"""Rules shared by the Phase-3 work items: calls, follow-ups, demos and trials.

Authorization is layered exactly like the rest of JAZ Sales:
  1. the router's require_permission(...) decides whether the caller may perform the ACTION at all
     (sales.<kind>.view / sales.<kind>.manage);
  2. lead_access decides on WHICH leads: a work item is visible and actionable exactly when its lead is. There is no
     second scope system - an item on a lead outside the caller's scope answers 403 "Access denied", an unknown or
     malformed id answers 404;
  3. the services enforce the business rules (each item's state machine, who may receive it, archived leads, time).

Every state change runs under a SHARE lock on the item's lead and, for an existing item, a row lock on the item
(lead first, then item - always in that order), so concurrent operations are applied one after another, the state a
request validated is the state it changes, and a lead cannot be archived between the check and the write.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from models import User
from sales import permissions as perms
from sales.models import SalesLead
from sales.repositories import staff as staff_repo
from sales.repositories import work as work_repo
from sales.repositories.work import LeadInfo
from sales.services.access import StaffContext
from sales.services.common import access_denied, field_error
from sales.services.lead_access import can_see
from services.admin import parse_uuid

# The most characters of a free-text note copied into a timeline event. The item itself keeps the full text.
NOTE_CLIP = 1000


def now() -> datetime:
    return datetime.now(timezone.utc)


def clip(text: Optional[str], limit: int = NOTE_CLIP) -> Optional[str]:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "…"


# ---- shaping -----------------------------------------------------------------------------------------------

def user_ref(user_id, name, status=None) -> Optional[dict]:
    """The API shape of a person a work item names."""
    if user_id is None:
        return None
    return {"id": str(user_id), "name": name or "", "status": status}


def person(user_id, name) -> dict:
    """The timeline shape of a person: who, by id and by name AT THE TIME (accounts can be renamed later)."""
    return {"id": str(user_id), "name": name or ""}


def lead_ref(info: LeadInfo) -> dict:
    return {
        "id": str(info.id),
        "business_name": info.business_name,
        "pipeline_stage": info.pipeline_stage,
        "archived": info.archived,
    }


def changed_fields(item: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of `values` that actually differs from what the item holds (no write, no event when empty)."""
    return {field: value for field, value in values.items() if getattr(item, field) != value}


# ---- errors ------------------------------------------------------------------------------------------------

def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def lead_not_found() -> HTTPException:
    return field_error("lead_id", "Lead not found", 404, code="lead_not_found")


def not_open(field: str, message: str, code: str) -> HTTPException:
    """The item is already in a terminal status: a conflict with its current state."""
    return field_error(field, message, 409, code=code)


def reject_future(field: str, value: datetime, *, tolerance) -> None:
    """For something that has ALREADY HAPPENED (a call, the start or the end of a trial): it cannot be in the future,
    give or take the tolerance for a slightly fast client clock."""
    if value > now() + tolerance:
        raise field_error(field, "This time cannot be in the future", 400, code=f"{field}_in_future")


def empty_update() -> HTTPException:
    return HTTPException(status_code=400, detail="No valid fields to update")


def not_clearable(values: Dict[str, Any], fields) -> None:
    for field in fields:
        if field in values and values[field] is None:
            raise field_error(field, "This field cannot be empty")


# ---- loading and guards ------------------------------------------------------------------------------------

def ensure_lead_active(lead: SalesLead) -> None:
    if lead.archived_at is not None:
        raise field_error("lead", "This lead is archived. Restore it to make changes.", 409, code="lead_archived")


def ensure_visible(ctx: StaffContext, created_by: uuid.UUID, assigned_to: Optional[uuid.UUID]) -> None:
    if not can_see(ctx, created_by=created_by, assigned_to=assigned_to):
        raise access_denied()


async def get_row_visible(db, ctx: StaffContext, repo, item_id: str, what: str):
    """One work item with its joined references, for reading: 404 when it does not exist (or the id is malformed),
    403 when its lead is outside the caller's scope."""
    parsed = parse_uuid(item_id)
    row = await repo.get_row(db, parsed) if parsed else None
    if row is None:
        raise not_found(what)
    ensure_visible(ctx, row.lead.created_by, row.lead.assigned_to)
    return row


async def load_lead_for_new_work(db, ctx: StaffContext, lead_id: uuid.UUID) -> SalesLead:
    """The lead a new work item is for: it must exist (404), be in the caller's scope (403) and not be archived (409).
    Held under a SHARE lock for the rest of the transaction (see repositories/work.get_lead_shared)."""
    lead = await work_repo.get_lead_shared(db, lead_id)
    if lead is None:
        raise lead_not_found()
    ensure_visible(ctx, lead.created_by, lead.assigned_to)
    ensure_lead_active(lead)
    return lead


async def load_item_for_update(db, ctx: StaffContext, repo, item_id: str, what: str) -> Tuple[Any, SalesLead]:
    """An existing work item, locked for a change, and its lead. Order: peek at the item to learn its lead (a lead
    never changes), lock the LEAD (shared), check scope and archive state, THEN lock and re-read the item - so its
    status is checked against what the database really holds, not against what was there before the wait."""
    parsed = parse_uuid(item_id)
    peek = await repo.get(db, parsed) if parsed else None
    if peek is None:
        raise not_found(what)
    lead = await work_repo.get_lead_shared(db, peek.lead_id)
    if lead is None:
        raise not_found(what)
    ensure_visible(ctx, lead.created_by, lead.assigned_to)
    ensure_lead_active(lead)
    item = await repo.get(db, parsed, for_update=True)
    if item is None:
        raise not_found(what)
    return item, lead


# ---- who may receive a follow-up / a demo ------------------------------------------------------------------

_ELIGIBILITY_MESSAGE = "This person cannot receive this work"


async def require_assignee(db, lead: SalesLead, assignee_id: uuid.UUID, *, permission: str) -> User:
    """The user, provided they may be given this kind of work on this lead RIGHT NOW: an active `jaz_staff` account
    that holds `permission` (sales.followups.manage / sales.demos.manage) through an ACTIVE role, and that can see
    the lead (an assignee who could not open the lead could not do the work). A deactivated account, a revoked
    grant, a retired role, a Lead Data Entry or Onboarding account, a customer user, a Super Admin and a made-up id
    all get the same answer."""
    user = await work_repo.get_usable_staff_user(db, assignee_id, lock=True)
    granted = frozenset()
    if user is not None:
        _, granted_set = await staff_repo.get_permission_profile(db, user.id)
        granted = frozenset(granted_set)
    if user is None or permission not in granted:
        raise field_error("assigned_to", _ELIGIBILITY_MESSAGE, 400, code="activity_assignee_not_eligible")
    assignee_ctx = StaffContext({"id": str(user.id), "role": user.role, "status": user.status}, user.id, False, tuple(), granted)
    if not can_see(assignee_ctx, created_by=lead.created_by, assigned_to=lead.assigned_to):
        raise field_error("assigned_to", "This person cannot see this lead", 400, code="activity_assignee_cannot_see_lead")
    return user


async def resolve_assignee(
    db, ctx: StaffContext, lead: SalesLead, requested: Optional[uuid.UUID], *, permission: str
) -> User:
    """Who a new follow-up / demo goes to. Nobody named = the caller. Naming somebody ELSE needs sales.leads.assign
    (choosing who does the work is a separate privilege, like choosing a lead's owner). Either way the person must
    be eligible (require_assignee) - so a caller who cannot themself receive this work (a Super Admin) has to name
    someone who can."""
    target = requested if requested is not None else ctx.user_id
    if target != ctx.user_id and not ctx.has(perms.PERM_LEADS_ASSIGN):
        raise access_denied()
    try:
        return await require_assignee(db, lead, target, permission=permission)
    except HTTPException as exc:
        if requested is None and exc.status_code == 400:
            raise field_error(
                "assigned_to", "Choose who this is for", 400, code="activity_assignee_required"
            )
        raise


async def change_assignee(db, ctx: StaffContext, lead: SalesLead, new_assignee_id: uuid.UUID, *, permission: str) -> User:
    """Changing who has an existing follow-up / demo: needs sales.leads.assign, and the new person must be eligible.
    (The CURRENT assignee is never re-checked: an item of somebody who was deactivated meanwhile can still be edited
    and handed to someone else - that is exactly how a manager cleans up.)"""
    if not ctx.has(perms.PERM_LEADS_ASSIGN):
        raise access_denied()
    return await require_assignee(db, lead, new_assignee_id, permission=permission)
