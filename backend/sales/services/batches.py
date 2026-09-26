"""Batches (migration f2b6d8a1c4e9): the WRITE side - Data Batches, Master Batches, lead attempts, Sales Work Batches, reassignment.

Everything here happens INSIDE an existing lead operation's transaction (lead creation, a stage change, the Customer Setup, an
assignment ...) or inside the Sales Manager's reassignment, and never changes what that operation does for the person doing it:
batches are an internal Sales Manager layer. Nothing in this module is reachable by a Data Entry or Sales Employee route except
through those operations, and nothing it writes appears in any lead, queue or timeline response (no batch id, no batch number).

DATA BATCHES. Each lead entered by Data Entry - a caller holding the intake scope without the all-leads scope (permissions, never
role names) - takes the next slot of THE open Data Batch; the 100th slot fills it, and every 10 full batches (oldest first) form a
Master Batch. The open batch's row lock serialises the slots, so a batch never holds more or fewer than its slots. (Batch NUMBERS -
the `seq` identity - are unique and increasing but may skip a value: PostgreSQL consumes an identity value for an insert that loses
a race or is rolled back.) A lead is handed out to Sales the moment it is created, exactly as before: the batch is bookkeeping,
nobody waits for it to fill.

LEAD ATTEMPTS. A salesperson's decision on a lead is one attempt row:
    accepted   the lead is won (the Customer Setup's "Agreed" - the only way a lead is won)
    rejected   the lead is lost (the "Not Interested" dialog, or a manual loss)
    wait_list  the lead goes on the wait list: a timestamp and a pending status, nothing else
A wait-listed attempt stays PENDING until the lead is won (wait_list -> accepted) or lost (wait_list -> rejected), or leaves the
salesperson (reassigned, unassigned or archived: `released`). The attempt belongs to the lead's owner at the time (`employee_id`);
who pressed the button is `recorded_by`.

WORK BATCHES. When a salesperson has attempts on 100 distinct leads that are in none of THEIR batches yet (a lead they decide again
after it entered one of their batches never counts twice), those leads (oldest first) form a Work Batch - its first ATTEMPT
(`initial`), with the leads as members. A batch attempt is OPEN while any of its leads is outstanding (pending on the wait list, or -
on a reassignment - still waiting for the new salesperson) and CLOSED once each has a final result. Its statistics count each lead
ONCE, by its latest attempt in that batch attempt (a lead decided, reopened and decided again shows its final result).

REASSIGNMENT. The Sales Manager hands a CLOSED Work Batch to another salesperson: a new attempt of the same batch over the SAME
lead rows (membership, never copies). Leads already won or converted keep their result; the others (lost, released, reopened) go
to the new salesperson, lost ones reopened, each with its ordinary timeline events. Somebody else's pending wait list on a moved lead
ends (`released`); the new salesperson's OWN pending wait list on it is kept and joins the new attempt (a lead already waiting on
them inside another open batch attempt stays there). Every earlier attempt stays exactly as it was.

LOCKING (every lock is taken in one global order: lead rows by id, then open batch attempts by id, then their Work Batch rows):
  * the lead row lock (taken first by every lead operation) serialises everything about one lead;
  * an operation that touches several leads (bulk assignment, reassignment) - or one lead that belongs to several open batch
    attempts - locks those open batch attempts up front, in id order, before resolving anything (lock_open_attempts_of);
  * a batch attempt's row lock serialises the "is it complete now?" check, so of two concurrent last decisions one always sees the
    other;
  * FORMING a Work Batch never happens inside the decision's transaction. The decision only notes the salesperson whose pool grew;
    settle() - called by the route once the service has returned - commits the decision and then forms any due batch in a short
    transaction of its own that holds NO lead lock: a blocking per-salesperson advisory lock (two formations for one person run one
    after the other, the second sees the first's batch), then the pool leads' rows FOR KEY SHARE in id order (the same order bulk
    operations lock them in), so it can neither miss a batch nor deadlock. A formation that fails is logged and retried by the
    manager's overview (form_all_due_work_batches) - the salesperson's committed decision is never affected.
"""
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from typing import Iterable, Set

from sqlalchemy import and_, exists, func, insert, not_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales import permissions as perms
from sales.constants import (
    DATA_BATCH_SIZE,
    MASTER_BATCH_SIZE,
    STAGE_ASSIGNED,
    STAGE_LOST,
    STAGE_NEW,
    STAGE_WON,
    WORK_BATCH_SIZE,
)
from sales.models import (
    SalesBatchAttempt,
    SalesBatchAttemptLead,
    SalesCustomer,
    SalesDataBatch,
    SalesLead,
    SalesLeadAttempt,
    SalesMasterBatch,
    SalesWorkBatch,
)
from sales.repositories import leads as leads_repo
from sales.services import activity as activity_service
from sales.services import audit as audit_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from services.admin import parse_uuid

logger = logging.getLogger("sales.batches")

OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_WAIT_LIST = "wait_list"
RESULT_RELEASED = "released"

_DATA = SalesDataBatch.__table__
_MASTER = SalesMasterBatch.__table__
_WORK = SalesWorkBatch.__table__
_BATTEMPT = SalesBatchAttempt.__table__
_MEMBER = SalesBatchAttemptLead.__table__
_ATTEMPT = SalesLeadAttempt.__table__


async def _now(db: AsyncSession) -> datetime:
    """One database-clock instant, so the timestamps a single decision writes are identical."""
    return (await db.execute(select(func.clock_timestamp()))).scalar_one()


# =============================================================================
# Data Batches and Master Batches
# =============================================================================

def enters_data_batch(ctx: StaffContext) -> bool:
    """Whether a lead this caller creates is Data Entry's work: the intake scope without the all-leads scope (the same test as the
    Data Entry edit window, lead_access.needs_recent_leads). A Sales Manager's, a Super Admin's or a salesperson's lead is not."""
    return ctx.has(perms.PERM_LEADS_SCOPE_INTAKE) and not ctx.has(perms.PERM_LEADS_SCOPE_ALL)


async def take_data_batch_slot(db: AsyncSession) -> uuid.UUID:
    """The id of the Data Batch the lead being created enters, its count already incremented (the caller stores the id on the lead
    in the same transaction). The open batch stays locked until the transaction ends; filling it forms any due Master Batch."""
    for _ in range(5):
        row = (
            await db.execute(select(_DATA.c.id, _DATA.c.lead_count).where(_DATA.c.status == "open").with_for_update())
        ).first()
        if row is None:
            # none open (the very first lead, or the previous batch just filled): open the next one. Two creators racing here
            # insert at most one - the partial unique index admits a single open batch - and both then lock that one.
            await db.execute(
                pg_insert(_DATA).values(id=uuid.uuid4())
                .on_conflict_do_nothing(index_elements=["status"], index_where=text("status = 'open'"))
            )
            continue
        batch_id, count = row.id, row.lead_count + 1
        if count >= DATA_BATCH_SIZE:
            await db.execute(
                update(_DATA).where(_DATA.c.id == batch_id)
                .values(lead_count=DATA_BATCH_SIZE, status="full", filled_at=func.clock_timestamp())
            )
            await _form_master_batches(db)
            logger.info("data_batch_filled batch=%s", batch_id)
        else:
            await db.execute(update(_DATA).where(_DATA.c.id == batch_id).values(lead_count=count))
        return batch_id
    raise RuntimeError("Could not obtain the open Data Batch")     # unreachable in practice: each pass either locks or opens one


async def _form_master_batches(db: AsyncSession) -> None:
    """Every MASTER_BATCH_SIZE full Data Batches not yet in a Master Batch, oldest first, form one. Runs while the filling
    transaction holds the open batch's lock, so two transactions never form masters at once."""
    while True:
        ids = list((
            await db.execute(
                select(_DATA.c.id)
                .where(_DATA.c.status == "full", _DATA.c.master_batch_id.is_(None))
                .order_by(_DATA.c.seq).limit(MASTER_BATCH_SIZE).with_for_update()
            )
        ).scalars().all())
        if len(ids) < MASTER_BATCH_SIZE:
            return
        master_id = uuid.uuid4()
        await db.execute(insert(_MASTER).values(id=master_id, formed_at=func.clock_timestamp()))
        await db.execute(update(_DATA).where(_DATA.c.id.in_(ids)).values(master_batch_id=master_id))
        logger.info("master_batch_formed master=%s data_batches=%d", master_id, len(ids))


# =============================================================================
# lead attempts
# =============================================================================

async def pending_attempt(db: AsyncSession, lead_id: uuid.UUID, *, for_update: bool = False):
    stmt = select(_ATTEMPT).where(_ATTEMPT.c.lead_id == lead_id, _ATTEMPT.c.result.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).first()


async def _open_reassignment_for(db: AsyncSession, employee_id: uuid.UUID, lead_id: uuid.UUID) -> Optional[uuid.UUID]:
    """The OPEN reassignment attempt that gave this lead to this salesperson and has no attempt on it yet - the attempt a decision
    of theirs on it belongs to. None: the decision goes to their own pool (the next Work Batch)."""
    already = exists().where(_ATTEMPT.c.batch_attempt_id == _BATTEMPT.c.id, _ATTEMPT.c.lead_id == lead_id)
    return (
        await db.execute(
            select(_BATTEMPT.c.id)
            .join(_MEMBER, _MEMBER.c.batch_attempt_id == _BATTEMPT.c.id)
            .where(
                _BATTEMPT.c.status == "open", _BATTEMPT.c.kind == "reassignment", _BATTEMPT.c.employee_id == employee_id,
                _MEMBER.c.lead_id == lead_id, not_(already),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


_POOL_KEY = "sales_work_batch_pools"      # db.info: the salespeople whose pool grew in this transaction (see settle)


async def _new_attempt(db: AsyncSession, ctx: StaffContext, lead: SalesLead, first_outcome: str) -> None:
    """A new attempt of the lead's owner (the lead row is locked by the caller, which serialises attempt numbers)."""
    employee_id = lead.assigned_to
    now = await _now(db)
    attempt_no = (
        await db.execute(select(func.coalesce(func.max(_ATTEMPT.c.attempt_no), 0)).where(_ATTEMPT.c.lead_id == lead.id))
    ).scalar_one() + 1
    link = await _open_reassignment_for(db, employee_id, lead.id)
    waiting = first_outcome == OUTCOME_WAIT_LIST
    await db.execute(
        insert(_ATTEMPT).values(
            id=uuid.uuid4(), lead_id=lead.id, employee_id=employee_id, attempt_no=attempt_no, first_outcome=first_outcome,
            outcome_at=now, wait_listed_at=now if waiting else None,
            result=None if waiting else first_outcome, result_at=None if waiting else now,
            recorded_by=ctx.user_id, batch_attempt_id=link, created_at=now, updated_at=now,
        )
    )
    logger.info("lead_attempt lead=%s employee=%s no=%d outcome=%s batch_attempt=%s", lead.id, employee_id, attempt_no, first_outcome, link)
    if link is None:
        db.info.setdefault(_POOL_KEY, set()).add(employee_id)       # the pool grew: settle() forms the batch once this commits
    else:
        await _maybe_close(db, link)


async def _resolve(db: AsyncSession, attempt, result: str) -> None:
    now = await _now(db)
    await db.execute(update(_ATTEMPT).where(_ATTEMPT.c.id == attempt.id).values(result=result, result_at=now, updated_at=now))
    logger.info("lead_attempt_resolved lead=%s attempt=%s result=%s", attempt.lead_id, attempt.id, result)
    if attempt.batch_attempt_id is not None:
        await _maybe_close(db, attempt.batch_attempt_id)


async def wait_list(db: AsyncSession, ctx: StaffContext, audit: AuditContext, lead: SalesLead) -> bool:
    """Put the (locked, validated: assigned, open, not archived) lead on the wait list. False when it already is - the first
    timestamp stands."""
    if await pending_attempt(db, lead.id, for_update=True) is not None:
        return False
    owner = await db.get(User, lead.assigned_to)
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_WAIT_LISTED,
        before={"wait_list": False}, after={"wait_list": True},
        metadata={"owner": {"id": str(owner.id), "name": owner.name}} if owner is not None else None,
    )
    await _new_attempt(db, ctx, lead, OUTCOME_WAIT_LIST)
    return True


async def record_decision(db: AsyncSession, ctx: StaffContext, lead: SalesLead, outcome: str) -> None:
    """The (locked) lead was just won (`accepted`) or lost (`rejected`). Resolves its pending wait-list attempt, or records a
    direct attempt of its owner. A lead nobody owns is nobody's work: no attempt."""
    pending = await pending_attempt(db, lead.id, for_update=True)
    if pending is not None:
        await _resolve(db, pending, outcome)
    elif lead.assigned_to is not None:
        await _new_attempt(db, ctx, lead, outcome)


async def lock_open_attempts_of(db: AsyncSession, lead_ids: Iterable[uuid.UUID]) -> List[uuid.UUID]:
    """Lock - in id order - every OPEN batch attempt any of these (already locked) leads belongs to, and return their ids. A pending
    attempt's batch attempt is always one its lead is a member of, so every later completeness check of the operation re-takes a
    lock it already holds: the batch-attempt locks of a multi-lead operation are taken in one global order, never lead by lead."""
    ids = list(dict.fromkeys(lead_ids))
    if not ids:
        return []
    return list((
        await db.execute(
            select(_BATTEMPT.c.id)
            .where(_BATTEMPT.c.status == "open", _BATTEMPT.c.id.in_(select(_MEMBER.c.batch_attempt_id).where(_MEMBER.c.lead_id.in_(ids))))
            .order_by(_BATTEMPT.c.id).with_for_update()
        )
    ).scalars().all())


async def lead_left_owner(db: AsyncSession, lead_id: uuid.UUID) -> None:
    """The (locked) lead was reassigned, unassigned or archived: a pending wait-list attempt is `released` (it ended without a
    decision), and any open batch attempt waiting on the lead re-checks whether it is complete now."""
    open_attempts = await lock_open_attempts_of(db, [lead_id])
    pending = await pending_attempt(db, lead_id, for_update=True)
    if pending is not None:
        await _resolve(db, pending, RESULT_RELEASED)
    for batch_attempt_id in open_attempts:
        await _maybe_close(db, batch_attempt_id)


# =============================================================================
# Work Batches
# =============================================================================

def _outstanding(batch_attempt_id, employee_id):
    """EXISTS: a lead of the batch attempt that still has no final result in it - its attempt there is pending, or (a reassignment)
    it has none yet while it is still with that salesperson, open and not archived."""
    linked = and_(_ATTEMPT.c.batch_attempt_id == batch_attempt_id, _ATTEMPT.c.lead_id == _MEMBER.c.lead_id)
    return exists(
        select(1).select_from(_MEMBER.join(SalesLead.__table__, SalesLead.id == _MEMBER.c.lead_id))
        .where(
            _MEMBER.c.batch_attempt_id == batch_attempt_id,
            exists().where(linked, _ATTEMPT.c.result.is_(None))
            | (
                not_(exists().where(linked))
                & (SalesLead.assigned_to == employee_id)
                & SalesLead.archived_at.is_(None)
                & SalesLead.pipeline_stage.not_in([STAGE_WON, STAGE_LOST])
            ),
        )
    )


async def _maybe_close(db: AsyncSession, batch_attempt_id: uuid.UUID) -> None:
    """Close the batch attempt - and its Work Batch - when none of its leads is outstanding any more. Under the batch attempt's
    row lock: of two concurrent last decisions, the second always sees the first."""
    row = (
        await db.execute(
            select(_BATTEMPT.c.id, _BATTEMPT.c.work_batch_id, _BATTEMPT.c.employee_id)
            .where(_BATTEMPT.c.id == batch_attempt_id, _BATTEMPT.c.status == "open").with_for_update()
        )
    ).first()
    if row is None:
        return
    if (await db.execute(select(_outstanding(row.id, row.employee_id)))).scalar_one():
        return
    now = await _now(db)
    await db.execute(update(_BATTEMPT).where(_BATTEMPT.c.id == row.id).values(status="closed", closed_at=now))
    await db.execute(
        update(_WORK).where(_WORK.c.id == row.work_batch_id, _WORK.c.status == "open").values(status="closed", closed_at=now)
    )
    logger.info("work_batch_attempt_closed batch=%s attempt=%s", row.work_batch_id, row.id)


def _in_own_batch(employee_id):
    """EXISTS: the attempt's lead is already a member of one of this salesperson's batch attempts."""
    return exists(
        select(1).select_from(_MEMBER.join(_BATTEMPT, _BATTEMPT.c.id == _MEMBER.c.batch_attempt_id))
        .where(_MEMBER.c.lead_id == _ATTEMPT.c.lead_id, _BATTEMPT.c.employee_id == employee_id)
    )


async def _pool(db: AsyncSession, employee_id: uuid.UUID) -> List[uuid.UUID]:
    """The oldest WORK_BATCH_SIZE distinct leads of the salesperson's pool: leads they decided that are in none of their batches."""
    first_at = func.min(_ATTEMPT.c.outcome_at)
    return list((
        await db.execute(
            select(_ATTEMPT.c.lead_id)
            .where(_ATTEMPT.c.employee_id == employee_id, _ATTEMPT.c.batch_attempt_id.is_(None), not_(_in_own_batch(employee_id)))
            .group_by(_ATTEMPT.c.lead_id)
            .order_by(first_at, _ATTEMPT.c.lead_id)
            .limit(WORK_BATCH_SIZE)
        )
    ).scalars().all())


async def _form_one(db: AsyncSession, employee_id: uuid.UUID) -> bool:
    """Form ONE Work Batch from the salesperson's pool if it holds WORK_BATCH_SIZE leads. The caller holds their advisory lock."""
    lead_ids = await _pool(db, employee_id)
    if len(lead_ids) < WORK_BATCH_SIZE:
        return False
    # the members' rows, FOR KEY SHARE in id order - the order every multi-lead operation locks leads in - before anything references
    # them (the membership rows' foreign keys would otherwise take these locks one by one in batch order)
    await db.execute(select(SalesLead.id).where(SalesLead.id.in_(lead_ids)).order_by(SalesLead.id).with_for_update(key_share=True, read=True))
    batch_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(insert(_WORK).values(id=batch_id, employee_id=employee_id, lead_count=len(lead_ids), status="open"))
    await db.execute(
        insert(_BATTEMPT).values(
            id=attempt_id, work_batch_id=batch_id, attempt_no=1, employee_id=employee_id, kind="initial", status="open",
            lead_count=len(lead_ids), started_by=employee_id,
        )
    )
    await db.execute(
        insert(_MEMBER), [{"batch_attempt_id": attempt_id, "lead_id": lead_id, "ordinal": i} for i, lead_id in enumerate(lead_ids, 1)]
    )
    await db.execute(
        update(_ATTEMPT)
        .where(_ATTEMPT.c.employee_id == employee_id, _ATTEMPT.c.batch_attempt_id.is_(None), _ATTEMPT.c.lead_id.in_(lead_ids))
        .values(batch_attempt_id=attempt_id)
    )
    logger.info("work_batch_formed batch=%s employee=%s leads=%d", batch_id, employee_id, len(lead_ids))
    await _maybe_close(db, attempt_id)
    return True


async def form_due_work_batches(db: AsyncSession, employee_ids: Optional[Iterable[uuid.UUID]] = None) -> int:
    """Form every due Work Batch of these salespeople (default: those whose pool grew in this transaction). Takes each person's
    BLOCKING advisory lock (their formations run one after the other), and must be run holding no lead lock - see LOCKING."""
    ids: Set[uuid.UUID] = set(employee_ids) if employee_ids is not None else db.info.pop(_POOL_KEY, set())
    formed = 0
    for employee_id in sorted(ids, key=str):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"sales_work_batch:{employee_id}"})
        while await _form_one(db, employee_id):
            formed += 1
    return formed


async def settle(db: AsyncSession) -> None:
    """Called by a route once its service has returned: commits the decision, then forms any Work Batch it made due in a short
    transaction of its own (holding no lead lock). A failing formation is logged and rolled back - never the committed decision -
    and the manager's overview forms it later (form_all_due_work_batches)."""
    due = db.info.pop(_POOL_KEY, None)
    if not due:
        return
    await db.commit()
    try:
        await form_due_work_batches(db, due)
        await db.commit()
    except Exception:            # noqa: BLE001 - the decision is committed; the batch is formed on the next overview
        await db.rollback()
        logger.exception("work_batch_formation_deferred employees=%s", sorted(str(i) for i in due))


async def form_all_due_work_batches(db: AsyncSession) -> int:
    """The safety net: forms every Work Batch that is due for anybody (a formation that failed after its decision committed).
    Runs inside a savepoint, so a failure here never fails the read that triggered it."""
    lead_count = func.count(func.distinct(_ATTEMPT.c.lead_id))
    due = (
        await db.execute(
            select(_ATTEMPT.c.employee_id)
            .where(_ATTEMPT.c.batch_attempt_id.is_(None), not_(_in_own_batch(_ATTEMPT.c.employee_id)))
            .group_by(_ATTEMPT.c.employee_id)
            .having(lead_count >= WORK_BATCH_SIZE)
        )
    ).scalars().all()
    if not due:
        return 0
    try:
        async with db.begin_nested():
            return await form_due_work_batches(db, due)
    except Exception:            # noqa: BLE001
        logger.exception("work_batch_catch_up_failed employees=%s", sorted(str(i) for i in due))
        return 0


# =============================================================================
# reassignment (Sales Manager)
# =============================================================================

def _person(user: Optional[User]) -> Optional[dict]:
    return {"id": str(user.id), "name": user.name} if user is not None else None


async def _batch_lead_ids(db: AsyncSession, work_batch_id: uuid.UUID) -> List[uuid.UUID]:
    """The batch's leads: the members of its initial attempt (every later attempt covers some of the same rows), in batch order."""
    return list((
        await db.execute(
            select(_MEMBER.c.lead_id)
            .join(_BATTEMPT, _BATTEMPT.c.id == _MEMBER.c.batch_attempt_id)
            .where(_BATTEMPT.c.work_batch_id == work_batch_id, _BATTEMPT.c.attempt_no == 1)
            .order_by(_MEMBER.c.ordinal)
        )
    ).scalars().all())


async def reassign_work_batch(
    db: AsyncSession, ctx: StaffContext, audit: AuditContext, work_batch_id: str, employee_id: uuid.UUID
) -> uuid.UUID:
    """Hand a closed Work Batch to another salesperson. Returns the batch id (the router answers with its detail)."""
    parsed = parse_uuid(work_batch_id)
    found = (await db.execute(select(_WORK.c.id).where(_WORK.c.id == parsed))).first() if parsed else None
    if found is None:
        raise field_error("work_batch", "Work batch not found", 404, code="work_batch_not_found")

    # LOCK ORDER: the leads (id order, like every bulk lead operation) FIRST, then the batch - the order a lead decision takes them
    # in (lead, then its batch attempt, then the batch) - so a reassignment and a decision can never wait on each other's locks.
    lead_ids = await _batch_lead_ids(db, parsed)
    leads = await leads_repo.get_leads_for_update(db, lead_ids)
    batch = (await db.execute(select(_WORK).where(_WORK.c.id == parsed).with_for_update())).first()   # re-read under its lock
    if batch.status != "closed":
        raise field_error(
            "work_batch", "Only a completed batch can be reassigned - some of its leads are still on the wait list or being worked.",
            409, code="work_batch_open",
        )
    latest = (
        await db.execute(select(_BATTEMPT).where(_BATTEMPT.c.work_batch_id == batch.id).order_by(_BATTEMPT.c.attempt_no.desc()).limit(1))
    ).first()

    assignee = await leads_repo.get_assignable_user(db, employee_id, lock=True)      # the new owner's account: shared lock, last
    if assignee is None:
        raise field_error("employee_id", "This person cannot receive lead assignments", 400, code="assignee_not_eligible")
    if assignee.id == latest.employee_id:
        raise field_error("employee_id", "The batch is already with this person. Choose another Sales Employee.", 409, code="same_employee")

    # every open batch attempt these leads belong to, locked now in id order (LOCKING): the loop below may end wait lists in them
    other_open = await lock_open_attempts_of(db, lead_ids)

    converted = set((await db.execute(select(SalesCustomer.lead_id).where(SalesCustomer.lead_id.in_(lead_ids)))).scalars().all())
    ordinal = {lead_id: i for i, lead_id in enumerate(lead_ids, 1)}
    kept = {"accepted": 0, "archived": 0, "waiting_elsewhere": 0}
    movable: List[SalesLead] = []
    pending_of = {}
    for lead in leads:
        pending = await pending_attempt(db, lead.id, for_update=True)
        if lead.archived_at is not None:
            kept["archived"] += 1
        elif lead.pipeline_stage == STAGE_WON or lead.id in converted:
            kept["accepted"] += 1
        elif pending is not None and pending.employee_id == assignee.id and pending.batch_attempt_id is not None:
            # already waiting on the new salesperson inside another open batch attempt: it stays there (and is decided there)
            kept["waiting_elsewhere"] += 1
        else:
            movable.append(lead)
            pending_of[lead.id] = pending
    if not movable:
        raise field_error(
            "work_batch", "Every lead of this batch is already won or archived - there is nothing left to work.", 409,
            code="nothing_to_reassign",
        )

    attempt_id = uuid.uuid4()
    attempt_no = latest.attempt_no + 1
    await db.execute(
        insert(_BATTEMPT).values(
            id=attempt_id, work_batch_id=batch.id, attempt_no=attempt_no, employee_id=assignee.id, kind="reassignment",
            status="open", lead_count=len(movable), started_by=ctx.user_id,
        )
    )
    await db.execute(
        insert(_MEMBER), [{"batch_attempt_id": attempt_id, "lead_id": lead.id, "ordinal": ordinal[lead.id]} for lead in movable]
    )
    await db.execute(update(_WORK).where(_WORK.c.id == batch.id).values(status="open", closed_at=None))

    cause = {"automatic": True, "cause": "reassignment"}
    for lead in movable:                                            # id order, the order they were locked in
        pending = pending_of[lead.id]
        if pending is not None and pending.employee_id == assignee.id:
            # the new salesperson's OWN wait list on it (in their pool): it is kept, and continues in the new attempt
            await db.execute(update(_ATTEMPT).where(_ATTEMPT.c.id == pending.id).values(batch_attempt_id=attempt_id))
        elif pending is not None:                                    # somebody else's wait list: it ends here
            await _resolve(db, pending, RESULT_RELEASED)
        previous = await db.get(User, lead.assigned_to) if lead.assigned_to is not None else None
        old_stage, old_reason = lead.pipeline_stage, lead.lost_reason
        new_stage = STAGE_ASSIGNED if old_stage in (STAGE_LOST, STAGE_NEW) else old_stage
        lead.assigned_to = assignee.id
        lead.assigned_at = func.clock_timestamp()
        lead.pipeline_stage = new_stage
        if old_stage == STAGE_LOST:
            lead.lost_reason = None
            lead.closed_at = None
        await db.flush()
        if previous is None or previous.id != assignee.id:
            await activity_service.record(
                db, ctx, audit, lead.id,
                activity_service.EVENT_LEAD_REASSIGNED if previous is not None else activity_service.EVENT_LEAD_ASSIGNED,
                before={"assigned_to": _person(previous)}, after={"assigned_to": _person(assignee)}, metadata=cause,
            )
        if new_stage != old_stage:
            await activity_service.record(
                db, ctx, audit, lead.id, activity_service.EVENT_STAGE_CHANGED,
                before={"pipeline_stage": old_stage, "lost_reason": old_reason}, after={"pipeline_stage": new_stage, "lost_reason": None},
                metadata={**cause, **({"reopened": True} if old_stage == STAGE_LOST else {})},
            )

    # a moved lead may have been the last one another open reassignment attempt was waiting for (it no longer is with that person)
    for batch_attempt_id in other_open:
        await _maybe_close(db, batch_attempt_id)

    await audit_service.record(
        db, ctx, audit,
        action=audit_service.ACTION_WORK_BATCH_REASSIGNED, target_type=audit_service.TARGET_SALES_WORK_BATCH,
        target_id=batch.id, target_user_id=assignee.id,
        before={"attempt_no": latest.attempt_no, "employee_id": str(latest.employee_id)},
        after={"attempt_no": attempt_no, "employee_id": str(assignee.id), "lead_count": len(movable)},
        metadata={"work_batch_seq": batch.seq, "kept": kept},
    )
    logger.info(
        "work_batch_reassigned actor=%s batch=%s attempt=%d to=%s leads=%d kept=%s",
        ctx.user_id, batch.id, attempt_no, assignee.id, len(movable), kept,
    )
    return batch.id

