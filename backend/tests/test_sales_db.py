"""JAZ Sales - Phase 1: database-level tests (no HTTP).

Proves the guarantees at the constraint / repository / resolver layer, below the
API: seed content, ck_users_role, the partial unique index, FK delete rules, grant
history preservation, idempotency and race-safety of grant/revoke, and permission
resolution (union, revoked grants, retired roles, inactive accounts).

Same pattern as test_leave_db_constraints.py (asyncio.run + engine.dispose()).
Refuses to run unless DATABASE_URL is the scratch database.
"""
from sales_test_utils import ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import asyncio
import random
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from database import SessionLocal, engine
from models import User
from sales.models import StaffPermission, StaffRole, StaffRolePermission, StaffUserRole
from sales.repositories import staff as staff_repo
from sales.services.access import resolve_staff_context


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _new_user(db, role="jaz_staff", status="active"):
    user = User(
        id=uuid.uuid4(), email=f"dbtest-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1666{random.randint(1000000, 9999999)}",
        password="not-a-real-hash", name="DB Test", role=role, status=status,
    )
    db.add(user)
    await db.flush()
    return user


async def _role(db, key):
    return (await db.execute(select(StaffRole).where(StaffRole.key == key))).scalar_one()


async def _purge_users(user_ids):
    async with SessionLocal() as db:
        await db.execute(delete(StaffUserRole).where(StaffUserRole.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


def _user_dict(user):
    return {"id": str(user.id), "role": user.role, "status": user.status}


# =============================================================================
class TestSeedAndCheckConstraint:
    def test_seed_is_exactly_the_approved_reference_data(self):
        async def _run():
            async with SessionLocal() as db:
                # SYSTEM roles only: other test modules create (and later retire) throwaway roles in the same DB
                roles = {r.key: r for r in (await db.execute(select(StaffRole).where(StaffRole.is_system.is_(True)))).scalars()}
                assert set(roles) == {"sales_manager", "sales_employee", "lead_data_entry", "onboarding_employee"}
                assert all(r.is_system and r.is_active and r.module == "sales" for r in roles.values())
                perms = {p.key for p in (await db.execute(select(StaffPermission))).scalars()}
                assert perms == ALL_PERMISSION_KEYS  # Phase 1 + Phase 2 + Phase 3 catalog
                grants = {(roles_by_id, k) for roles_by_id, k in (await db.execute(
                    select(StaffRole.key, StaffRolePermission.permission_key).join(StaffRolePermission, StaffRolePermission.role_id == StaffRole.id)
                    .where(StaffRole.is_system.is_(True))
                )).all()}
                assert grants == {(role, key) for role, keys in SYSTEM_ROLE_PERMISSIONS.items() for key in keys}
        run(_run())

    def test_ck_users_role_accepts_jaz_staff_and_rejects_anything_else(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _new_user(db, role="jaz_staff")  # accepted
                await db.rollback()
            async with SessionLocal() as db:
                with pytest.raises(IntegrityError) as exc:
                    await _new_user(db, role="platform_overlord")
                assert "ck_users_role" in str(exc.value)
                await db.rollback()
        run(_run())


class TestGrantConstraints:
    def test_second_active_grant_is_rejected_by_the_partial_unique_index(self):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "sales_employee")
                    db.add(StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id, granted_by=user.id))
                    await db.flush()
                    db.add(StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id, granted_by=user.id))
                    with pytest.raises(IntegrityError) as exc:
                        await db.flush()
                    assert "uq_staff_user_roles_active" in str(exc.value)
                    await db.rollback()
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_history_is_preserved_across_grant_revoke_regrant(self):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "sales_manager")
                    assert await staff_repo.grant_role(db, user.id, role.id, user.id) is True
                    assert await staff_repo.revoke_role(db, user.id, role.id, user.id) is True
                    assert await staff_repo.grant_role(db, user.id, role.id, user.id) is True   # allowed again after revoke
                    await db.commit()
                async with SessionLocal() as db:
                    rows = (await db.execute(select(StaffUserRole).where(StaffUserRole.user_id == uid).order_by(StaffUserRole.granted_at))).scalars().all()
                    assert len(rows) == 2, "revoking must keep the row - history is never deleted"
                    assert sum(1 for r in rows if r.revoked_at is None) == 1
                    revoked = [r for r in rows if r.revoked_at is not None][0]
                    assert revoked.revoked_by == uid and revoked.revoked_at >= revoked.granted_at
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_check_revoked_not_before_granted(self):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "sales_employee")
                    await staff_repo.grant_role(db, user.id, role.id, user.id)
                    # revoked_by is set too: with ck_staff_user_roles_revoked_pair in place, setting revoked_at alone
                    # would violate TWO constraints and the test would depend on which one PostgreSQL reports first.
                    with pytest.raises(IntegrityError) as exc:
                        await db.execute(update(StaffUserRole).where(StaffUserRole.user_id == user.id)
                                         .values(revoked_at=text("granted_at - interval '1 day'"), revoked_by=user.id))
                    assert "ck_staff_user_roles_revoked_after_granted" in str(exc.value)
                    await db.rollback()
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_delete_rules(self):
        """role in use: RESTRICT; catalog permission in use: RESTRICT; user with history: NO ACTION;
        role_permissions: CASCADE with its role."""
        async def _run():
            uid = None
            temp_role_id = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "sales_employee")
                    await staff_repo.grant_role(db, user.id, role.id, user.id)
                    await db.commit()

                async with SessionLocal() as db:   # a role that has been assigned can't be deleted
                    role = await _role(db, "sales_employee")
                    with pytest.raises(IntegrityError):
                        await db.execute(delete(StaffRole).where(StaffRole.id == role.id))
                    await db.rollback()
                async with SessionLocal() as db:   # a permission still granted by a role can't be deleted
                    with pytest.raises(IntegrityError):
                        await db.execute(delete(StaffPermission).where(StaffPermission.key == "sales.access"))
                    await db.rollback()
                async with SessionLocal() as db:   # a user with grant history can't be hard-deleted
                    with pytest.raises(IntegrityError):
                        await db.execute(delete(User).where(User.id == uid))
                    await db.rollback()

                async with SessionLocal() as db:   # unassigned role: deletable, and its grants cascade away
                    temp = StaffRole(id=uuid.uuid4(), module="sales", key=f"temp_{uuid.uuid4().hex[:8]}", name_en="t", name_ar="t")
                    db.add(temp); temp_role_id = temp.id
                    await db.flush()
                    db.add(StaffRolePermission(role_id=temp.id, permission_key="sales.access"))
                    await db.flush()
                    await db.execute(delete(StaffRole).where(StaffRole.id == temp.id))
                    left = (await db.execute(select(func.count()).select_from(StaffRolePermission).where(StaffRolePermission.role_id == temp.id))).scalar_one()
                    assert left == 0
                    await db.commit(); temp_role_id = None
            finally:
                if uid:
                    await _purge_users([uid])
                if temp_role_id:
                    async with SessionLocal() as db:
                        await db.execute(delete(StaffRole).where(StaffRole.id == temp_role_id)); await db.commit()
        run(_run())


# =============================================================================
class TestRepositoryIdempotencyAndRaces:
    def test_grant_and_revoke_report_whether_they_changed_anything(self):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "lead_data_entry")
                    assert [await staff_repo.grant_role(db, user.id, role.id, user.id) for _ in range(3)] == [True, False, False]
                    assert [await staff_repo.revoke_role(db, user.id, role.id, user.id) for _ in range(3)] == [True, False, False]
                    await db.commit()
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_concurrent_grants_insert_exactly_once_and_concurrent_revokes_change_exactly_once(self):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db, "sales_manager")
                    role_id = role.id
                    await db.commit()

                async def one(fn):
                    async with SessionLocal() as s:          # own connection + own transaction
                        changed = await fn(s, uid, role_id, uid)
                        await s.commit()
                        return changed

                grants = await asyncio.gather(*[one(staff_repo.grant_role) for _ in range(12)])
                assert sum(grants) == 1, grants
                async with SessionLocal() as db:
                    active = (await db.execute(select(func.count()).select_from(StaffUserRole).where(StaffUserRole.user_id == uid, StaffUserRole.revoked_at.is_(None)))).scalar_one()
                    assert active == 1

                revokes = await asyncio.gather(*[one(staff_repo.revoke_role) for _ in range(12)])
                assert sum(revokes) == 1, revokes
                async with SessionLocal() as db:
                    total = (await db.execute(select(func.count()).select_from(StaffUserRole).where(StaffUserRole.user_id == uid))).scalar_one()
                    assert total == 1, "row kept as history"
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_revoke_from_a_transaction_that_started_before_the_grant_committed(self):
        """Regression test for using clock_timestamp() (not now()) in revoke: transaction A starts
        first (now() = t1), transaction B then grants and commits (granted_at = t2 > t1); A then
        revokes. With revoked_at = now() that would be t1 < t2 and violate the CHECK."""
        async def _run():
            uid = None
            try:
                async with SessionLocal() as setup:
                    user = await _new_user(setup); uid = user.id
                    role = await _role(setup, "sales_employee"); role_id = role.id
                    await setup.commit()

                a = SessionLocal()
                try:
                    await a.execute(text("select 1"))            # A's transaction begins (its now() is fixed here)
                    await asyncio.sleep(0.05)
                    async with SessionLocal() as b:              # B starts later, grants, commits
                        assert await staff_repo.grant_role(b, uid, role_id, uid) is True
                        await b.commit()
                    assert await staff_repo.revoke_role(a, uid, role_id, uid) is True   # must NOT raise the CHECK violation
                    await a.commit()
                finally:
                    await a.close()
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())


# =============================================================================
class TestPermissionResolution:
    def test_platform_role_decision_table(self):
        async def _run():
            uids = []
            try:
                async with SessionLocal() as db:
                    staff_active = await _new_user(db, "jaz_staff", "active"); uids.append(staff_active.id)
                    staff_inactive = await _new_user(db, "jaz_staff", "inactive"); uids.append(staff_inactive.id)
                    for platform_role in ("company_owner", "employee"):
                        with pytest.raises(HTTPException) as exc:
                            await resolve_staff_context(db, {"id": str(uuid.uuid4()), "role": platform_role, "status": "active"})
                        assert exc.value.status_code == 403
                    with pytest.raises(HTTPException) as exc:
                        await resolve_staff_context(db, {"id": str(uuid.uuid4()), "role": "some_future_role", "status": "active"})
                    assert exc.value.status_code == 403

                    sa = await resolve_staff_context(db, {"id": str(uuid.uuid4()), "role": "super_admin", "status": "active"})
                    assert sa.is_super_admin and sa.permissions == frozenset(ALL_PERMISSION_KEYS)

                    with pytest.raises(HTTPException) as exc:
                        await resolve_staff_context(db, _user_dict(staff_inactive))          # deactivated -> denied outright
                    assert exc.value.status_code == 403

                    none_ctx = await resolve_staff_context(db, _user_dict(staff_active))      # active, zero roles -> holds nothing
                    assert not none_ctx.is_super_admin and none_ctx.permissions == frozenset() and none_ctx.roles == ()
                    await db.rollback()
            finally:
                if uids:
                    await _purge_users(uids)
        run(_run())

    def test_union_revoked_and_retired_roles(self):
        async def _run():
            uid = None
            temp_id = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    mgr, emp = await _role(db, "sales_manager"), await _role(db, "sales_employee")
                    emp_perms = frozenset(SYSTEM_ROLE_PERMISSIONS["sales_employee"])
                    await staff_repo.grant_role(db, user.id, emp.id, user.id)
                    ctx = await resolve_staff_context(db, _user_dict(user))
                    assert ctx.permissions == emp_perms

                    await staff_repo.grant_role(db, user.id, mgr.id, user.id)                     # union grows
                    ctx = await resolve_staff_context(db, _user_dict(user))
                    assert ctx.permissions == emp_perms | SYSTEM_ROLE_PERMISSIONS["sales_manager"]
                    assert "sales.team.view" in ctx.permissions

                    await staff_repo.revoke_role(db, user.id, mgr.id, user.id)                    # revoked grant contributes nothing
                    ctx = await resolve_staff_context(db, _user_dict(user))
                    assert ctx.permissions == emp_perms

                    # a RETIRED role (is_active=False) contributes nothing even while still granted
                    temp = StaffRole(id=uuid.uuid4(), module="sales", key=f"temp_{uuid.uuid4().hex[:8]}", name_en="t", name_ar="t"); temp_id = temp.id
                    db.add(temp); await db.flush()
                    db.add(StaffRolePermission(role_id=temp.id, permission_key="sales.team.view")); await db.flush()
                    await staff_repo.grant_role(db, user.id, temp.id, user.id)
                    ctx = await resolve_staff_context(db, _user_dict(user))
                    assert "sales.team.view" in ctx.permissions
                    temp.is_active = False; await db.flush()
                    ctx = await resolve_staff_context(db, _user_dict(user))
                    assert ctx.permissions == emp_perms
                    await db.commit()
            finally:
                if uid:
                    await _purge_users([uid])
                if temp_id:
                    async with SessionLocal() as db:
                        await db.execute(delete(StaffRole).where(StaffRole.id == temp_id)); await db.commit()
        run(_run())

    def test_staff_lookup_is_pinned_to_jaz_staff(self):
        async def _run():
            uids = []
            try:
                async with SessionLocal() as db:
                    owner = await _new_user(db, "company_owner"); employee = await _new_user(db, "employee")
                    staff_user = await _new_user(db, "jaz_staff"); uids = [owner.id, employee.id, staff_user.id]
                    assert await staff_repo.get_staff_user(db, owner.id) is None
                    assert await staff_repo.get_staff_user(db, employee.id) is None
                    assert (await staff_repo.get_staff_user(db, staff_user.id)).id == staff_user.id
                    staff_user.deleted_at = func.now(); await db.flush()
                    assert await staff_repo.get_staff_user(db, staff_user.id) is None      # soft-deleted staff are invisible
                    await db.rollback()
            finally:
                if uids:
                    await _purge_users(uids)
        run(_run())
