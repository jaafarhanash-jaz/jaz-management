"""JAZ Sales - batches, attempts, work sessions and their guarantees at the DATABASE / service level (migration f2b6d8a1c4e9).

The Data Batch is ONE global set of rows and the scratch database holds whatever other runs left in it, so these tests never assume
an empty start. They drive the real services (leads_service.create_lead, batches.take_data_batch_slot, batches.record_decision,
batches.wait_list, batches.reassign_work_batch, work_sessions.touch) inside a transaction that is NEVER committed and RELATIVE to
the state they find: "the open batch has c leads -> 100 - c more fill it -> the next lead opens a new one". Nothing of a test is
visible outside its own transaction. (The open batch's row lock is held for the test's duration, so another worker's Data Entry
lead creation waits the few seconds it takes - it is never blocked forever.)

Also here: what the database itself refuses (the 100-slot cap, a second open batch, a rewritten "who entered it", a pending attempt
that contradicts its result ...), and the downgrade guard of the migration.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from database import SessionLocal, engine
from models import User
from sales import permissions as P
from sales.constants import DATA_BATCH_SIZE, MASTER_BATCH_SIZE, SESSION_GAP, SESSION_TAIL_CREDIT, WORK_BATCH_SIZE
from sales.lead_schemas import LeadCreate
from sales.models import (
    SalesBatchAttempt,
    SalesBatchAttemptLead,
    SalesDataBatch,
    SalesLead,
    SalesLeadAttempt,
    SalesMasterBatch,
    SalesWorkBatch,
    SalesWorkSession,
    StaffAuditEvent,
    StaffRole,
    StaffRolePermission,
    StaffUserRole,
)
from sales.repositories import batches as batches_repo
from sales.repositories import leads as leads_repo
from sales.services import batch_views
from sales.services import batches as batches_service
from sales.services import leads as leads_service
from sales.services import work_sessions
from sales.services.access import StaffContext
from sales.services.audit import new_audit_context

RECEIVER_KEYS = (P.PERM_ACCESS, P.PERM_LEADS_VIEW, P.PERM_LEADS_CHANGE_STAGE, P.PERM_LEADS_SCOPE_ASSIGNED)
ENTRY_KEYS = (P.PERM_ACCESS, P.PERM_LEADS_VIEW, P.PERM_LEADS_CREATE, P.PERM_LEADS_SCOPE_INTAKE)
MANAGER_KEYS = (
    P.PERM_ACCESS, P.PERM_LEADS_VIEW, P.PERM_LEADS_CREATE, P.PERM_LEADS_ASSIGN, P.PERM_LEADS_CHANGE_STAGE, P.PERM_LEADS_SCOPE_ALL,
    P.PERM_BATCHES_VIEW, P.PERM_BATCHES_REASSIGN, P.PERM_PERFORMANCE_VIEW,
)


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _user(db) -> User:
    user = User(
        id=uuid.uuid4(), email=f"bdb-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1777{random.randint(100000000, 999999999)}",
        password="not-a-real-hash", name=f"Bdb {uuid.uuid4().hex[:6]}", role="jaz_staff", status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _role(db, keys) -> StaffRole:
    role = StaffRole(id=uuid.uuid4(), module="sales", key=f"t_{uuid.uuid4().hex[:10]}", name_en="t", name_ar="t")
    db.add(role)
    await db.flush()
    for key in keys:
        db.add(StaffRolePermission(role_id=role.id, permission_key=key))
    await db.flush()
    return role


def _ctx(user, role, keys) -> StaffContext:
    return StaffContext(
        {"id": str(user.id), "name": user.name, "role": "jaz_staff", "status": "active"}, user.id, False,
        ({"key": role.key, "name_en": "t", "name_ar": "t"},), frozenset(keys),
    )


class World:
    """One manager, one Data Entry person and receivers - all inside this test's own uncommitted transaction."""

    def __init__(self, db):
        self.db = db
        self.audit = new_audit_context("pytest")

    async def build(self, receivers: int = 2):
        db = self.db
        self.receiver_role = await _role(db, RECEIVER_KEYS)
        self.receivers = []
        for _ in range(receivers):
            user = await _user(db)
            db.add(StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=self.receiver_role.id, granted_by=user.id))
            self.receivers.append(user)
        await db.flush()
        self.receiver_ctxs = {u.id: _ctx(u, self.receiver_role, RECEIVER_KEYS) for u in self.receivers}
        manager_role = await _role(db, MANAGER_KEYS)
        self.manager = await _user(db)
        db.add(StaffUserRole(id=uuid.uuid4(), user_id=self.manager.id, role_id=manager_role.id, granted_by=self.manager.id))
        self.manager_ctx = _ctx(self.manager, manager_role, MANAGER_KEYS)
        entry_role = await _role(db, ENTRY_KEYS)
        self.entry = await _user(db)
        db.add(StaffUserRole(id=uuid.uuid4(), user_id=self.entry.id, role_id=entry_role.id, granted_by=self.entry.id))
        self.entry_ctx = _ctx(self.entry, entry_role, ENTRY_KEYS)
        await db.flush()
        return self

    async def entry_lead(self, ctx=None, **fields):
        values = {"business_name": f"Bdb Lead {uuid.uuid4().hex[:10]}", "phone": f"+1{random.randint(2000000000, 9999999999)}"}
        values.update(fields)
        return await leads_service.create_lead(self.db, ctx or self.entry_ctx, LeadCreate(**values), self.audit)

    async def raw_lead(self, owner=None, **fields) -> SalesLead:
        """A lead row without going through create_lead (no duplicate check, no timeline): fast bulk material."""
        lead = SalesLead(id=uuid.uuid4(), created_by=self.manager.id, pipeline_stage="assigned" if owner else "new")
        leads_repo.apply_fields(lead, {"business_name": f"Bulk {uuid.uuid4().hex[:12]}", **fields})
        if owner is not None:
            lead.assigned_to = owner.id
            lead.assigned_at = func.clock_timestamp()
        self.db.add(lead)
        await self.db.flush()
        return lead

    async def decide(self, lead: SalesLead, outcome: str, ctx=None):
        """The owner's decision the way change_stage records it: the stage moves, then the attempt."""
        ctx = ctx or self.receiver_ctxs[lead.assigned_to]
        lead.pipeline_stage = "won" if outcome == "accepted" else "lost"
        lead.lost_reason = None if outcome == "accepted" else "not_interested"           # a lost lead always carries its reason (CHECK)
        lead.closed_at = func.clock_timestamp()
        await self.db.flush()
        await batches_service.record_decision(self.db, ctx, lead, outcome)
        await batches_service.form_due_work_batches(self.db)      # what settle() does once the decision has committed

    async def wait(self, lead: SalesLead, ctx=None) -> bool:
        changed = await batches_service.wait_list(self.db, ctx or self.receiver_ctxs[lead.assigned_to], self.audit, lead)
        await batches_service.form_due_work_batches(self.db)
        return changed


def _in_world(fn, receivers: int = 2):
    async def _go():
        async with SessionLocal() as db:
            try:
                world = await World(db).build(receivers)
                return await fn(world)
            finally:
                await db.rollback()          # NOTHING of this test is ever committed
    return run(_go())


async def _open_batch(db):
    return (await db.execute(select(SalesDataBatch).where(SalesDataBatch.status == "open").with_for_update())).scalar_one_or_none()


# =============================================================================
# Data Batches: exactly 100, by every Data Entry person combined
# =============================================================================
class TestDataBatchBoundaries:
    def test_the_hundredth_lead_fills_the_batch_and_the_next_opens_another(self):
        async def check(world):
            opened = await _open_batch(world.db)
            existing = opened.lead_count if opened else 0
            if opened is None:
                first = await world.entry_lead()
                opened = await _open_batch(world.db)
                existing = 1
            open_id = opened.id
            needed = DATA_BATCH_SIZE - existing
            leads = [await world.entry_lead() for _ in range(needed - 1)]             # one slot short of full
            batch = (await world.db.execute(select(SalesDataBatch).where(SalesDataBatch.id == open_id))).scalar_one()
            await world.db.refresh(batch)
            assert batch.status == "open" and batch.lead_count == DATA_BATCH_SIZE - 1 and batch.filled_at is None
            last = await world.entry_lead()                                            # the 100th
            await world.db.refresh(batch)
            assert batch.status == "full" and batch.lead_count == DATA_BATCH_SIZE and batch.filled_at is not None
            # the next lead opens a NEW batch - nothing is over-filled, nothing is skipped
            nxt = await world.entry_lead()
            fresh = await _open_batch(world.db)
            assert fresh.id != batch.id and fresh.lead_count == 1 and fresh.seq > batch.seq
            in_batch = (await world.db.execute(select(func.count()).select_from(SalesLead).where(SalesLead.data_batch_id == batch.id))).scalar_one()
            assert in_batch == DATA_BATCH_SIZE
            ids = {uuid.UUID(l["id"]) for l in leads} | {uuid.UUID(last["id"])}
            stored = (await world.db.execute(select(SalesLead.id, SalesLead.data_batch_id).where(SalesLead.id.in_(ids | {uuid.UUID(nxt["id"])})))).all()
            assert {b for i, b in stored if i in ids} == {batch.id} and {b for i, b in stored if i == uuid.UUID(nxt["id"])} == {fresh.id}
        _in_world(check)

    def test_two_data_entry_people_fill_the_same_batch_together(self):
        async def check(world):
            other_role = await _role(world.db, ENTRY_KEYS)
            other = await _user(world.db)
            world.db.add(StaffUserRole(id=uuid.uuid4(), user_id=other.id, role_id=other_role.id, granted_by=other.id))
            other_ctx = _ctx(other, other_role, ENTRY_KEYS)
            a = [await world.entry_lead() for _ in range(3)]
            b = [await world.entry_lead(other_ctx) for _ in range(3)]
            rows = (await world.db.execute(select(SalesLead.id, SalesLead.data_batch_id, SalesLead.created_by).where(SalesLead.id.in_([uuid.UUID(l["id"]) for l in a + b])))).all()
            batches = {r.data_batch_id for r in rows}
            assert len(batches) <= 2                                                     # one batch (two only if the third slot filled it)
            assert {r.created_by for r in rows} == {world.entry.id, other.id}            # each lead still records ITS person
            assert None not in batches
        _in_world(check)

    def test_only_data_entry_leads_take_a_slot(self):
        async def check(world):
            opened = await _open_batch(world.db)
            before = opened.lead_count if opened else 0
            manager_lead = await world.entry_lead(world.manager_ctx)                     # a manager (all-leads scope) is not Data Entry
            employee_lead = await leads_service.create_lead(                              # nor is anybody else
                world.db, _ctx(world.receivers[0], world.receiver_role, RECEIVER_KEYS + (P.PERM_LEADS_CREATE,)),
                LeadCreate(business_name=f"Emp {uuid.uuid4().hex[:8]}", phone=f"+1{random.randint(2000000000, 9999999999)}"), world.audit,
            )
            stored = (await world.db.execute(select(SalesLead.data_batch_id).where(SalesLead.id.in_([uuid.UUID(manager_lead["id"]), uuid.UUID(employee_lead["id"])])))).scalars().all()
            assert stored == [None, None]
            opened = await _open_batch(world.db)
            assert (opened.lead_count if opened else 0) == before
            entry_lead = await world.entry_lead()
            assert (await world.db.execute(select(SalesLead.data_batch_id).where(SalesLead.id == uuid.UUID(entry_lead["id"])))).scalar_one() is not None
        _in_world(check)

    def test_a_lead_that_fails_to_be_created_takes_no_slot(self):
        async def check(world):
            await world.entry_lead()
            opened = await _open_batch(world.db)
            before = opened.lead_count
            with pytest.raises(HTTPException):
                await world.entry_lead(business_name="!!!")                              # rejected before any write
            after = (await world.db.execute(select(SalesDataBatch.lead_count).where(SalesDataBatch.id == opened.id))).scalar_one()
            assert after == before
        _in_world(check)

    def test_enters_data_batch_is_decided_by_permissions_never_by_role_names(self):
        def ctx(*keys):
            return StaffContext({"id": str(uuid.uuid4())}, uuid.uuid4(), False, ({"key": "sales_manager", "name_en": "", "name_ar": ""},), frozenset(keys))
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_INTAKE)) is True
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_INTAKE, P.PERM_LEADS_SCOPE_ASSIGNED)) is True
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_INTAKE, P.PERM_LEADS_SCOPE_ALL)) is False
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_ALL)) is False
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_ASSIGNED)) is False
        assert batches_service.enters_data_batch(ctx()) is False                         # even a "sales_manager" named role holding nothing


class TestMasterBatches:
    def test_ten_full_data_batches_form_one_master_and_the_data_batches_stay(self):
        async def check(world):
            db = world.db
            masters_before = (await db.execute(select(func.count()).select_from(SalesMasterBatch))).scalar_one()
            unmastered_before = (await db.execute(
                select(SalesDataBatch.id).where(SalesDataBatch.status == "full", SalesDataBatch.master_batch_id.is_(None)).order_by(SalesDataBatch.seq)
            )).scalars().all()
            batches_before = (await db.execute(select(func.count()).select_from(SalesDataBatch))).scalar_one()
            need = MASTER_BATCH_SIZE - len(unmastered_before)              # full batches still missing for the next master
            opened = await _open_batch(db)
            fill_current = (DATA_BATCH_SIZE - opened.lead_count) if opened else DATA_BATCH_SIZE
            before_last = need - 1                                            # all but the LAST one: it is the one that completes the master
            slots = 0 if before_last == 0 else fill_current + (before_last - 1) * DATA_BATCH_SIZE
            for _ in range(slots):
                await batches_service.take_data_batch_slot(db)
            masters_now = (await db.execute(select(func.count()).select_from(SalesMasterBatch))).scalar_one()
            assert masters_now == masters_before                             # nine full batches: no master yet
            opened = await _open_batch(db)
            for _ in range(DATA_BATCH_SIZE - (opened.lead_count if opened else 0)):
                await batches_service.take_data_batch_slot(db)               # the tenth completes
            masters_now = (await db.execute(select(func.count()).select_from(SalesMasterBatch))).scalar_one()
            assert masters_now == masters_before + 1
            master = (await db.execute(select(SalesMasterBatch).order_by(SalesMasterBatch.seq.desc()).limit(1))).scalar_one()
            members = (await db.execute(select(SalesDataBatch).where(SalesDataBatch.master_batch_id == master.id).order_by(SalesDataBatch.seq))).scalars().all()
            assert len(members) == MASTER_BATCH_SIZE and all(b.status == "full" and b.lead_count == DATA_BATCH_SIZE for b in members)
            assert [b.id for b in members][:len(unmastered_before)] == unmastered_before          # the OLDEST unmastered ones, in order
            # the data batches underneath are intact (none removed, none changed but for the master reference)
            batches_now = (await db.execute(select(func.count()).select_from(SalesDataBatch))).scalar_one()
            assert batches_now >= batches_before + need - 1 and all(b.filled_at is not None for b in members)
            still_open = (await db.execute(select(func.count()).select_from(SalesDataBatch).where(SalesDataBatch.status == "open"))).scalar_one()
            assert still_open <= 1
        _in_world(check)

    def test_a_data_batch_belongs_to_at_most_one_master(self):
        async def check(world):
            rows = (await world.db.execute(select(SalesDataBatch.master_batch_id, func.count()).where(SalesDataBatch.master_batch_id.is_not(None)).group_by(SalesDataBatch.master_batch_id))).all()
            assert all(n == MASTER_BATCH_SIZE for _, n in rows)                  # every master on the database has exactly ten
        _in_world(check)


# =============================================================================
# what the database refuses
# =============================================================================
class TestDatabaseGuarantees:
    def test_a_second_open_data_batch_is_refused(self):
        async def check(world):
            if await _open_batch(world.db) is None:
                await batches_service.take_data_batch_slot(world.db)
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(text("INSERT INTO sales_data_batches (id) VALUES (:i)"), {"i": uuid.uuid4()})
        _in_world(check)

    def test_the_batch_check_constraints(self):
        async def check(world):
            for sql in (
                "INSERT INTO sales_data_batches (id, status, lead_count) VALUES (gen_random_uuid(), 'full', 99)",                      # full is 100
                "INSERT INTO sales_data_batches (id, status, lead_count, filled_at) VALUES (gen_random_uuid(), 'open', 3, now())",     # filled pair
                "INSERT INTO sales_data_batches (id, status, lead_count) VALUES (gen_random_uuid(), 'weird', 3)",
                "INSERT INTO sales_data_batches (id, status, lead_count) VALUES (gen_random_uuid(), 'full', 101)",
            ):
                with pytest.raises(IntegrityError):
                    async with world.db.begin_nested():
                        await world.db.execute(text(sql))
            master = uuid.uuid4()
            await world.db.execute(text("INSERT INTO sales_master_batches (id) VALUES (:m)"), {"m": master})
            with pytest.raises(IntegrityError):                                                      # only a FULL batch may sit in a master
                async with world.db.begin_nested():
                    await world.db.execute(text("INSERT INTO sales_data_batches (id, status, lead_count, master_batch_id) VALUES (gen_random_uuid(), 'open', 3, :m)"), {"m": master})
        _in_world(check)

    def test_who_entered_a_lead_and_which_batch_are_permanent(self):
        async def check(world):
            lead = await world.entry_lead()
            lead_id = uuid.UUID(lead["id"])
            batch_id = (await world.db.execute(select(SalesLead.data_batch_id).where(SalesLead.id == lead_id))).scalar_one()
            assert batch_id is not None
            for sql, params in (
                ("UPDATE sales_leads SET created_by = :u WHERE id = :l", {"u": world.manager.id, "l": lead_id}),
                ("UPDATE sales_leads SET data_batch_id = NULL WHERE id = :l", {"l": lead_id}),
                ("UPDATE sales_leads SET data_batch_id = :b WHERE id = :l", {"b": uuid.uuid4(), "l": lead_id}),
                ("UPDATE sales_leads SET created_at = created_at - interval '3 days' WHERE id = :l", {"l": lead_id}),
            ):
                with pytest.raises(DBAPIError):
                    async with world.db.begin_nested():
                        await world.db.execute(text(sql), params)
            # a data batch that holds leads cannot be deleted from under them
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(text("DELETE FROM sales_data_batches WHERE id = :b"), {"b": batch_id})
            # ordinary edits are untouched
            await world.db.execute(update(SalesLead).where(SalesLead.id == lead_id).values(notes="edited"))
            assert (await world.db.execute(select(SalesLead.notes).where(SalesLead.id == lead_id))).scalar_one() == "edited"
        _in_world(check)

    def test_the_backdating_override_is_explicit_and_only_for_created_at(self):
        async def check(world):
            lead = await world.entry_lead()
            lead_id = uuid.UUID(lead["id"])
            await world.db.execute(text("SELECT set_config('jaz.allow_lead_backdating', 'on', true)"))
            await world.db.execute(text("UPDATE sales_leads SET created_at = created_at - interval '3 days' WHERE id = :l"), {"l": lead_id})
            for sql in ("UPDATE sales_leads SET created_by = :u WHERE id = :l",):
                with pytest.raises(DBAPIError):                                                        # who entered it: never, override or not
                    async with world.db.begin_nested():
                        await world.db.execute(text(sql), {"u": world.manager.id, "l": lead_id})
        _in_world(check)

    def test_pre_batch_leads_simply_belong_to_no_batch(self):
        async def check(world):
            lead = await world.raw_lead()
            assert lead.data_batch_id is None
            await world.db.execute(update(SalesLead).where(SalesLead.id == lead.id).values(notes="x"))
        _in_world(check)

    def test_the_attempt_constraints(self):
        async def check(world):
            owner = world.receivers[0]
            lead = await world.raw_lead(owner)

            async def insert(**over):
                values = dict(id=uuid.uuid4(), lead_id=lead.id, employee_id=owner.id, attempt_no=1, first_outcome="accepted", outcome_at=func.now(),
                              result="accepted", result_at=func.now(), recorded_by=owner.id)
                values.update(over)
                async with world.db.begin_nested():
                    await world.db.execute(SalesLeadAttempt.__table__.insert().values(**values))

            for over in (
                {"first_outcome": "maybe"}, {"result": "pending"},
                {"first_outcome": "accepted", "result": "rejected"},                                    # a direct outcome IS the result
                {"first_outcome": "accepted", "result": None, "result_at": None},                       # ... and is never pending
                {"first_outcome": "accepted", "wait_listed_at": func.now()},                            # only the wait list has a wait stamp
                {"first_outcome": "wait_list", "result": None, "result_at": None},                      # a wait list needs its stamp
                {"first_outcome": "accepted", "result": "released"},                                    # released only leaves the wait list
                {"first_outcome": "wait_list", "wait_listed_at": func.now(), "result": "accepted", "result_at": None},   # result pair
                {"attempt_no": 0},
            ):
                with pytest.raises(IntegrityError):
                    await insert(**over)
            await insert(first_outcome="wait_list", wait_listed_at=func.now(), result=None, result_at=None)            # a valid pending wait
            with pytest.raises(IntegrityError):                                                          # ... one pending attempt per lead
                await insert(attempt_no=2, first_outcome="wait_list", wait_listed_at=func.now(), result=None, result_at=None)
            with pytest.raises(IntegrityError):                                                          # ... and unique numbering
                await insert(attempt_no=1)
        _in_world(check)

    def test_the_batch_attempt_constraints(self):
        async def check(world):
            owner = world.receivers[0]
            batch = uuid.uuid4()
            await world.db.execute(SalesWorkBatch.__table__.insert().values(id=batch, employee_id=owner.id, lead_count=1, status="open"))

            async def attempt(**over):
                values = dict(id=uuid.uuid4(), work_batch_id=batch, attempt_no=1, employee_id=owner.id, kind="initial", status="open", lead_count=1, started_by=owner.id)
                values.update(over)
                async with world.db.begin_nested():
                    await world.db.execute(SalesBatchAttempt.__table__.insert().values(**values))

            with pytest.raises(IntegrityError):
                await attempt(kind="reassignment")                        # attempt 1 is the initial one
            with pytest.raises(IntegrityError):
                await attempt(attempt_no=2, kind="initial")               # ... and only attempt 1
            with pytest.raises(IntegrityError):
                await attempt(status="closed")                            # closed needs its stamp
            await attempt()
            with pytest.raises(IntegrityError):
                await attempt(attempt_no=2, kind="reassignment")          # two OPEN attempts of one batch
            with pytest.raises(IntegrityError):
                await attempt(attempt_no=1)                                # duplicate number
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(SalesWorkBatch.__table__.insert().values(id=uuid.uuid4(), employee_id=owner.id, lead_count=0))
        _in_world(check)

    def test_history_rows_cannot_be_deleted_from_under_their_owners(self):
        async def check(world):
            owner = world.receivers[0]
            lead = await world.raw_lead(owner)
            await world.decide(lead, "rejected")
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(text("DELETE FROM sales_leads WHERE id = :l"), {"l": lead.id})         # RESTRICT: the attempt references it
        _in_world(check)


# =============================================================================
# Work Batches at the service level
# =============================================================================
async def _work_pool(world, owner, count):
    leads = [await world.raw_lead(owner) for _ in range(count)]
    return leads


class TestWorkBatchFormation:
    def test_the_hundredth_worked_lead_forms_the_batch_and_not_before(self):
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads[:-1]:
                await world.decide(lead, "rejected")
            none_yet = (await world.db.execute(select(func.count()).select_from(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert none_yet == 0
            await world.decide(leads[-1], "accepted")
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert batch.lead_count == WORK_BATCH_SIZE
            assert batch.status == "closed" and batch.closed_at is not None                   # every lead has a result: born closed
            attempt = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id))).scalar_one()
            assert (attempt.attempt_no, attempt.kind, attempt.status, attempt.lead_count, attempt.started_by) == (1, "initial", "closed", 100, owner.id)
            members = (await world.db.execute(select(SalesBatchAttemptLead.lead_id, SalesBatchAttemptLead.ordinal).where(SalesBatchAttemptLead.batch_attempt_id == attempt.id).order_by(SalesBatchAttemptLead.ordinal))).all()
            assert [m.ordinal for m in members] == list(range(1, 101)) and {m.lead_id for m in members} == {l.id for l in leads}
            linked = (await world.db.execute(select(func.count()).select_from(SalesLeadAttempt).where(SalesLeadAttempt.batch_attempt_id == attempt.id))).scalar_one()
            assert linked == 100
            # the 101st decision starts the next pool, no second batch
            extra = await world.raw_lead(owner)
            await world.decide(extra, "rejected")
            assert (await world.db.execute(select(func.count()).select_from(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one() == 1
        _in_world(check)

    def test_each_person_has_their_own_pool(self):
        async def check(world):
            a, b = world.receivers
            for lead in await _work_pool(world, a, 60):
                await world.decide(lead, "rejected")
            for lead in await _work_pool(world, b, 60):
                await world.decide(lead, "rejected")
            assert (await world.db.execute(select(func.count()).select_from(SalesWorkBatch).where(SalesWorkBatch.employee_id.in_([a.id, b.id])))).scalar_one() == 0
        _in_world(check)

    def test_the_wait_list_keeps_a_batch_open_until_every_lead_has_a_result(self):
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads[:90]:
                await world.decide(lead, "accepted")
            for lead in leads[90:]:
                assert await world.wait(lead) is True
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert batch.status == "open" and batch.closed_at is None
            for lead in leads[90:99]:
                await world.decide(lead, "rejected")
            await world.db.refresh(batch)
            assert batch.status == "open"                                                        # one still waits
            await world.decide(leads[99], "accepted")
            await world.db.refresh(batch)
            assert batch.status == "closed" and batch.closed_at is not None
            stats = (await batches_repo.batch_attempt_stats(world.db, [(await world.db.execute(select(SalesBatchAttempt.id).where(SalesBatchAttempt.work_batch_id == batch.id))).scalar_one()]))
            (only,) = stats.values()
            assert (only["accepted_direct"], only["wait_listed"], only["wait_accepted"], only["wait_rejected"], only["wait_pending"]) == (90, 10, 1, 9, 0)
            assert only["total"] == 100
        _in_world(check)

    def test_a_wait_listed_lead_that_leaves_its_owner_no_longer_holds_the_batch_open(self):
        async def check(world):
            owner, other = world.receivers
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads[:99]:
                await world.decide(lead, "rejected")
            await world.wait(leads[99])
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert batch.status == "open"
            leads[99].assigned_to = other.id                                                     # the manager reassigns it
            await world.db.flush()
            await batches_service.lead_left_owner(world.db, leads[99].id)
            await world.db.refresh(batch)
            assert batch.status == "closed"
            released = (await world.db.execute(select(SalesLeadAttempt).where(SalesLeadAttempt.lead_id == leads[99].id))).scalar_one()
            assert released.result == "released" and released.result_at is not None and released.first_outcome == "wait_list"
        _in_world(check)

    def test_an_unowned_lead_decision_is_nobodys_attempt(self):
        async def check(world):
            lead = await world.raw_lead()
            lead.pipeline_stage, lead.lost_reason, lead.closed_at = "lost", "other", func.clock_timestamp()
            await world.db.flush()
            await batches_service.record_decision(world.db, world.manager_ctx, lead, "rejected")
            assert (await world.db.execute(select(func.count()).select_from(SalesLeadAttempt).where(SalesLeadAttempt.lead_id == lead.id))).scalar_one() == 0
        _in_world(check)

    def test_a_pending_wait_is_resolved_not_duplicated(self):
        async def check(world):
            owner = world.receivers[0]
            lead = await world.raw_lead(owner)
            assert await world.wait(lead) is True
            assert await world.wait(lead) is False
            await world.decide(lead, "accepted")
            rows = (await world.db.execute(select(SalesLeadAttempt).where(SalesLeadAttempt.lead_id == lead.id))).scalars().all()
            assert len(rows) == 1 and (rows[0].first_outcome, rows[0].result) == ("wait_list", "accepted")
            assert rows[0].wait_listed_at is not None and rows[0].result_at >= rows[0].wait_listed_at
        _in_world(check)


class TestReassignment:
    async def _closed_batch(self, world, accepted=60):
        owner = world.receivers[0]
        leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
        for i, lead in enumerate(leads):
            await world.decide(lead, "accepted" if i < accepted else "rejected")
        batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
        assert batch.status == "closed"
        return owner, leads, batch

    def test_the_same_leads_are_reused_and_the_history_is_kept(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world)
            target = world.receivers[1]
            lead_rows_before = (await world.db.execute(select(func.count()).select_from(SalesLead))).scalar_one()
            attempts_before = (await world.db.execute(select(SalesLeadAttempt.id, SalesLeadAttempt.result, SalesLeadAttempt.employee_id).where(SalesLeadAttempt.employee_id == owner.id))).all()
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), target.id)
            # NO lead was created: the batch reuses the very same rows
            assert (await world.db.execute(select(func.count()).select_from(SalesLead))).scalar_one() == lead_rows_before
            # the first attempt's history is untouched
            assert set((await world.db.execute(select(SalesLeadAttempt.id, SalesLeadAttempt.result, SalesLeadAttempt.employee_id).where(SalesLeadAttempt.employee_id == owner.id))).all()) == set(attempts_before)
            attempts = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id).order_by(SalesBatchAttempt.attempt_no))).scalars().all()
            assert [(a.attempt_no, a.kind, a.employee_id, a.status) for a in attempts] == [(1, "initial", owner.id, "closed"), (2, "reassignment", target.id, "open")]
            assert attempts[1].started_by == world.manager.id and attempts[1].lead_count == 40
            await world.db.refresh(batch)
            assert batch.status == "open" and batch.closed_at is None
            members = {r for (r,) in (await world.db.execute(select(SalesBatchAttemptLead.lead_id).where(SalesBatchAttemptLead.batch_attempt_id == attempts[1].id))).all()}
            assert members == {l.id for l in leads[60:]}                                          # exactly the rejected ones, the SAME ids
            for lead in leads[60:]:
                await world.db.refresh(lead)
                assert lead.assigned_to == target.id and lead.pipeline_stage == "assigned" and lead.lost_reason is None and lead.closed_at is None
            for lead in leads[:60]:
                await world.db.refresh(lead)
                assert lead.assigned_to == owner.id and lead.pipeline_stage == "won"              # what was won stays with its winner
        _in_world(check)

    def test_the_new_owners_decisions_join_the_new_attempt_and_close_it(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world, accepted=90)
            target = world.receivers[1]
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), target.id)
            second = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id, SalesBatchAttempt.attempt_no == 2))).scalar_one()
            moved = leads[90:]
            for lead in moved[:-1]:
                await world.decide(lead, "accepted")
            await world.db.refresh(second)
            assert second.status == "open"
            await world.decide(moved[-1], "rejected")
            await world.db.refresh(second)
            await world.db.refresh(batch)
            assert second.status == "closed" and second.closed_at is not None and batch.status == "closed"
            linked = (await world.db.execute(select(SalesLeadAttempt).where(SalesLeadAttempt.batch_attempt_id == second.id))).scalars().all()
            assert len(linked) == 10 and {a.employee_id for a in linked} == {target.id} and {a.attempt_no for a in linked} == {2}
            # they did NOT go to the target's own next pool
            unbatched = (await world.db.execute(select(func.count()).select_from(SalesLeadAttempt).where(SalesLeadAttempt.employee_id == target.id, SalesLeadAttempt.batch_attempt_id.is_(None)))).scalar_one()
            assert unbatched == 0
        _in_world(check)

    def test_the_guards(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world)
            target = world.receivers[1]
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(uuid.uuid4()), target.id)
            assert e.value.status_code == 404
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, "garbage", target.id)
            assert e.value.status_code == 404
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), owner.id)          # the same person
            assert e.value.status_code == 409 and e.value.detail["code"] == "same_employee"
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), world.entry.id)      # cannot receive leads
            assert e.value.status_code == 400
            # deactivated people cannot receive it
            target.status = "inactive"
            await world.db.flush()
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), target.id)
            assert e.value.status_code == 400
            target.status = "active"
            await world.db.flush()
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), target.id)
            with pytest.raises(HTTPException) as e:                                                                                       # now open: no second hand-over
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), owner.id)
            assert e.value.status_code == 409 and e.value.detail["code"] == "work_batch_open"
        _in_world(check)

    def test_a_batch_with_nothing_left_to_work_cannot_be_reassigned(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world, accepted=100)
            with pytest.raises(HTTPException) as e:
                await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), world.receivers[1].id)
            assert e.value.status_code == 409 and e.value.detail["code"] == "nothing_to_reassign"
            attempts = (await world.db.execute(select(func.count()).select_from(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id))).scalar_one()
            assert attempts == 1
        _in_world(check)

    def test_an_archived_lead_does_not_move_and_a_customer_lead_stays(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world, accepted=50)
            leads[70].archived_at, leads[70].archived_by = datetime.now(timezone.utc), world.manager.id
            await world.db.flush()
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), world.receivers[1].id)
            second = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id, SalesBatchAttempt.attempt_no == 2))).scalar_one()
            assert second.lead_count == 49                                                           # 50 rejected, one of them archived
            await world.db.refresh(leads[70])
            assert leads[70].assigned_to == owner.id
            event = (await world.db.execute(select(StaffAuditEvent).where(StaffAuditEvent.target_id == batch.id))).scalars().all()
            assert [e.action for e in event] == ["work_batch_reassigned"] and event[0].event_metadata["kept"] == {"accepted": 50, "archived": 1, "waiting_elsewhere": 0}
        _in_world(check)

    def test_the_audit_event_names_who_gets_the_batch_and_never_a_secret(self):
        async def check(world):
            owner, leads, batch = await self._closed_batch(world)
            target = world.receivers[1]
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), target.id)
            (event,) = (await world.db.execute(select(StaffAuditEvent).where(StaffAuditEvent.target_id == batch.id))).scalars().all()
            assert (event.action, event.actor_user_id, event.target_user_id, event.target_type) == ("work_batch_reassigned", world.manager.id, target.id, "sales_work_batch")
            assert event.before_data == {"attempt_no": 1, "employee_id": str(owner.id)}
            assert event.after_data == {"attempt_no": 2, "employee_id": str(target.id), "lead_count": 40}
        _in_world(check)


# =============================================================================
# work sessions ("work hours")
# =============================================================================
class TestWorkSessions:
    def test_actions_close_together_extend_one_session_and_a_gap_opens_the_next(self):
        async def check(world):
            user = world.receivers[0]
            for _ in range(3):
                await work_sessions.touch(world.db, user.id)
            rows = (await world.db.execute(select(SalesWorkSession).where(SalesWorkSession.user_id == user.id))).scalars().all()
            assert len(rows) == 1 and rows[0].actions == 3 and rows[0].ended_at >= rows[0].started_at
            # a long silence: age the session by more than the gap - the next action opens a NEW session
            await world.db.execute(text("UPDATE sales_work_sessions SET started_at = started_at - CAST(:d AS interval), ended_at = ended_at - CAST(:d AS interval) WHERE user_id = :u"),
                                   {"d": SESSION_GAP + timedelta(minutes=1), "u": user.id})
            await work_sessions.touch(world.db, user.id)
            rows = (await world.db.execute(select(SalesWorkSession).where(SalesWorkSession.user_id == user.id).order_by(SalesWorkSession.started_at))).scalars().all()
            assert len(rows) == 2 and rows[0].actions == 3 and rows[1].actions == 1
            # just inside the gap: still the same session
            await world.db.execute(text("UPDATE sales_work_sessions SET started_at = now() - CAST(:d AS interval) - interval '1 second', ended_at = now() - CAST(:d AS interval) WHERE id = :i"), {"d": SESSION_GAP - timedelta(minutes=1), "i": rows[1].id})
            await work_sessions.touch(world.db, user.id)
            actions = (await world.db.execute(select(SalesWorkSession.actions).where(SalesWorkSession.user_id == user.id))).scalars().all()   # (columns, not the stale ORM rows)
            assert sorted(actions) == [2, 3]
        _in_world(check)

    def test_two_people_never_share_a_session(self):
        async def check(world):
            a, b = world.receivers
            await work_sessions.touch(world.db, a.id)
            await work_sessions.touch(world.db, b.id)
            counts = dict((await world.db.execute(select(SalesWorkSession.user_id, func.count()).where(SalesWorkSession.user_id.in_([a.id, b.id])).group_by(SalesWorkSession.user_id))).all())
            assert counts == {a.id: 1, b.id: 1}
        _in_world(check)

    def test_every_timeline_event_is_a_recorded_moment_of_work(self):
        async def check(world):
            lead = await world.entry_lead()                 # lead_created + (no assignment): at least one action by the Data Entry person
            n = (await world.db.execute(select(func.coalesce(func.sum(SalesWorkSession.actions), 0)).where(SalesWorkSession.user_id == world.entry.id))).scalar_one()
            assert n >= 1
        _in_world(check)

    def test_the_hours_of_a_period_are_the_clipped_sessions_plus_the_tail_credit(self):
        async def check(world):
            user = world.receivers[0]
            now = datetime.now(timezone.utc)
            # a session of exactly 10 minutes two hours ago; one of 20 minutes straddling the start of a window
            db = world.db
            for start, end in ((now - timedelta(hours=2), now - timedelta(hours=2) + timedelta(minutes=10)),
                               (now - timedelta(hours=5, minutes=10), now - timedelta(hours=4, minutes=50))):
                db.add(SalesWorkSession(id=uuid.uuid4(), user_id=user.id, started_at=start, ended_at=end, actions=2))
            await db.flush()
            everything = batches_repo.Period("all", None, None)
            last_three_hours = batches_repo.Period("window", now - timedelta(hours=3), now)
            straddle = batches_repo.Period("straddle", now - timedelta(hours=5), now - timedelta(hours=5) + timedelta(minutes=20))
            out = await batches_repo.work_seconds(db, [user.id], [everything, last_three_hours, straddle])
            tail = SESSION_TAIL_CREDIT.total_seconds()
            assert out[user.id]["all"] == pytest.approx(10 * 60 + tail + 20 * 60 + tail, abs=1)
            assert out[user.id]["window"] == pytest.approx(10 * 60 + tail, abs=1)                    # the older session is outside the window
            assert out[user.id]["straddle"] == pytest.approx(10 * 60 + tail, abs=1)                   # only the part inside [start, start + 20 min): 11 min
        _in_world(check)


# =============================================================================
# the manager's read side over real rows
# =============================================================================
class TestViewsOverRealRows:
    def test_a_batch_detail_shows_every_attempt_and_the_split(self):
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads[:40]:
                await world.decide(lead, "accepted")
            for lead in leads[40:70]:
                await world.decide(lead, "rejected")
            for lead in leads[70:]:
                await world.wait(lead)
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            detail = await batch_views.get_work_batch(world.db, world.manager_ctx, str(batch.id))
            assert detail["status"] == "open" and len(detail["leads"]) == 100 and len(detail["attempts"]) == 1
            s = detail["attempts"][0]["stats"]
            assert (s["accepted_direct"], s["rejected_direct"], s["wait_pending"], s["decided"]) == (40, 30, 30, 70)
            assert (s["pct_accepted_direct"], s["pct_rejected_direct"]) == (57.1, 42.9)
            assert detail["can_reassign"] is False and detail["original_employee"]["id"] == str(owner.id)
        _in_world(check)

    def test_the_trace_of_a_lead_that_entered_a_batch(self):
        async def check(world):
            created = await world.entry_lead()
            trace = await batch_views.trace(world.db, world.manager_ctx, created["id"])
            assert trace["data_batch"]["id"] and trace["entered_by"]["id"] == str(world.entry.id)
            assert trace["master_batch"] is None or trace["master_batch"]["id"]
            assert trace["work_batches"] == [] and trace["attempts"] == [] and trace["timeline_total"] >= 1
        _in_world(check)

    def test_a_lead_of_a_full_batch_is_traced_all_the_way_to_its_master_batch(self):
        """Lead -> Data Batch -> Master Batch, through the trace, the search and both batch listings (a whole Master Batch is built
        inside this never-committed transaction: ten full Data Batches under one master)."""
        async def check(world):
            db = world.db
            master = uuid.uuid4()
            await db.execute(SalesMasterBatch.__table__.insert().values(id=master))
            batch_ids = [uuid.uuid4() for _ in range(MASTER_BATCH_SIZE)]
            for batch_id in batch_ids:
                await db.execute(SalesDataBatch.__table__.insert().values(
                    id=batch_id, status="full", lead_count=DATA_BATCH_SIZE, filled_at=func.now(), master_batch_id=master))
            token = uuid.uuid4().hex[:10]
            lead = await world.raw_lead(None, business_name=f"Master Trace {token}", business_type=f"Bakery {token}")
            lead.data_batch_id = batch_ids[3]                                     # set once (NULL -> a batch): the trigger allows exactly that
            await db.flush()

            trace = await batch_views.trace(db, world.manager_ctx, str(lead.id))
            assert trace["data_batch"]["id"] == str(batch_ids[3]) and trace["data_batch"]["status"] == "full"
            assert trace["master_batch"]["id"] == str(master) and trace["entered_by"]["id"] == str(world.manager.id)
            found = await batch_views.search(db, q=token, field="business_type", limit=5)
            assert [i["id"] for i in found["items"]] == [str(lead.id)]
            assert found["items"][0]["master_batch"]["id"] == str(master) and found["items"][0]["data_batch"]["id"] == str(batch_ids[3])

            detail = await batch_views.get_master_batch(db, str(master))
            assert (detail["data_batch_count"], detail["lead_count"], len(detail["data_batches"])) == (MASTER_BATCH_SIZE, MASTER_BATCH_SIZE * DATA_BATCH_SIZE, MASTER_BATCH_SIZE)
            assert {b["id"] for b in detail["data_batches"]} == {str(b) for b in batch_ids} and all(b["master_batch"]["id"] == str(master) for b in detail["data_batches"])
            listed = await batch_views.list_data_batches(db, status="full", master_batch_id=str(master), limit=50, offset=0)
            assert listed["total"] == MASTER_BATCH_SIZE
            masters = await batch_views.list_master_batches(db, limit=5, offset=0)
            assert str(master) in {m["id"] for m in masters["items"]}
            data_detail = await batch_views.get_data_batch(db, str(batch_ids[3]))
            assert [l["id"] for l in data_detail["leads"]] == [str(lead.id)] and data_detail["master_batch"]["id"] == str(master)
        _in_world(check)

    def test_unknown_ids_are_404_at_the_service_too(self):
        async def check(world):
            for call in (batch_views.get_data_batch(world.db, "x"), batch_views.get_master_batch(world.db, str(uuid.uuid4())),
                         batch_views.get_work_batch(world.db, world.manager_ctx, str(uuid.uuid4())), batch_views.trace(world.db, world.manager_ctx, "nope"),
                         batch_views.employee_performance(world.db, str(uuid.uuid4())), batch_views.employee_history(world.db, "nope", days=3, sessions=3)):
                with pytest.raises(HTTPException) as e:
                    await call
                assert e.value.status_code == 404
        _in_world(check)

    def test_the_performance_people_are_found_by_permission_or_by_history(self):
        async def check(world):
            is_entry, is_sales = batches_repo.is_data_entry_person, batches_repo.is_sales_person
            assert await is_entry(world.db, world.entry.id) and not await is_sales(world.db, world.entry.id)
            assert await is_sales(world.db, world.receivers[0].id) and not await is_entry(world.db, world.receivers[0].id)
            assert not await is_entry(world.db, world.manager.id) and not await is_sales(world.db, world.manager.id)   # the all-leads scope is neither
            people, total = await batches_repo.data_entry_people(world.db, q=world.entry.name, limit=10)
            assert [u.id for u in people] == [world.entry.id] and total == 1
            # a person who LEFT (role retired) keeps showing while their history exists
            lead = await world.raw_lead(world.receivers[1])
            await world.decide(lead, "rejected")
            world.receiver_role.is_active = False
            await world.db.flush()
            assert await is_sales(world.db, world.receivers[1].id)
        _in_world(check)


# =============================================================================
# the migration file
# =============================================================================
class TestMigrationFile:
    from pathlib import Path
    SOURCE = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "f2b6d8a1c4e9_sales_batches_and_performance.py").read_text()

    def test_it_is_additive_and_chained_after_the_setup_permission(self):
        assert "revision: str = 'f2b6d8a1c4e9'" in self.SOURCE and "down_revision: Union[str, Sequence[str], None] = 'e7a2d4c9b1f3'" in self.SOURCE
        upgrade = self.SOURCE[self.SOURCE.index("def upgrade"):self.SOURCE.index("def downgrade")]
        for destructive in ("drop_table", "drop_column", "drop_index", "drop_constraint", "DELETE FROM", "TRUNCATE", "DROP TABLE", "UPDATE sales", "op.alter_column"):
            assert destructive not in upgrade, destructive
        assert upgrade.count("op.create_table(") == 7 and upgrade.count("op.add_column(") == 1        # seven new tables, ONE new nullable column

    def test_the_downgrade_refuses_while_any_history_exists(self):
        downgrade = self.SOURCE[self.SOURCE.index("def downgrade"):]
        assert "Refusing to downgrade" in downgrade and "RuntimeError" in downgrade
        for table in ("sales_work_sessions", "sales_lead_attempts", "sales_batch_attempt_leads", "sales_batch_attempts", "sales_work_batches", "sales_data_batches", "sales_master_batches"):
            assert table in self.SOURCE[self.SOURCE.index("_TABLES"):self.SOURCE.index("_DATA_STATUSES")] or table in downgrade
        assert "data_batch_id IS NOT NULL" in downgrade
        assert downgrade.index("Refusing to downgrade") < downgrade.index("op.drop_table(table)")       # the guard runs before anything is dropped

    def test_only_the_manager_is_granted_the_three_keys(self):
        assert "_GRANTS = [('sales_manager', k) for k, _ in _PERMISSIONS]" in self.SOURCE
        assert "sales_employee" not in self.SOURCE.split("_GRANTS")[1].split("\n")[0]
        assert self.SOURCE.count("'sales.batches.") == 2 and "'sales.performance.view'" in self.SOURCE

    def test_the_catalog_text_equals_the_code(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bmig", str(self.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "f2b6d8a1c4e9_sales_batches_and_performance.py"))
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        assert dict(mig._PERMISSIONS) == {k: P.ALL_PERMISSIONS[k] for k in (P.PERM_BATCHES_VIEW, P.PERM_BATCHES_REASSIGN, P.PERM_PERFORMANCE_VIEW)}

    def test_the_database_has_exactly_what_the_models_declare(self):
        from sales import models
        async def check():
            async with SessionLocal() as db:
                idx = {n for (n,) in (await db.execute(text(
                    "select indexname from pg_indexes where tablename in ('sales_master_batches','sales_data_batches','sales_work_batches','sales_batch_attempts',"
                    "'sales_batch_attempt_leads','sales_lead_attempts','sales_work_sessions') and indexname not like '%_pkey'"))).all()}
                cons = {n for (n,) in (await db.execute(text(
                    "select conname from pg_constraint where contype = 'c' and conrelid::regclass::text in ('sales_master_batches','sales_data_batches','sales_work_batches',"
                    "'sales_batch_attempts','sales_batch_attempt_leads','sales_lead_attempts','sales_work_sessions')"))).all()}
                trig = (await db.execute(text("select count(*) from pg_trigger where tgname = 'trg_sales_leads_entry_immutable' and not tgisinternal"))).scalar_one()
            return idx, cons, trig
        idx, cons, trig = run(check())
        classes = (models.SalesMasterBatch, models.SalesDataBatch, models.SalesWorkBatch, models.SalesBatchAttempt, models.SalesBatchAttemptLead,
                   models.SalesLeadAttempt, models.SalesWorkSession)
        uniques = {c.name for cls in classes for c in cls.__table__.constraints if c.__class__.__name__ == "UniqueConstraint" and c.name}
        assert {i.name for c in classes for i in c.__table__.indexes} | uniques == idx           # (a UNIQUE constraint is an index in the catalog)
        assert {c.name for cls in classes for c in cls.__table__.constraints if c.__class__.__name__ == "CheckConstraint"} == cons
        assert trig == 1


# =============================================================================
# regressions of the adversarial review (each test names the defect it pins)
# =============================================================================
class TestReviewRegressions:
    def test_formation_happens_after_the_decision_not_inside_it(self):
        """A decision only NOTES the pool (db.info); the batch forms in form_due_work_batches - what settle() runs after the commit."""
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads:
                lead.pipeline_stage, lead.lost_reason, lead.closed_at = "lost", "other", func.clock_timestamp()
                await world.db.flush()
                await batches_service.record_decision(world.db, world.receiver_ctxs[owner.id], lead, "rejected")
            count = lambda: world.db.execute(select(func.count()).select_from(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))
            assert (await count()).scalar_one() == 0 and owner.id in world.db.info[batches_service._POOL_KEY]
            assert await batches_service.form_due_work_batches(world.db) == 1
            assert (await count()).scalar_one() == 1 and batches_service._POOL_KEY not in world.db.info
            assert await batches_service.form_due_work_batches(world.db, [owner.id]) == 0                    # idempotent
        _in_world(check)

    def test_the_catch_up_forms_a_batch_whose_formation_was_lost(self):
        """settle() failing after the commit (or never running) leaves a due pool: the manager's overview forms it."""
        async def check(world):
            owner = world.receivers[0]
            for lead in await _work_pool(world, owner, WORK_BATCH_SIZE):
                lead.pipeline_stage, lead.lost_reason, lead.closed_at = "lost", "other", func.clock_timestamp()
                await world.db.flush()
                await batches_service.record_decision(world.db, world.receiver_ctxs[owner.id], lead, "rejected")
            world.db.info.pop(batches_service._POOL_KEY, None)                  # the post-commit step never ran
            await batch_views.overview(world.db)
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert batch.lead_count == WORK_BATCH_SIZE and batch.status == "closed"
        _in_world(check)

    def test_a_batch_counts_each_lead_once_by_its_latest_attempt(self):
        """Review #6/#12: a lead decided, reopened and decided again before its batch formed is ONE lead with its final result."""
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            twice = leads[0]
            await world.decide(twice, "rejected")
            twice.pipeline_stage, twice.lost_reason, twice.closed_at = "assigned", None, None              # reopened (still theirs)
            await world.db.flush()
            await world.decide(twice, "accepted")                                                     # decided again: accepted
            for lead in leads[1:]:
                await world.decide(lead, "rejected")
            attempt = (await world.db.execute(select(SalesBatchAttempt).join(SalesWorkBatch, SalesWorkBatch.id == SalesBatchAttempt.work_batch_id)
                                              .where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            linked = (await world.db.execute(select(func.count()).select_from(SalesLeadAttempt).where(SalesLeadAttempt.batch_attempt_id == attempt.id))).scalar_one()
            assert linked == 101                                                                  # the history keeps both tries ...
            (stats,) = (await batches_repo.batch_attempt_stats(world.db, [attempt.id])).values()
            assert stats["total"] == 100 and stats["accepted_direct"] == 1 and stats["rejected_direct"] == 99   # ... the split counts leads
            view = batch_views.stats_out(stats, lead_count=attempt.lead_count)
            assert view["decided"] == 100 and view["pct_accepted"] == 1.0 and view["not_started"] == 0
        _in_world(check)

    def test_a_lead_already_in_ones_batch_never_reenters_ones_pool(self):
        """Review #7: deciding again a lead that is already in one of your batches does not count toward your next 100."""
        async def check(world):
            owner = world.receivers[0]
            leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
            for lead in leads:
                await world.decide(lead, "rejected")
            again = leads[5]
            again.pipeline_stage, again.lost_reason, again.closed_at = "assigned", None, None
            await world.db.flush()
            await world.decide(again, "accepted")                                                 # a new, unbatched attempt ...
            assert again.id not in await batches_service._pool(world.db, owner.id)                # ... that is not pool material
            for lead in await _work_pool(world, owner, WORK_BATCH_SIZE - 1):
                await world.decide(lead, "rejected")
            batches = (await world.db.execute(select(func.count()).select_from(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            assert batches == 1                                                                   # 99 genuinely new leads: no second batch yet
            await world.decide(await world.raw_lead(owner), "rejected")
            batches = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id).order_by(SalesWorkBatch.seq))).scalars().all()
            assert len(batches) == 2
            second = (await world.db.execute(select(SalesBatchAttemptLead.lead_id).join(SalesBatchAttempt, SalesBatchAttempt.id == SalesBatchAttemptLead.batch_attempt_id)
                                             .where(SalesBatchAttempt.work_batch_id == batches[1].id))).scalars().all()
            assert again.id not in second and len(second) == WORK_BATCH_SIZE
        _in_world(check)

    def test_reassigning_keeps_the_new_owners_own_wait_list(self):
        """Review #0/#9: the batch goes to B, who already holds one of its (reopened) leads on THEIR wait list - it is kept and joins
        the new attempt, the badge stays, and B's decision closes the attempt."""
        async def check(world):
            a, b = world.receivers
            leads = await _work_pool(world, a, WORK_BATCH_SIZE)
            for lead in leads:
                await world.decide(lead, "rejected")
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == a.id))).scalar_one()
            held = leads[3]
            held.assigned_to, held.pipeline_stage, held.lost_reason, held.closed_at = b.id, "assigned", None, None
            await world.db.flush()
            assert await world.wait(held, world.receiver_ctxs[b.id]) is True
            pending = await batches_service.pending_attempt(world.db, held.id)
            assert pending.employee_id == b.id and pending.batch_attempt_id is None
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch.id), b.id)
            second = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch.id, SalesBatchAttempt.attempt_no == 2))).scalar_one()
            still = await batches_service.pending_attempt(world.db, held.id)
            assert still is not None and still.id == pending.id and still.result is None and still.batch_attempt_id == second.id   # kept, adopted
            row = await leads_repo.get_row(world.db, held.id)
            assert row.wait_listed_at is not None                                                 # the badge and queue position stay
            for lead in leads:
                if lead.id != held.id:
                    await world.decide(lead, "rejected", world.receiver_ctxs[b.id])
            await world.db.refresh(second)
            assert second.status == "open"                                                        # B's wait list still holds it open
            await world.decide(held, "accepted", world.receiver_ctxs[b.id])
            await world.db.refresh(second)
            assert second.status == "closed"
            (stats,) = (await batches_repo.batch_attempt_stats(world.db, [second.id])).values()
            assert stats["wait_accepted"] == 1 and stats["released"] == 0
        _in_world(check)

    def test_a_lead_waiting_on_the_new_owner_in_another_open_attempt_stays_there(self):
        async def check(world):
            a, b = world.receivers
            leads = await _work_pool(world, a, WORK_BATCH_SIZE)
            for lead in leads:
                await world.decide(lead, "rejected")
            batch_a = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == a.id))).scalar_one()
            shared = leads[7]
            shared.assigned_to, shared.pipeline_stage, shared.lost_reason, shared.closed_at = b.id, "assigned", None, None
            await world.db.flush()
            await world.wait(shared, world.receiver_ctxs[b.id])
            for lead in await _work_pool(world, b, WORK_BATCH_SIZE - 1):                            # B's own batch forms around it (open)
                await world.decide(lead, "rejected")
            pending = await batches_service.pending_attempt(world.db, shared.id)
            assert pending.batch_attempt_id is not None
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(batch_a.id), b.id)
            after = await batches_service.pending_attempt(world.db, shared.id)
            assert after.id == pending.id and after.batch_attempt_id == pending.batch_attempt_id    # untouched, still in B's own batch
            second = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == batch_a.id, SalesBatchAttempt.attempt_no == 2))).scalar_one()
            assert second.lead_count == WORK_BATCH_SIZE - 1
            members = (await world.db.execute(select(SalesBatchAttemptLead.lead_id).where(SalesBatchAttemptLead.batch_attempt_id == second.id))).scalars().all()
            assert shared.id not in members
            (event,) = (await world.db.execute(select(StaffAuditEvent).where(StaffAuditEvent.target_id == batch_a.id))).scalars().all()
            assert event.event_metadata["kept"] == {"accepted": 0, "archived": 0, "waiting_elsewhere": 1}
        _in_world(check)

    def test_moving_the_last_outstanding_lead_of_another_open_attempt_closes_it(self):
        """Review #4/#8/#11: a reassignment that takes away the lead another open reassignment attempt was still waiting for re-checks
        that attempt - it closes instead of staying open forever."""
        async def check(world):
            third = await _user(world.db)
            world.db.add(StaffUserRole(id=uuid.uuid4(), user_id=third.id, role_id=world.receiver_role.id, granted_by=third.id))
            await world.db.flush()
            world.receiver_ctxs[third.id] = _ctx(third, world.receiver_role, RECEIVER_KEYS)
            a, b = world.receivers
            # W1: A's 100 leads, all rejected (closed); W3: B's 100 leads - one of them (L) shared with W1 - all rejected (closed)
            leads_a = await _work_pool(world, a, WORK_BATCH_SIZE)
            for lead in leads_a:
                await world.decide(lead, "rejected")
            w1 = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == a.id))).scalar_one()
            shared = leads_a[0]
            shared.assigned_to, shared.pipeline_stage, shared.lost_reason, shared.closed_at = b.id, "assigned", None, None
            await world.db.flush()
            await world.decide(shared, "rejected", world.receiver_ctxs[b.id])
            for lead in await _work_pool(world, b, WORK_BATCH_SIZE - 1):
                await world.decide(lead, "rejected")
            w3 = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == b.id))).scalar_one()
            assert w1.status == "closed" and w3.status == "closed"
            # W3 goes to the third person, who decides everything but L: W3's attempt 2 is open only because of L
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(w3.id), third.id)
            ba4 = (await world.db.execute(select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id == w3.id, SalesBatchAttempt.attempt_no == 2))).scalar_one()
            members = (await world.db.execute(select(SalesLead).join(SalesBatchAttemptLead, SalesBatchAttemptLead.lead_id == SalesLead.id)
                                              .where(SalesBatchAttemptLead.batch_attempt_id == ba4.id))).scalars().all()
            for lead in members:
                if lead.id != shared.id:
                    await world.decide(lead, "rejected", world.receiver_ctxs[third.id])
            await world.db.refresh(ba4)
            assert ba4.status == "open"
            # W1 goes to B: L moves away from the third person - BA4 has nothing left to wait for and must close
            await batches_service.reassign_work_batch(world.db, world.manager_ctx, world.audit, str(w1.id), b.id)
            await world.db.refresh(ba4)
            await world.db.refresh(w3)
            assert ba4.status == "closed" and w3.status == "closed"
        _in_world(check)

    def test_the_open_attempts_of_many_leads_are_locked_in_id_order(self):
        async def check(world):
            a, b = world.receivers
            for owner in (a, b):
                leads = await _work_pool(world, owner, WORK_BATCH_SIZE)
                for lead in leads[:-1]:
                    await world.decide(lead, "rejected")
                await world.wait(leads[-1])                                                        # keeps each batch open
            open_ids = (await world.db.execute(select(SalesBatchAttempt.id).join(SalesWorkBatch, SalesWorkBatch.id == SalesBatchAttempt.work_batch_id)
                                               .where(SalesWorkBatch.employee_id.in_([a.id, b.id]), SalesBatchAttempt.status == "open"))).scalars().all()
            members = (await world.db.execute(select(SalesBatchAttemptLead.lead_id).where(SalesBatchAttemptLead.batch_attempt_id.in_(open_ids)))).scalars().all()
            locked = await batches_service.lock_open_attempts_of(world.db, reversed(members))
            assert locked == sorted(open_ids) and len(locked) == 2
            assert await batches_service.lock_open_attempts_of(world.db, []) == []
        _in_world(check)

    def test_can_reassign_only_when_a_lead_is_left_to_move(self):
        async def check(world):
            owner = world.receivers[0]
            for lead in await _work_pool(world, owner, WORK_BATCH_SIZE):
                await world.decide(lead, "accepted")
            batch = (await world.db.execute(select(SalesWorkBatch).where(SalesWorkBatch.employee_id == owner.id))).scalar_one()
            detail = await batch_views.get_work_batch(world.db, world.manager_ctx, str(batch.id))
            assert detail["status"] == "closed" and detail["can_reassign"] is False               # every lead won: nothing to hand on
        _in_world(check)

    def test_performance_reports_only_on_data_entry_and_sales_people(self):
        """Review #1: a peer Sales Manager (or a staff account with no Sales role) is not somebody the view reports on."""
        async def check(world):
            for person in (world.manager, await _user(world.db)):
                for call in (batch_views.employee_performance(world.db, str(person.id)), batch_views.employee_history(world.db, str(person.id), days=3, sessions=3)):
                    with pytest.raises(HTTPException) as e:
                        await call
                    assert e.value.status_code == 404
            assert (await batch_views.employee_performance(world.db, str(world.entry.id)))["data_entry"] is not None
            assert (await batch_views.employee_history(world.db, str(world.receivers[0].id), days=3, sessions=3))["user"]["id"] == str(world.receivers[0].id)
        _in_world(check)
