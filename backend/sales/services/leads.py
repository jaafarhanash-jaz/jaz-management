"""Lead operations (Phase 2): create, read, update, archive, assign, and the pipeline.

Authorization is layered exactly like the rest of JAZ Sales:
  1. the router's require_permission(...) decides whether the caller may perform the ACTION at all;
  2. lead_access decides on WHICH leads (scope_all / scope_assigned / scope_intake, from permissions);
  3. this module enforces the business rules (pipeline transitions, assignment eligibility, duplicates).
A lead outside the caller's scope answers 403 "Access denied"; an unknown or malformed id answers 404.

Every state change runs under a row lock on the lead (read -> validate -> write -> record its timeline event in
one transaction), so concurrent operations on a lead are applied one after another and the before/after values
in the timeline are always true.
"""
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales import permissions as perms
from sales.constants import (
    AUTO_STAGE_ON_ASSIGN,
    AUTO_STAGE_ON_UNASSIGN,
    STAGE_ASSIGNED,
    STAGE_LOST,
    STAGE_NEW,
    STAGE_WON,
    allowed_stages,
    transition_error,
)
from sales.models import SalesCampaign, SalesLead
from sales.repositories import campaigns as campaigns_repo
from sales.repositories import leads as leads_repo
from sales.repositories.leads import LeadFilters, LeadRow
from sales.services import activity as activity_service
from sales.services import duplicates as duplicates_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import access_denied, field_error
from sales.services.lead_access import can_see, lead_visibility
from sales.services.normalize import norm_text
from services.admin import parse_uuid
from services.identifiers import phone_error

logger = logging.getLogger("sales.leads")

# Recorded on lead_created: what the lead started as. Long free text (description, notes) stays on the lead.
_CREATED_FIELDS = (
    "business_name", "business_type", "contact_name", "contact_position", "phone", "whatsapp", "email", "website",
    "country", "city", "address", "latitude", "longitude", "source", "priority", "estimated_value", "pipeline_stage",
)
_NOT_CLEARABLE = ("business_name", "source", "priority")
# lead field -> the Keys attribute holding its normalized value
_IDENTITY_KEYS = {
    "phone": "phone", "whatsapp": "whatsapp", "email": "email", "website": "website",
    "business_name": "name", "city": "city",
}

# stage-change failures that are about the REQUEST (400); everything else is a conflict with the lead's state (409)
_STAGE_BAD_REQUEST = {"invalid_stage", "lost_reason_required", "invalid_lost_reason", "lost_reason_not_allowed"}


# ---- shaping -----------------------------------------------------------------------------------------------

def _ref(user_id, name, status=None) -> Optional[dict]:
    if user_id is None:
        return None
    return {"id": str(user_id), "name": name or "", "status": status}


_SUMMARY_COLUMNS = (
    "business_name", "business_type", "contact_name", "contact_position", "phone", "whatsapp", "email", "website",
    "country", "city", "address", "latitude", "longitude", "source", "pipeline_stage", "lost_reason", "priority",
    "assigned_at", "closed_at", "archived_at", "created_at", "updated_at",
)


def lead_out(row: LeadRow, ctx: StaffContext, *, detail: bool) -> dict:
    """The API shape of a lead. Explicit field list - the normalized duplicate columns never leave the server.
    `allowed_stages` and `can` are what THIS caller may do to THIS lead right now; the UI mirrors them, and the
    endpoints enforce the same rules regardless."""
    lead = row.lead
    archived = lead.archived_at is not None
    can_stage = ctx.has(perms.PERM_LEADS_CHANGE_STAGE) and not archived
    out: Dict[str, Any] = {
        "id": str(lead.id),
        **{column: getattr(lead, column) for column in _SUMMARY_COLUMNS},
        "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None,
        "campaign_id": str(lead.campaign_id) if lead.campaign_id else None,
        "campaign": {"id": str(lead.campaign_id), "name": row.campaign_name, "status": row.campaign_status}
        if lead.campaign_id else None,
        "assigned_to": _ref(lead.assigned_to, row.assignee_name, row.assignee_status),
        "created_by": _ref(lead.created_by, row.creator_name),
        "allowed_stages": list(allowed_stages(lead.pipeline_stage, has_assignee=lead.assigned_to is not None)) if can_stage else [],
        "can": {
            "update": ctx.has(perms.PERM_LEADS_UPDATE) and not archived,
            "change_stage": can_stage,
            "assign": ctx.has(perms.PERM_LEADS_ASSIGN) and not archived,
            "archive": ctx.has(perms.PERM_LEADS_DELETE) and not archived,
            "restore": ctx.has(perms.PERM_LEADS_DELETE) and archived,
        },
    }
    if detail:
        out["description"] = lead.description
        out["notes"] = lead.notes
    return out


async def _detail(db: AsyncSession, ctx: StaffContext, lead_id: uuid.UUID) -> dict:
    row = await leads_repo.get_row(db, lead_id)
    return lead_out(row, ctx, detail=True)


# ---- loading and guards ------------------------------------------------------------------------------------

def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Lead not found")


async def _load_for_update(db: AsyncSession, ctx: StaffContext, lead_id: str) -> SalesLead:
    parsed = parse_uuid(lead_id)
    lead = await leads_repo.get_lead(db, parsed, for_update=True) if parsed else None
    if lead is None:
        raise _not_found()
    if not can_see(ctx, created_by=lead.created_by, assigned_to=lead.assigned_to):
        raise access_denied()
    return lead


def _ensure_active(lead: SalesLead) -> None:
    if lead.archived_at is not None:
        raise field_error("lead", "This lead is archived. Restore it to make changes.", 409, code="lead_archived")


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """A concurrent request can win a race the pre-checks lose (e.g. the campaign was deleted a moment ago); the
    database's own constraints are the real guarantee. Turn that into the clean 400 the pre-check would have
    produced, never a 500."""
    text = str(getattr(exc, "orig", exc))
    if "sales_leads_campaign_id_fkey" in text:
        return field_error("campaign_id", "Unknown campaign")
    if "sales_leads_source_fkey" in text:
        return field_error("source", "Unknown lead source")
    raise exc


def _validate_values(values: Dict[str, Any]) -> None:
    for field in ("phone", "whatsapp"):
        if values.get(field) is not None:
            message = phone_error(values[field])  # the shared Phase-1 phone rule
            if message is not None:
                raise field_error(field, message)
    if values.get("business_name") is not None and norm_text(values["business_name"]) is None:
        raise field_error("business_name", "Business name must contain letters or digits")


def _validate_coordinates(latitude: Optional[float], longitude: Optional[float]) -> None:
    """Both or neither (the database has the same CHECK; this is the clean 400 instead of a constraint error)."""
    if (latitude is None) != (longitude is None):
        raise field_error("latitude" if latitude is None else "longitude", "Latitude and longitude must be given together")


async def _resolve_references(db: AsyncSession, values: Dict[str, Any]) -> Optional[SalesCampaign]:
    """Validate source / campaign server-side (never trusted from the client). Returns the campaign, if any."""
    if "source" in values and not await leads_repo.is_active_source(db, values["source"]):
        raise field_error("source", "Unknown lead source")
    if values.get("campaign_id") is not None:
        campaign = await campaigns_repo.get(db, values["campaign_id"])
        if campaign is None:
            raise field_error("campaign_id", "Unknown campaign")
        return campaign
    return None


async def _require_assignee(db: AsyncSession, assignee_id: uuid.UUID) -> User:
    """The user, provided they may receive assignments RIGHT NOW: an active jaz_staff account whose active
    role grants the assigned-leads scope. A deactivated account, a revoked grant, a retired role, a customer
    user, a Super Admin or a made-up id all get the same answer."""
    user = await leads_repo.get_assignable_user(db, assignee_id, lock=True)
    if user is None:
        raise field_error(
            "assigned_to", "This person cannot receive lead assignments", 400, code="assignee_not_eligible"
        )
    return user


# ---- reads -------------------------------------------------------------------------------------------------

async def list_sources(db: AsyncSession) -> List[dict]:
    return [
        {"key": s.key, "name_en": s.name_en, "name_ar": s.name_ar, "sort_order": s.sort_order}
        for s in await leads_repo.list_sources(db)
    ]


async def list_leads(
    db: AsyncSession, ctx: StaffContext, filters: LeadFilters, *, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    rows, total = await leads_repo.list_leads(
        db, visibility=lead_visibility(ctx), filters=filters, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return {"items": [lead_out(row, ctx, detail=False) for row in rows], "total": total, "limit": limit, "offset": offset}


async def stage_counts(db: AsyncSession, ctx: StaffContext, filters: LeadFilters) -> dict:
    counts = await leads_repo.stage_counts(db, visibility=lead_visibility(ctx), filters=filters)
    return {"counts": counts, "total": sum(counts.values())}


async def get_lead(db: AsyncSession, ctx: StaffContext, lead_id: str) -> dict:
    parsed = parse_uuid(lead_id)
    row = await leads_repo.get_row(db, parsed) if parsed else None
    if row is None:
        raise _not_found()
    if not can_see(ctx, created_by=row.lead.created_by, assigned_to=row.lead.assigned_to):
        raise access_denied()
    return lead_out(row, ctx, detail=True)


async def list_activities(db: AsyncSession, ctx: StaffContext, lead_id: str, limit: int, offset: int) -> dict:
    parsed = parse_uuid(lead_id)
    lead = await leads_repo.get_lead(db, parsed) if parsed else None
    if lead is None:
        raise _not_found()
    if not can_see(ctx, created_by=lead.created_by, assigned_to=lead.assigned_to):
        raise access_denied()
    return await activity_service.list_events(db, lead.id, limit, offset, activity_service.visible_event_types(ctx))


async def list_assignees(db: AsyncSession) -> List[dict]:
    return [
        {"id": str(user.id), "name": user.name, "email": user.email, "open_leads": open_leads}
        for user, open_leads in await leads_repo.list_assignees(db)
    ]


async def duplicate_check(db: AsyncSession, ctx: StaffContext, body) -> dict:
    keys = duplicates_service.Keys.of(
        business_name=body.business_name, city=body.city, phone=body.phone, whatsapp=body.whatsapp,
        email=body.email, website=body.website,
    )
    report = await duplicates_service.check(db, keys, exclude_lead_id=body.exclude_lead_id)
    return duplicates_service.present(report, ctx)


# ---- create / update ---------------------------------------------------------------------------------------

async def create_lead(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    values = body.model_dump(exclude={"confirm_duplicates", "assigned_to"})
    _validate_values(values)
    _validate_coordinates(values["latitude"], values["longitude"])
    campaign = await _resolve_references(db, values)

    assignee = None
    if body.assigned_to is not None:
        # Creating a lead and choosing its owner are separate privileges (same rule as staff create + roles).
        if not ctx.has(perms.PERM_LEADS_ASSIGN):
            raise access_denied()
        assignee = await _require_assignee(db, body.assigned_to)

    keys = duplicates_service.Keys.of(**{k: values[k] for k in ("business_name", "city", "phone", "whatsapp", "email", "website")})
    override = await duplicates_service.enforce(db, ctx, keys, confirm=body.confirm_duplicates)

    lead = SalesLead(id=uuid.uuid4(), created_by=ctx.user_id, pipeline_stage=STAGE_NEW)
    leads_repo.apply_fields(lead, values)
    if assignee is not None:
        lead.assigned_to = assignee.id
        lead.assigned_at = func.clock_timestamp()
        lead.pipeline_stage = STAGE_ASSIGNED
    try:
        async with db.begin_nested():  # savepoint: a lost race must not poison the outer transaction
            db.add(lead)
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    after = {k: v for k, v in activity_service.snapshot(lead, _CREATED_FIELDS).items() if v is not None}
    if campaign is not None:
        after.update(campaign_id=str(campaign.id), campaign_name=campaign.name)
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_CREATED,
        after=after, metadata={"duplicate_override": override} if override else None,
    )
    if assignee is not None:
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_ASSIGNED,
            before={"assigned_to": None}, after={"assigned_to": _person(assignee)},
            metadata={"at_creation": True},
        )
    logger.info("lead_created actor=%s lead=%s assigned=%s", ctx.user_id, lead.id, assignee is not None)
    return await _detail(db, ctx, lead.id)


def _person(user: User) -> dict:
    return {"id": str(user.id), "name": user.name}


def _differs(current: Any, new: Any) -> bool:
    return current != new


async def update_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, body, audit: AuditContext) -> dict:
    lead = await _load_for_update(db, ctx, lead_id)
    _ensure_active(lead)

    values = body.model_dump(exclude_unset=True, exclude={"confirm_duplicates"})
    if not values:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    for field in _NOT_CLEARABLE:
        if field in values and values[field] is None:
            raise field_error(field, "This field cannot be empty")
    _validate_values(values)
    _validate_coordinates(values.get("latitude", lead.latitude), values.get("longitude", lead.longitude))
    new_campaign = await _resolve_references(db, values)

    changes = {f: v for f, v in values.items() if _differs(getattr(lead, f), v)}
    if not changes:
        return await _detail(db, ctx, lead.id)  # nothing actually changes: no write, no event

    # Duplicate detection on the identity keys whose NORMALIZED value changes. An edit that merely reformats a
    # phone number, or touches unrelated fields, never re-raises a duplicate that was already accepted.
    identity = ("business_name", "city", "phone", "whatsapp", "email", "website")
    before_keys = duplicates_service.Keys.of(**{f: getattr(lead, f) for f in identity})
    after_keys = duplicates_service.Keys.of(**{f: changes.get(f, getattr(lead, f)) for f in identity})
    changed_identity = [f for f, attr in _IDENTITY_KEYS.items() if getattr(before_keys, attr) != getattr(after_keys, attr)]
    override = None
    if changed_identity:
        override = await duplicates_service.enforce(
            db, ctx, after_keys, confirm=body.confirm_duplicates, exclude_lead_id=lead.id,
            checks=duplicates_service.checks_for_changed_fields(changed_identity),
        )

    before = {f: getattr(lead, f) for f in changes}
    old_campaign_name = None
    if "campaign_id" in changes and lead.campaign_id is not None:
        old = await campaigns_repo.get(db, lead.campaign_id)
        old_campaign_name = old.name if old else None

    leads_repo.apply_fields(lead, changes)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    after = {f: getattr(lead, f) for f in changes}
    if "campaign_id" in changes:
        before["campaign_name"] = old_campaign_name
        after["campaign_name"] = new_campaign.name if new_campaign else None
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_UPDATED,
        before=before, after=after,
        metadata={"fields": sorted(changes), **({"duplicate_override": override} if override else {})},
    )
    logger.info("lead_updated actor=%s lead=%s fields=%s", ctx.user_id, lead.id, sorted(changes))
    return await _detail(db, ctx, lead.id)


# ---- archive / restore -------------------------------------------------------------------------------------

async def archive_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, audit: AuditContext) -> dict:
    """The application's 'delete': the lead is hidden from listings but nothing is removed, and its whole history
    stays. Idempotent."""
    lead = await _load_for_update(db, ctx, lead_id)
    changed = lead.archived_at is None
    if changed:
        lead.archived_at = func.clock_timestamp()
        lead.archived_by = ctx.user_id
        await db.flush()
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_ARCHIVED, before={"archived": False}, after={"archived": True}
        )
        logger.info("lead_archived actor=%s lead=%s", ctx.user_id, lead.id)
    return {"changed": changed, "lead": await _detail(db, ctx, lead.id)}


async def restore_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, audit: AuditContext) -> dict:
    lead = await _load_for_update(db, ctx, lead_id)
    changed = lead.archived_at is not None
    if changed:
        lead.archived_at = None
        lead.archived_by = None
        await db.flush()
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_RESTORED, before={"archived": True}, after={"archived": False}
        )
        logger.info("lead_restored actor=%s lead=%s", ctx.user_id, lead.id)
    return {"changed": changed, "lead": await _detail(db, ctx, lead.id)}


# ---- assignment --------------------------------------------------------------------------------------------

async def _apply_assignment(
    db: AsyncSession, ctx: StaffContext, audit: AuditContext, lead: SalesLead, assignee: User
) -> bool:
    """Give `lead` (already locked and validated) to `assignee`. False when it already is theirs. Records
    lead_assigned or lead_reassigned, plus an automatic stage_changed when the lead leaves `new`."""
    if lead.assigned_to == assignee.id:
        return False
    previous = await db.get(User, lead.assigned_to) if lead.assigned_to is not None else None
    old_stage = lead.pipeline_stage
    new_stage = AUTO_STAGE_ON_ASSIGN.get(old_stage)

    lead.assigned_to = assignee.id
    lead.assigned_at = func.clock_timestamp()
    if new_stage:
        lead.pipeline_stage = new_stage
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id,
        activity_service.EVENT_LEAD_REASSIGNED if previous is not None else activity_service.EVENT_LEAD_ASSIGNED,
        before={"assigned_to": _person(previous) if previous is not None else None},
        after={"assigned_to": _person(assignee)},
    )
    if new_stage:
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_STAGE_CHANGED,
            before={"pipeline_stage": old_stage}, after={"pipeline_stage": new_stage},
            metadata={"automatic": True, "cause": "assignment"},
        )
    logger.info("lead_assigned actor=%s lead=%s assignee=%s previous=%s", ctx.user_id, lead.id, assignee.id,
                previous.id if previous is not None else None)
    return True


async def assign_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, assignee_id: uuid.UUID, audit: AuditContext) -> dict:
    """Assign, or reassign, one lead to one Sales Employee."""
    lead = await _load_for_update(db, ctx, lead_id)
    _ensure_active(lead)
    assignee = await _require_assignee(db, assignee_id)
    changed = await _apply_assignment(db, ctx, audit, lead, assignee)
    return {"changed": changed, "lead": await _detail(db, ctx, lead.id)}


async def unassign_lead(db: AsyncSession, ctx: StaffContext, lead_id: str, audit: AuditContext) -> dict:
    lead = await _load_for_update(db, ctx, lead_id)
    _ensure_active(lead)
    changed = lead.assigned_to is not None
    if changed:
        previous = await db.get(User, lead.assigned_to)
        old_stage = lead.pipeline_stage
        new_stage = AUTO_STAGE_ON_UNASSIGN.get(old_stage)
        lead.assigned_to = None
        lead.assigned_at = None
        if new_stage:
            lead.pipeline_stage = new_stage
        await db.flush()
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_UNASSIGNED,
            before={"assigned_to": _person(previous) if previous is not None else None}, after={"assigned_to": None},
        )
        if new_stage:
            await activity_service.record(
                db, ctx, audit, lead.id, activity_service.EVENT_STAGE_CHANGED,
                before={"pipeline_stage": old_stage}, after={"pipeline_stage": new_stage},
                metadata={"automatic": True, "cause": "unassignment"},
            )
        logger.info("lead_unassigned actor=%s lead=%s previous=%s", ctx.user_id, lead.id, previous.id if previous else None)
    return {"changed": changed, "lead": await _detail(db, ctx, lead.id)}


async def bulk_assign(
    db: AsyncSession, ctx: StaffContext, lead_ids: List[uuid.UUID], assignee_id: uuid.UUID, audit: AuditContext
) -> dict:
    """All-or-nothing: every lead must exist, be in the caller's scope and not be archived, or NOTHING changes.
    A lead that already belongs to the assignee is simply counted as unchanged."""
    ids = list(dict.fromkeys(lead_ids))
    # Lock order is always lead(s) first, then the assignee's account (shared) - the same order assign_lead uses -
    # so no two operations can wait on each other's locks.
    leads = await leads_repo.get_leads_for_update(db, ids)
    found = {lead.id for lead in leads}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise field_error("lead_ids", "Some leads were not found", 404, code="leads_not_found", missing=missing)
    if any(not can_see(ctx, created_by=lead.created_by, assigned_to=lead.assigned_to) for lead in leads):
        raise access_denied()
    archived = [str(lead.id) for lead in leads if lead.archived_at is not None]
    if archived:
        raise field_error("lead_ids", "Archived leads cannot be assigned", 409, code="lead_archived", archived=archived)
    assignee = await _require_assignee(db, assignee_id)

    changed = 0
    for lead in leads:  # id order, the order they were locked in
        if await _apply_assignment(db, ctx, audit, lead, assignee):
            changed += 1
    return {"total": len(leads), "assigned": changed, "unchanged": len(leads) - changed}


# ---- pipeline ----------------------------------------------------------------------------------------------

async def change_stage(db: AsyncSession, ctx: StaffContext, lead_id: str, body, audit: AuditContext) -> dict:
    """Move a lead along the pipeline. The transition rules live in sales/constants.py (transition_error) and are
    the ONLY authority: the UI merely mirrors `allowed_stages`. Won and lost are explicit, validated states;
    lost always carries a reason; every change is recorded with who, when and before/after."""
    lead = await _load_for_update(db, ctx, lead_id)
    _ensure_active(lead)

    old_stage, old_reason = lead.pipeline_stage, lead.lost_reason
    target = body.stage
    error = transition_error(
        old_stage, target, has_assignee=lead.assigned_to is not None, lost_reason=body.lost_reason
    )
    if error is not None:
        code, message = error
        field = "lost_reason" if code.startswith("lost_reason") or code == "invalid_lost_reason" else "stage"
        raise field_error(field, message, 400 if code in _STAGE_BAD_REQUEST else 409, code=code)

    lead.pipeline_stage = target
    lead.lost_reason = body.lost_reason if target == STAGE_LOST else None
    lead.closed_at = func.clock_timestamp() if target in (STAGE_WON, STAGE_LOST) else None
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_STAGE_CHANGED,
        before={"pipeline_stage": old_stage, "lost_reason": old_reason},
        after={"pipeline_stage": target, "lost_reason": lead.lost_reason},
        note=body.note,
        metadata={"reopened": True} if old_stage == STAGE_LOST else None,
    )
    if target == STAGE_WON:
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_MARKED_WON,
            before={"pipeline_stage": old_stage}, after={"pipeline_stage": STAGE_WON}, note=body.note,
        )
    elif target == STAGE_LOST:
        await activity_service.record(
            db, ctx, audit, lead.id, activity_service.EVENT_LEAD_MARKED_LOST,
            before={"pipeline_stage": old_stage}, after={"pipeline_stage": STAGE_LOST, "lost_reason": body.lost_reason},
            note=body.note,
        )
    logger.info("lead_stage_changed actor=%s lead=%s from=%s to=%s", ctx.user_id, lead.id, old_stage, target)
    return await _detail(db, ctx, lead.id)
