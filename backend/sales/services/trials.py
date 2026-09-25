"""Sales-side trials (Phase 3): tracking of a trial period offered to a lead.

THIS IS NOT THE PLATFORM'S SUBSCRIPTION / TRIAL SYSTEM. Nothing here creates, extends or ends a company's access to
JAZ; it only records that Sales offered a trial, when it started, when it is expected to end, and how it ended.

Lifecycle: `active` -> `completed` | `cancelled`, both terminal; `actual_end_at` is set exactly when the trial stops
being active (a cancelled trial ended early, and that is when). A lead has AT MOST ONE ACTIVE trial: starting another
answers 409, and the database's partial unique index is the guarantee behind that answer (two racing requests cannot
both win). Trials carry no assignee - who works the trial is who works the lead - so any caller who may manage
trials on the lead may act on it.

Every action - start, update, complete, cancel - is recorded on the lead's timeline in the same transaction. Nothing
here moves the lead in the pipeline: after a trial starts the UI may OFFER the trial stage, but the change itself is
the ordinary, separately validated stage action.
"""
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import CLOCK_SKEW_TOLERANCE, TRIAL_OPEN
from sales.models import SalesTrial
from sales.repositories import trials as trials_repo
from sales.repositories.trials import TrialFilters, TrialRow
from sales.services import activity as activity_service
from sales.services import work
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from sales.services.lead_access import lead_visibility

logger = logging.getLogger("sales.trials")


def trial_out(row: TrialRow, ctx: StaffContext) -> dict:
    t = row.trial
    workable = ctx.has(perms.PERM_TRIALS_MANAGE) and t.status == TRIAL_OPEN and not row.lead.archived
    return {
        "id": str(t.id),
        "lead": work.lead_ref(row.lead),
        "started_at": t.started_at,
        "expected_end_at": t.expected_end_at,
        "actual_end_at": t.actual_end_at,
        "status": t.status,
        "notes": t.notes,
        "is_overdue": row.is_overdue,
        "created_by": work.user_ref(t.created_by, row.creator_name),
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "can": {"update": workable, "complete": workable, "cancel": workable},
    }


async def _one(db: AsyncSession, ctx: StaffContext, trial_id: uuid.UUID) -> dict:
    return trial_out(await trials_repo.get_row(db, trial_id), ctx)


def _not_active(trial: SalesTrial) -> HTTPException:
    return work.not_open("status", f"This trial is already {trial.status}", "trial_not_active")


def _active_exists() -> HTTPException:
    return field_error("lead", "This lead already has an active trial", 409, code="active_trial_exists")


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """A concurrent start can win the race the pre-check loses; the partial unique index is the real guarantee.
    Turn that into the clean 409 the pre-check would have produced, never a 500."""
    if "uq_sales_trials_one_active" in str(getattr(exc, "orig", exc)):
        return _active_exists()
    raise exc


def _check_end_after_start(expected_end_at, started_at) -> None:
    if expected_end_at <= started_at:
        raise field_error("expected_end_at", "The expected end must be after the start", 400, code="expected_end_before_start")


# ---- reads -------------------------------------------------------------------------------------------------

async def list_trials(
    db: AsyncSession, ctx: StaffContext, filters: TrialFilters, *, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    rows, total = await trials_repo.list_rows(
        db, visibility=lead_visibility(ctx), filters=filters, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return {"items": [trial_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def counts(db: AsyncSession, ctx: StaffContext, filters: TrialFilters, ending_within_days: int) -> dict:
    return await trials_repo.counts(db, visibility=lead_visibility(ctx), filters=filters, ending_within_days=ending_within_days)


async def get_trial(db: AsyncSession, ctx: StaffContext, trial_id: str) -> dict:
    return trial_out(await work.get_row_visible(db, ctx, trials_repo, trial_id, "Trial"), ctx)


# ---- writes ------------------------------------------------------------------------------------------------

async def start_trial(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    lead = await work.load_lead_for_new_work(db, ctx, body.lead_id)
    started_at = body.started_at or work.now()
    work.reject_future("started_at", started_at, tolerance=CLOCK_SKEW_TOLERANCE)
    _check_end_after_start(body.expected_end_at, started_at)
    if await trials_repo.get_active_for_lead(db, lead.id) is not None:
        raise _active_exists()

    trial = SalesTrial(
        id=uuid.uuid4(), lead_id=lead.id, started_at=started_at, expected_end_at=body.expected_end_at, notes=body.notes,
        status=TRIAL_OPEN, created_by=ctx.user_id,
    )
    try:
        async with db.begin_nested():  # savepoint: a lost race on the unique index must not poison the outer transaction
            db.add(trial)
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_TRIAL_STARTED,
        after={"started_at": trial.started_at, "expected_end_at": trial.expected_end_at},
        note=work.clip(trial.notes), metadata={"trial_id": str(trial.id)},
    )
    logger.info("trial_started actor=%s trial=%s lead=%s", ctx.user_id, trial.id, lead.id)
    return await _one(db, ctx, trial.id)


async def update_trial(db: AsyncSession, ctx: StaffContext, trial_id: str, body, audit: AuditContext) -> dict:
    trial, lead = await work.load_item_for_update(db, ctx, trials_repo, trial_id, "Trial")

    values = body.model_dump(exclude_unset=True)
    if not values:
        raise work.empty_update()
    work.not_clearable(values, ("expected_end_at",))
    if trial.status != TRIAL_OPEN:
        raise _not_active(trial)
    if values.get("expected_end_at") is not None:
        _check_end_after_start(values["expected_end_at"], trial.started_at)

    changes = work.changed_fields(trial, values)
    if not changes:
        return await _one(db, ctx, trial.id)      # nothing actually changes: no write, no event

    before = {field: getattr(trial, field) for field in changes}
    for field, value in changes.items():
        setattr(trial, field, value)
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_TRIAL_UPDATED,
        before=before, after={field: getattr(trial, field) for field in changes},
        metadata={"trial_id": str(trial.id), "fields": sorted(changes)},
    )
    logger.info("trial_updated actor=%s trial=%s fields=%s", ctx.user_id, trial.id, sorted(changes))
    return await _one(db, ctx, trial.id)


async def _end_trial(
    db: AsyncSession, ctx: StaffContext, trial_id: str, note, audit: AuditContext, *, status: str, event: str,
    actual_end_at=None,
) -> dict:
    """complete / cancel: the one open status -> a terminal status. `actual_end_at` is when the trial really ended
    (default: now - but never before it started, which a start dated a few minutes ahead of a slow clock could
    otherwise cause)."""
    trial, lead = await work.load_item_for_update(db, ctx, trials_repo, trial_id, "Trial")
    if trial.status != TRIAL_OPEN:
        raise _not_active(trial)

    if actual_end_at is not None:
        work.reject_future("actual_end_at", actual_end_at, tolerance=CLOCK_SKEW_TOLERANCE)
        if actual_end_at < trial.started_at:
            raise field_error("actual_end_at", "The end cannot be before the start", 400, code="actual_end_before_start")
        ended_at = actual_end_at
    else:
        ended_at = max(work.now(), trial.started_at)

    expected_end_at = trial.expected_end_at
    trial.status = status
    trial.actual_end_at = ended_at
    await db.flush()

    await activity_service.record(
        db, ctx, audit, lead.id, event,
        before={"status": TRIAL_OPEN, "expected_end_at": expected_end_at},
        after={"status": status, "actual_end_at": trial.actual_end_at},
        note=work.clip(note), metadata={"trial_id": str(trial.id)},
    )
    logger.info("%s actor=%s trial=%s lead=%s", event, ctx.user_id, trial.id, lead.id)
    return await _one(db, ctx, trial.id)


async def complete_trial(db, ctx, trial_id, body, audit) -> dict:
    return await _end_trial(
        db, ctx, trial_id, body.note if body else None, audit, status="completed",
        event=activity_service.EVENT_TRIAL_COMPLETED, actual_end_at=body.actual_end_at if body else None,
    )


async def cancel_trial(db, ctx, trial_id, note, audit) -> dict:
    return await _end_trial(db, ctx, trial_id, note, audit, status="cancelled", event=activity_service.EVENT_TRIAL_CANCELLED)
