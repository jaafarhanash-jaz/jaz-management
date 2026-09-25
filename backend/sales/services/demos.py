"""Demos (Phase 3): Sales-side scheduling and tracking of product demos for a lead.

Lifecycle: `scheduled` -> `completed` | `cancelled` | `no_show`, all terminal. A scheduled demo whose time has
passed is simply still waiting for its outcome ("past due"); the clock never changes a status by itself. Only the
time can move (reschedule), and only while the demo is scheduled. There is NO calendar integration: this is
tracking, not an appointment system. Nothing is ever deleted.

Same rules as follow-ups for who can be given a demo (an active staff member holding sales.demos.manage who can
see the lead; naming somebody other than yourself, or changing the assignee later, also needs sales.leads.assign).
Every action - schedule, reschedule, update, complete, cancel, no-show - is recorded on the lead's timeline in the
same transaction. Nothing here moves the lead in the pipeline: after a demo is scheduled or completed the UI may
OFFER the matching stage, but the change itself is the ordinary, separately validated stage action.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import DEMO_OPEN
from sales.models import SalesDemo
from sales.repositories import demos as demos_repo
from sales.repositories import work as work_repo
from sales.repositories.demos import DemoFilters, DemoRow
from sales.services import activity as activity_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from sales.services.lead_access import lead_visibility

logger = logging.getLogger("sales.demos")


def demo_out(row: DemoRow, ctx: StaffContext) -> dict:
    d = row.demo
    workable = ctx.has(perms.PERM_DEMOS_MANAGE) and d.status == DEMO_OPEN and not row.lead.archived
    return {
        "id": str(d.id),
        "lead": work.lead_ref(row.lead),
        "assigned_to": work.user_ref(d.assigned_to, row.assignee_name, row.assignee_status),
        "scheduled_at": d.scheduled_at,
        "status": d.status,
        "notes": d.notes,
        "completed_at": d.completed_at,
        "is_past_due": row.is_past_due,
        "created_by": work.user_ref(d.created_by, row.creator_name),
        "created_at": d.created_at,
        "updated_at": d.updated_at,
        "can": {
            "update": workable,
            "reschedule": workable,
            "complete": workable,
            "cancel": workable,
            "no_show": workable,
            "assign": workable and ctx.has(perms.PERM_LEADS_ASSIGN),
        },
    }


async def _one(db: AsyncSession, ctx: StaffContext, demo_id: uuid.UUID) -> dict:
    return demo_out(await demos_repo.get_row(db, demo_id), ctx)


def _not_scheduled(demo: SalesDemo):
    return work.not_open("status", f"This demo is already {demo.status.replace('_', ' ')}", "demo_not_scheduled")


# ---- reads -------------------------------------------------------------------------------------------------

async def list_demos(db: AsyncSession, ctx: StaffContext, filters: DemoFilters, *, descending: bool, limit: int, offset: int) -> dict:
    rows, total = await demos_repo.list_rows(
        db, visibility=lead_visibility(ctx), filters=filters, descending=descending, limit=limit, offset=offset
    )
    return {"items": [demo_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def counts(db: AsyncSession, ctx: StaffContext, filters: DemoFilters) -> dict:
    return await demos_repo.counts(db, visibility=lead_visibility(ctx), filters=filters, me=ctx.user_id)


async def get_demo(db: AsyncSession, ctx: StaffContext, demo_id: str) -> dict:
    return demo_out(await work.get_row_visible(db, ctx, demos_repo, demo_id, "Demo"), ctx)


# ---- writes ------------------------------------------------------------------------------------------------

async def schedule_demo(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    lead = await work.load_lead_for_new_work(db, ctx, body.lead_id)
    assignee = await work.resolve_assignee(db, ctx, lead, body.assigned_to, permission=perms.PERM_DEMOS_MANAGE)

    demo = SalesDemo(
        id=uuid.uuid4(), lead_id=lead.id, assigned_to=assignee.id, scheduled_at=body.scheduled_at, notes=body.notes,
        status=DEMO_OPEN, created_by=ctx.user_id,
    )
    db.add(demo)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_DEMO_SCHEDULED,
        after={"scheduled_at": demo.scheduled_at, "assigned_to": work.person(assignee.id, assignee.name)},
        note=work.clip(demo.notes), metadata={"demo_id": str(demo.id)},
    )
    logger.info("demo_scheduled actor=%s demo=%s lead=%s assignee=%s", ctx.user_id, demo.id, lead.id, assignee.id)
    return await _one(db, ctx, demo.id)


async def update_demo(db: AsyncSession, ctx: StaffContext, demo_id: str, body, audit: AuditContext) -> dict:
    """Notes and who has the demo. (The time moves through reschedule_demo.)"""
    demo, lead = await work.load_item_for_update(db, ctx, demos_repo, demo_id, "Demo")

    values = body.model_dump(exclude_unset=True)
    if not values:
        raise work.empty_update()
    work.not_clearable(values, ("assigned_to",))
    if demo.status != DEMO_OPEN:
        raise _not_scheduled(demo)

    changes = work.changed_fields(demo, values)
    if not changes:
        return await _one(db, ctx, demo.id)      # nothing actually changes: no write, no event

    before = {field: getattr(demo, field) for field in changes}
    new_assignee = None
    if "assigned_to" in changes:
        new_assignee = await work.change_assignee(db, ctx, lead, changes["assigned_to"], permission=perms.PERM_DEMOS_MANAGE)
        previous = await work_repo.get_user(db, demo.assigned_to)
        before["assigned_to"] = work.person(demo.assigned_to, previous.name if previous else None)

    for field, value in changes.items():
        setattr(demo, field, value)
    await db.flush()

    after = {field: getattr(demo, field) for field in changes}
    if new_assignee is not None:
        after["assigned_to"] = work.person(new_assignee.id, new_assignee.name)
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_DEMO_UPDATED,
        before=before, after=after, metadata={"demo_id": str(demo.id), "fields": sorted(changes)},
    )
    logger.info("demo_updated actor=%s demo=%s fields=%s", ctx.user_id, demo.id, sorted(changes))
    return await _one(db, ctx, demo.id)


async def reschedule_demo(db: AsyncSession, ctx: StaffContext, demo_id: str, body, audit: AuditContext) -> dict:
    demo, lead = await work.load_item_for_update(db, ctx, demos_repo, demo_id, "Demo")
    if demo.status != DEMO_OPEN:
        raise _not_scheduled(demo)
    if body.scheduled_at == demo.scheduled_at:
        raise field_error("scheduled_at", "The demo is already scheduled for this time", 409, code="demo_same_time")

    previous = demo.scheduled_at
    demo.scheduled_at = body.scheduled_at
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_DEMO_RESCHEDULED,
        before={"scheduled_at": previous}, after={"scheduled_at": demo.scheduled_at},
        note=work.clip(body.note), metadata={"demo_id": str(demo.id)},
    )
    logger.info("demo_rescheduled actor=%s demo=%s lead=%s", ctx.user_id, demo.id, lead.id)
    return await _one(db, ctx, demo.id)


async def _close_demo(
    db: AsyncSession, ctx: StaffContext, demo_id: str, note, audit: AuditContext, *, status: str, event: str
) -> dict:
    """complete / cancel / no-show: the one open status -> a terminal status, recorded with the demo's time."""
    demo, lead = await work.load_item_for_update(db, ctx, demos_repo, demo_id, "Demo")
    if demo.status != DEMO_OPEN:
        raise _not_scheduled(demo)

    scheduled_at = demo.scheduled_at
    after = {"status": status}
    demo.status = status
    if status == "completed":
        demo.completed_at = work.now()
        after["completed_at"] = demo.completed_at
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, event,
        before={"status": DEMO_OPEN, "scheduled_at": scheduled_at}, after=after,
        note=work.clip(note), metadata={"demo_id": str(demo.id)},
    )
    logger.info("%s actor=%s demo=%s lead=%s", event, ctx.user_id, demo.id, lead.id)
    return await _one(db, ctx, demo.id)


async def complete_demo(db, ctx, demo_id, note, audit) -> dict:
    return await _close_demo(db, ctx, demo_id, note, audit, status="completed", event=activity_service.EVENT_DEMO_COMPLETED)


async def cancel_demo(db, ctx, demo_id, note, audit) -> dict:
    return await _close_demo(db, ctx, demo_id, note, audit, status="cancelled", event=activity_service.EVENT_DEMO_CANCELLED)


async def mark_no_show(db, ctx, demo_id, note, audit) -> dict:
    return await _close_demo(db, ctx, demo_id, note, audit, status="no_show", event=activity_service.EVENT_DEMO_NO_SHOW)
