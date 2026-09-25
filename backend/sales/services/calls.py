"""Calls (Phase 3): log, read and correct the calls made to a lead.

A call is a record of something that HAPPENED, so it is never deleted and cannot be dated in the future. Its author
(`employee_id`) is always the caller - it is not a request field, so nobody can log a call in somebody else's name -
and it never changes afterwards; neither does the lead it belongs to. Correcting a call (result, time, duration,
notes) is allowed to anyone who may manage calls on that lead, and every correction is a timeline event carrying
the before and after values, so the history of a call is never lost.

Logging or correcting a call NEVER moves the lead in the pipeline: stage changes stay an explicit, separately
validated action (POST /leads/{id}/stage).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import CLOCK_SKEW_TOLERANCE
from sales.models import SalesCall
from sales.repositories import calls as calls_repo
from sales.repositories.calls import CallFilters, CallRow
from sales.services import activity as activity_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.lead_access import lead_visibility

logger = logging.getLogger("sales.calls")

_NOT_CLEARABLE = ("result", "called_at")


def call_out(row: CallRow, ctx: StaffContext) -> dict:
    """The API shape of a call. `can` is what THIS caller may do to THIS call right now (the UI mirrors it; the
    endpoints enforce the same rules regardless)."""
    call = row.call
    return {
        "id": str(call.id),
        "lead": work.lead_ref(row.lead),
        "employee": work.user_ref(call.employee_id, row.employee_name, row.employee_status),
        "called_at": call.called_at,
        "duration_seconds": call.duration_seconds,
        "result": call.result,
        "notes": call.notes,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
        "can": {"update": ctx.has(perms.PERM_CALLS_MANAGE) and not row.lead.archived},
    }


async def _one(db: AsyncSession, ctx: StaffContext, call_id: uuid.UUID) -> dict:
    return call_out(await calls_repo.get_row(db, call_id), ctx)


# ---- reads -------------------------------------------------------------------------------------------------

async def list_calls(db: AsyncSession, ctx: StaffContext, filters: CallFilters, *, descending: bool, limit: int, offset: int) -> dict:
    rows, total = await calls_repo.list_rows(
        db, visibility=lead_visibility(ctx), filters=filters, descending=descending, limit=limit, offset=offset
    )
    return {"items": [call_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def result_counts(db: AsyncSession, ctx: StaffContext, filters: CallFilters) -> dict:
    counts = await calls_repo.result_counts(db, visibility=lead_visibility(ctx), filters=filters)
    return {"counts": counts, "total": sum(counts.values())}


async def get_call(db: AsyncSession, ctx: StaffContext, call_id: str) -> dict:
    return call_out(await work.get_row_visible(db, ctx, calls_repo, call_id, "Call"), ctx)


# ---- writes ------------------------------------------------------------------------------------------------

async def create_call(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    lead = await work.load_lead_for_new_work(db, ctx, body.lead_id)
    called_at = body.called_at or work.now()
    work.reject_future("called_at", called_at, tolerance=CLOCK_SKEW_TOLERANCE)

    call = SalesCall(
        id=uuid.uuid4(), lead_id=lead.id, employee_id=ctx.user_id, called_at=called_at,
        duration_seconds=body.duration_seconds, result=body.result, notes=body.notes,
    )
    db.add(call)
    await db.flush()

    after = {"result": call.result, "called_at": call.called_at, "employee": work.person(ctx.user_id, ctx.user.get("name"))}
    if call.duration_seconds is not None:
        after["duration_seconds"] = call.duration_seconds
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_CALL_CREATED,
        after=after, note=work.clip(call.notes), metadata={"call_id": str(call.id)},
    )
    logger.info("call_created actor=%s call=%s lead=%s result=%s", ctx.user_id, call.id, lead.id, call.result)
    return await _one(db, ctx, call.id)


async def update_call(db: AsyncSession, ctx: StaffContext, call_id: str, body, audit: AuditContext) -> dict:
    call, lead = await work.load_item_for_update(db, ctx, calls_repo, call_id, "Call")

    values = body.model_dump(exclude_unset=True)
    if not values:
        raise work.empty_update()
    work.not_clearable(values, _NOT_CLEARABLE)
    if values.get("called_at") is not None:
        work.reject_future("called_at", values["called_at"], tolerance=CLOCK_SKEW_TOLERANCE)

    changes = work.changed_fields(call, values)
    if not changes:
        return await _one(db, ctx, call.id)      # nothing actually changes: no write, no event

    before = {field: getattr(call, field) for field in changes}
    for field, value in changes.items():
        setattr(call, field, value)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_CALL_UPDATED,
        before=before, after={field: getattr(call, field) for field in changes},
        metadata={"call_id": str(call.id), "fields": sorted(changes)},
    )
    logger.info("call_updated actor=%s call=%s fields=%s", ctx.user_id, call.id, sorted(changes))
    return await _one(db, ctx, call.id)
