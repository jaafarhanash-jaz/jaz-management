"""Follow-ups (Phase 3): something to do about a lead by a due time, given to one employee.

Lifecycle: `pending` -> `completed` | `cancelled`, both terminal. "Overdue" is not a status: it is pending AND
due_at < now(), read from the clock. Nothing is ever deleted.

  * Who can receive one: an active staff member who holds sales.followups.manage through an active role and can see
    the lead (services/work.require_assignee) - a deactivated or revoked employee cannot be given a NEW follow-up.
    Existing follow-ups of somebody who was deactivated stay where they are (and show up overdue to the manager, who
    can hand them over): nothing is lost, nothing is silently reassigned.
  * Creating for yourself needs sales.followups.manage; naming somebody else - or changing the assignee later -
    also needs sales.leads.assign.
  * Completing / cancelling / editing works on any follow-up of a lead the caller may see.
  * Every change is recorded on the lead's timeline in the same transaction (followup_created / _updated /
    _completed / _cancelled). Nothing here moves the lead in the pipeline.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import FOLLOWUP_OPEN
from sales.models import SalesFollowup
from sales.repositories import followups as followups_repo
from sales.repositories import work as work_repo
from sales.repositories.followups import FollowupFilters, FollowupRow
from sales.services import activity as activity_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.lead_access import lead_visibility

logger = logging.getLogger("sales.followups")

_NOT_CLEARABLE = ("due_at", "assigned_to")


def followup_out(row: FollowupRow, ctx: StaffContext) -> dict:
    f = row.followup
    workable = ctx.has(perms.PERM_FOLLOWUPS_MANAGE) and f.status == FOLLOWUP_OPEN and not row.lead.archived
    return {
        "id": str(f.id),
        "lead": work.lead_ref(row.lead),
        "assigned_to": work.user_ref(f.assigned_to, row.assignee_name, row.assignee_status),
        "due_at": f.due_at,
        "notes": f.notes,
        "status": f.status,
        "completed_at": f.completed_at,
        "is_overdue": row.is_overdue,
        "created_by": work.user_ref(f.created_by, row.creator_name),
        "created_at": f.created_at,
        "updated_at": f.updated_at,
        "can": {
            "update": workable,
            "complete": workable,
            "cancel": workable,
            "assign": workable and ctx.has(perms.PERM_LEADS_ASSIGN),
        },
    }


async def _one(db: AsyncSession, ctx: StaffContext, followup_id: uuid.UUID) -> dict:
    return followup_out(await followups_repo.get_row(db, followup_id), ctx)


def _not_pending(followup: SalesFollowup):
    return work.not_open("status", f"This follow-up is already {followup.status}", "followup_not_pending")


# ---- reads -------------------------------------------------------------------------------------------------

async def list_followups(
    db: AsyncSession, ctx: StaffContext, filters: FollowupFilters, *, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    rows, total = await followups_repo.list_rows(
        db, visibility=lead_visibility(ctx), filters=filters, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return {"items": [followup_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def counts(db: AsyncSession, ctx: StaffContext, filters: FollowupFilters) -> dict:
    return await followups_repo.counts(db, visibility=lead_visibility(ctx), filters=filters, me=ctx.user_id)


async def get_followup(db: AsyncSession, ctx: StaffContext, followup_id: str) -> dict:
    return followup_out(await work.get_row_visible(db, ctx, followups_repo, followup_id, "Follow-up"), ctx)


# ---- writes ------------------------------------------------------------------------------------------------

async def create_followup(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    lead = await work.load_lead_for_new_work(db, ctx, body.lead_id)
    assignee = await work.resolve_assignee(db, ctx, lead, body.assigned_to, permission=perms.PERM_FOLLOWUPS_MANAGE)

    followup = SalesFollowup(
        id=uuid.uuid4(), lead_id=lead.id, assigned_to=assignee.id, due_at=body.due_at, notes=body.notes,
        status=FOLLOWUP_OPEN, created_by=ctx.user_id,
    )
    db.add(followup)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_FOLLOWUP_CREATED,
        after={"due_at": followup.due_at, "assigned_to": work.person(assignee.id, assignee.name)},
        note=work.clip(followup.notes), metadata={"followup_id": str(followup.id)},
    )
    logger.info("followup_created actor=%s followup=%s lead=%s assignee=%s", ctx.user_id, followup.id, lead.id, assignee.id)
    return await _one(db, ctx, followup.id)


async def update_followup(db: AsyncSession, ctx: StaffContext, followup_id: str, body, audit: AuditContext) -> dict:
    followup, lead = await work.load_item_for_update(db, ctx, followups_repo, followup_id, "Follow-up")

    values = body.model_dump(exclude_unset=True)
    if not values:
        raise work.empty_update()
    work.not_clearable(values, _NOT_CLEARABLE)
    if followup.status != FOLLOWUP_OPEN:
        raise _not_pending(followup)

    changes = work.changed_fields(followup, values)
    if not changes:
        return await _one(db, ctx, followup.id)      # nothing actually changes: no write, no event

    before = {field: getattr(followup, field) for field in changes}
    new_assignee = None
    if "assigned_to" in changes:
        new_assignee = await work.change_assignee(db, ctx, lead, changes["assigned_to"], permission=perms.PERM_FOLLOWUPS_MANAGE)
        previous = await work_repo.get_user(db, followup.assigned_to)
        before["assigned_to"] = work.person(followup.assigned_to, previous.name if previous else None)

    for field, value in changes.items():
        setattr(followup, field, value)
    await db.flush()

    after = {field: getattr(followup, field) for field in changes}
    if new_assignee is not None:
        after["assigned_to"] = work.person(new_assignee.id, new_assignee.name)
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_FOLLOWUP_UPDATED,
        before=before, after=after, metadata={"followup_id": str(followup.id), "fields": sorted(changes)},
    )
    logger.info("followup_updated actor=%s followup=%s fields=%s", ctx.user_id, followup.id, sorted(changes))
    return await _one(db, ctx, followup.id)


async def complete_followup(db: AsyncSession, ctx: StaffContext, followup_id: str, note, audit: AuditContext) -> dict:
    followup, lead = await work.load_item_for_update(db, ctx, followups_repo, followup_id, "Follow-up")
    if followup.status != FOLLOWUP_OPEN:
        raise _not_pending(followup)

    completed_at = work.now()
    was_overdue = followup.due_at < completed_at
    due_at = followup.due_at
    followup.status = "completed"
    followup.completed_at = completed_at
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_FOLLOWUP_COMPLETED,
        before={"status": FOLLOWUP_OPEN, "due_at": due_at}, after={"status": "completed", "completed_at": completed_at},
        note=work.clip(note), metadata={"followup_id": str(followup.id), "was_overdue": was_overdue},
    )
    logger.info("followup_completed actor=%s followup=%s lead=%s overdue=%s", ctx.user_id, followup.id, lead.id, was_overdue)
    return await _one(db, ctx, followup.id)


async def cancel_followup(db: AsyncSession, ctx: StaffContext, followup_id: str, note, audit: AuditContext) -> dict:
    followup, lead = await work.load_item_for_update(db, ctx, followups_repo, followup_id, "Follow-up")
    if followup.status != FOLLOWUP_OPEN:
        raise _not_pending(followup)

    due_at = followup.due_at
    followup.status = "cancelled"
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_FOLLOWUP_CANCELLED,
        before={"status": FOLLOWUP_OPEN, "due_at": due_at}, after={"status": "cancelled"},
        note=work.clip(note), metadata={"followup_id": str(followup.id)},
    )
    logger.info("followup_cancelled actor=%s followup=%s lead=%s", ctx.user_id, followup.id, lead.id)
    return await _one(db, ctx, followup.id)
