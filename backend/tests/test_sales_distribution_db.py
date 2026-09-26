"""JAZ Sales - simplified workflow: automatic lead distribution at the DATABASE / service level.

The distribution setting is ONE global row, and the scratch database holds every Sales Employee earlier runs created - so
switching it on through the API would hand the other test worker's new leads to strangers. These tests therefore drive the
real services (leads_service.create_lead, distribution.*) inside a transaction that is NEVER committed: in it they switch
distribution on, and retire every other role that makes people assignable, so the rotation holds exactly the people the
test created. Nothing here is visible outside the test's own transaction (other workers read the committed row: OFF).

Also here: the export's row limit (a 413 before any file is built).
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)

import asyncio
import random
import uuid
from collections import Counter

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text, update

from database import SessionLocal, engine
from models import User
from sales import permissions as P
from sales.lead_schemas import LeadCreate
from sales.models import SalesLead, SalesLeadActivity, StaffAuditEvent, StaffRole, StaffRolePermission, StaffUserRole
from sales.repositories import leads as leads_repo
from sales.repositories import settings as settings_repo
from sales.repositories.leads import LeadFilters
from sales.services import distribution
from sales.services import export as export_service
from sales.services import leads as leads_service
from sales.services.access import StaffContext
from sales.services.audit import new_audit_context
from sales.setup_schemas import DistributionUpdate

RECEIVER_KEYS = (P.PERM_ACCESS, P.PERM_LEADS_VIEW, P.PERM_LEADS_SCOPE_ASSIGNED)
CREATOR_KEYS = (P.PERM_ACCESS, P.PERM_LEADS_VIEW, P.PERM_LEADS_CREATE, P.PERM_LEADS_SCOPE_INTAKE)
MANAGER_KEYS = (
    P.PERM_ACCESS, P.PERM_SETTINGS_MANAGE, P.PERM_LEADS_VIEW, P.PERM_LEADS_CREATE, P.PERM_LEADS_ASSIGN, P.PERM_LEADS_SCOPE_ALL,
    P.PERM_LEADS_EXPORT,
)


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _user(db, *, status="active") -> User:
    user = User(
        id=uuid.uuid4(), email=f"dist-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1666{random.randint(100000000, 999999999)}",
        password="not-a-real-hash", name=f"Dist {uuid.uuid4().hex[:6]}", role="jaz_staff", status=status,
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


async def _grant(db, user, role) -> StaffUserRole:
    grant = StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id, granted_by=user.id)
    db.add(grant)
    await db.flush()
    return grant


def _ctx(user, role, keys) -> StaffContext:
    return StaffContext(
        {"id": str(user.id), "name": user.name, "role": "jaz_staff", "status": "active"}, user.id, False,
        ({"key": role.key, "name_en": "t", "name_ar": "t"},), frozenset(keys),
    )


class World:
    """This test's people, inside its own uncommitted transaction."""

    def __init__(self, db):
        self.db = db
        self.audit = new_audit_context("pytest")

    async def build(self, receivers: int = 3):
        db = self.db
        # The other xdist worker must not commit a new assignable role or grant while this world runs (READ COMMITTED would
        # show it to the next statement and put a stranger in the rotation). Its READS are never blocked; its writes to these
        # tables wait the second or two until this transaction is rolled back.
        await db.execute(text("LOCK TABLE staff_roles, staff_role_permissions, staff_user_roles IN SHARE ROW EXCLUSIVE MODE"))
        self.receiver_role = await _role(db, RECEIVER_KEYS)
        self.receivers = []
        self.grants = {}
        for _ in range(receivers):
            user = await _user(db)
            self.grants[user.id] = await _grant(db, user, self.receiver_role)
            self.receivers.append(user)
        manager_role = await _role(db, MANAGER_KEYS)
        self.manager = await _user(db)
        await _grant(db, self.manager, manager_role)
        self.manager_ctx = _ctx(self.manager, manager_role, MANAGER_KEYS)
        creator_role = await _role(db, CREATOR_KEYS)
        self.creator = await _user(db)
        await _grant(db, self.creator, creator_role)
        self.creator_ctx = _ctx(self.creator, creator_role, CREATOR_KEYS)

        # Everybody ELSE who could receive leads leaves the rotation - in this transaction only.
        await db.execute(
            update(StaffRole)
            .where(
                StaffRole.is_active.is_(True), StaffRole.id != self.receiver_role.id,
                StaffRole.id.in_(select(StaffRolePermission.role_id).where(StaffRolePermission.permission_key == P.PERM_LEADS_SCOPE_ASSIGNED)),
            )
            .values(is_active=False)
        )
        settings = await settings_repo.get_for_update(db)
        settings.last_assigned_user_id = None
        settings.auto_distribution_enabled = False
        await db.flush()
        return self

    async def rotation_ids(self):
        return [uuid.UUID(member["id"]) for member in (await distribution.get_distribution(self.db))["rotation"]]

    async def switch(self, on: bool):
        return await distribution.update_distribution(
            self.db, self.manager_ctx, DistributionUpdate(auto_distribution_enabled=on), self.audit,
        )

    async def create(self, ctx=None, **fields):
        values = {"business_name": f"Dist Lead {uuid.uuid4().hex[:10]}", "phone": f"+1{random.randint(2000000000, 9999999999)}"}
        values.update(fields)
        return await leads_service.create_lead(self.db, ctx or self.creator_ctx, LeadCreate(**values), self.audit)

    async def cursor(self):
        return (await settings_repo.get(self.db)).last_assigned_user_id


def _in_world(fn, receivers: int = 3):
    async def _go():
        async with SessionLocal() as db:
            try:
                world = await World(db).build(receivers)
                return await fn(world)
            finally:
                await db.rollback()          # NOTHING of this test is ever committed
    return run(_go())


def _owner(lead) -> uuid.UUID:
    return uuid.UUID(lead["assigned_to"]["id"]) if lead["assigned_to"] else None


class TestRoundRobin:
    def test_the_rotation_is_exactly_the_eligible_people_in_creation_order(self):
        async def check(world):
            ids = await world.rotation_ids()
            assert len(ids) == len(world.receivers) and set(ids) == {u.id for u in world.receivers}
            assert ids == await world.rotation_ids()                          # a stable order, asked twice
        _in_world(check)

    def test_equal_distribution_hands_out_new_leads_in_turn(self):
        async def check(world):
            await world.switch(True)
            order = await world.rotation_ids()
            leads = [await world.create() for _ in range(3 * len(order))]
            owners = [_owner(lead) for lead in leads]
            assert owners == order * 3                                    # deterministic round-robin from the start
            assert Counter(owners) == {uid: 3 for uid in order}           # equal
            assert all(lead["pipeline_stage"] == "assigned" for lead in leads)
            assert await world.cursor() == owners[-1]
            # the timeline says it was automatic, and who created the lead is untouched
            events = (await world.db.execute(
                select(SalesLeadActivity).where(SalesLeadActivity.lead_id == uuid.UUID(leads[0]["id"]), SalesLeadActivity.event_type == "lead_assigned")
            )).scalars().all()
            assert len(events) == 1
            assert events[0].event_metadata["automatic"] is True and events[0].event_metadata["cause"] == "auto_distribution"
            assert events[0].actor_user_id == world.creator.id and leads[0]["created_by"]["id"] == str(world.creator.id)
        _in_world(check)

    def test_switched_off_a_new_lead_stays_unassigned(self):
        async def check(world):
            lead = await world.create()
            assert lead["assigned_to"] is None and lead["pipeline_stage"] == "new"
            assert await world.cursor() is None
            await world.switch(True)
            await world.switch(False)
            lead = await world.create()
            assert lead["assigned_to"] is None and lead["pipeline_stage"] == "new"
        _in_world(check)

    def test_inactive_revoked_and_deleted_people_are_skipped(self):
        async def check(world):
            order = await world.rotation_ids()
            by_id = {u.id: u for u in world.receivers}
            inactive, revoked, deleted = order[1], order[2], order[3]
            by_id[inactive].status = "inactive"
            by_id[deleted].deleted_at = func.now()
            world.grants[revoked].revoked_at = func.clock_timestamp()
            world.grants[revoked].revoked_by = world.manager.id
            await world.db.flush()
            await world.switch(True)
            first = _owner(await world.create())
            assert first == order[0]
            # "whose turn is next" (the manager's view) skips them too, not only the assignment itself
            assert (await distribution.get_distribution(world.db))["next_assignee"]["id"] == str(order[4])
            owners = [first] + [_owner(await world.create()) for _ in range(3)]
            assert owners == [order[0], order[4], order[0], order[4]]
            assert await world.rotation_ids() == [order[0], order[4]]
        _in_world(check, receivers=5)

    def test_a_retired_role_takes_its_people_out(self):
        async def check(world):
            world.receiver_role.is_active = False
            await world.db.flush()
            await world.switch(True)
            lead = await world.create()
            assert lead["assigned_to"] is None and lead["pipeline_stage"] == "new"     # nobody eligible: unassigned, no error
            assert (await distribution.get_distribution(world.db))["next_assignee"] is None
        _in_world(check)

    def test_a_chosen_owner_wins_and_does_not_move_the_rotation(self):
        async def check(world):
            order = await world.rotation_ids()
            await world.switch(True)
            chosen = await world.create(ctx=world.manager_ctx, assigned_to=order[2])
            assert _owner(chosen) == order[2] and await world.cursor() is None
            assert _owner(await world.create()) == order[0]
        _in_world(check)

    def test_the_rotation_continues_after_whoever_had_the_last_turn_even_if_they_left(self):
        async def check(world):
            order = await world.rotation_ids()
            settings = await settings_repo.get_for_update(world.db)
            settings.last_assigned_user_id = order[1]
            next(u for u in world.receivers if u.id == order[1]).status = "inactive"
            await world.db.flush()
            await world.switch(True)
            assert (await distribution.get_distribution(world.db))["next_assignee"]["id"] == str(order[2])
            assert [_owner(await world.create()) for _ in range(3)] == [order[2], order[0], order[2]]
        _in_world(check)

    def test_manual_reassignment_is_untouched(self):
        async def check(world):
            order = await world.rotation_ids()
            await world.switch(True)
            lead = await world.create()
            assert _owner(lead) == order[0]
            result = await leads_service.assign_lead(world.db, world.manager_ctx, lead["id"], order[2], world.audit)
            assert _owner(result["lead"]) == order[2]
            assert await world.cursor() == order[0]                          # the rotation only moves for new leads
        _in_world(check)


class TestSettings:
    def test_a_change_is_audited_and_a_repeat_writes_nothing(self):
        async def check(world):
            def events():
                return select(func.count()).select_from(StaffAuditEvent).where(
                    StaffAuditEvent.action == "sales_settings_updated", StaffAuditEvent.correlation_id == world.audit.correlation_id,
                )
            out = await world.switch(True)
            assert out["auto_distribution_enabled"] is True and out["distribution_mode"] == "equal"
            assert out["updated_by"]["id"] == str(world.manager.id)
            assert (await world.db.execute(events())).scalar_one() == 1
            await world.switch(True)                                           # nothing changes: no second event
            assert (await world.db.execute(events())).scalar_one() == 1
            row = (await world.db.execute(select(StaffAuditEvent).where(
                StaffAuditEvent.action == "sales_settings_updated", StaffAuditEvent.correlation_id == world.audit.correlation_id,
            ))).scalar_one()
            assert row.before_data == {"auto_distribution_enabled": False, "distribution_mode": "equal"}
            assert row.after_data == {"auto_distribution_enabled": True, "distribution_mode": "equal"}
            assert row.actor_user_id == world.manager.id and row.target_type == "sales_settings"
        _in_world(check)

    def test_the_settings_row_is_a_singleton(self):
        async def check(world):
            from sqlalchemy import text
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(text("INSERT INTO sales_settings (id) VALUES (2)"))
            with pytest.raises(IntegrityError):
                async with world.db.begin_nested():
                    await world.db.execute(text("UPDATE sales_settings SET distribution_mode = 'by_region' WHERE id = 1"))
        _in_world(check)


class TestExportLimit:
    def test_more_rows_than_the_limit_is_413_before_any_file_is_built(self, monkeypatch):
        monkeypatch.setattr(export_service, "EXPORT_MAX_ROWS", 1)
        built = []
        monkeypatch.setattr(export_service, "to_xlsx", lambda *a, **k: built.append(1) or b"")

        async def check(world):
            await world.create(ctx=world.manager_ctx)
            await world.create(ctx=world.manager_ctx)
            with pytest.raises(HTTPException) as caught:
                await export_service.export_leads(
                    world.db, world.manager_ctx, LeadFilters(), sort="created_at", descending=True, fmt="xlsx", lang="en",
                    audit=world.audit,
                )
            assert caught.value.status_code == 413 and caught.value.detail["code"] == "export_too_large"
            assert built == []
        _in_world(check)

    def test_the_export_reads_every_matching_row_in_one_query(self):
        async def check(world):
            token = uuid.uuid4().hex[:12]
            for i in range(30):
                await world.create(ctx=world.manager_ctx, business_name=f"Export {token} {i}")
            rows = await leads_repo.list_all_rows(
                world.db, visibility=leads_service.lead_visibility(world.manager_ctx), filters=LeadFilters(q=token),
                sort="business_name", descending=False, max_rows=100,
            )
            assert len(rows) == 30                                            # every match, far past one page of 25
        _in_world(check)
