"""JAZ Sales - Phase 3: database-level tests (no HTTP).

Proves the guarantees at the constraint / trigger / query layer, below the API: the seeded permissions, every CHECK and
FK on calls / follow-ups / demos / trials, the partial unique index behind "one active trial per lead", the timeline
accepting (and still protecting) the new event types, that a page of work items costs a constant number of queries (no
N+1), the SQL scope predicate applied to work items, the row locks that keep concurrent changes from interleaving, and
that a failing timeline writer rolls back the change it was recording. Refuses to run unless DATABASE_URL is the scratch
database.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import asyncio
import itertools
import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from database import SessionLocal, engine, get_db
from models import User
from sales import activity_schemas as S
from sales import constants as C
from sales import permissions as P
from sales.models import (
    SalesCall,
    SalesDemo,
    SalesFollowup,
    SalesLead,
    SalesLeadActivity,
    SalesTrial,
    StaffPermission,
    StaffRole,
    StaffRolePermission,
    StaffUserRole,
)
from sales.repositories import activities as activities_repo
from sales.repositories import calls as calls_repo
from sales.repositories import demos as demos_repo
from sales.repositories import followups as followups_repo
from sales.repositories import trials as trials_repo
from sales.repositories import work as work_repo
from sales.repositories.calls import CallFilters
from sales.repositories.demos import DemoFilters
from sales.repositories.followups import FollowupFilters
from sales.repositories.trials import TrialFilters
from sales.services import activity as activity_service
from sales.services import audit as audit_service
from sales.services import calls as calls_service
from sales.services import demos as demos_service
from sales.services import followups as followups_service
from sales.services import leads as leads_service
from sales.services import trials as trials_service
from sales.services import work as work_service
from sales.services.access import StaffContext
from sales.services.lead_access import can_see, lead_visibility

KINDS = ("calls", "followups", "demos", "trials")
PHASE3_PERMISSIONS = sorted(f"sales.{k}.{a}" for k in KINDS for a in ("view", "manage"))
TABLES = (SalesCall, SalesFollowup, SalesDemo, SalesTrial)


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


def now() -> datetime:
    return datetime.now(timezone.utc)


async def _user(db, role="jaz_staff", status="active") -> User:
    user = User(
        id=uuid.uuid4(), email=f"actdb-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1666{random.randint(1000000, 9999999)}",
        password="not-a-real-hash", name="Activity DB Test", role=role, status=status,
    )
    db.add(user)
    await db.flush()
    return user


def _lead(created_by, **kw) -> SalesLead:
    token = uuid.uuid4().hex[:10]
    values = dict(id=uuid.uuid4(), created_by=created_by, business_name=f"Activity DB {token}", name_norm=f"activity db {token}")
    values.update(kw)
    return SalesLead(**values)


async def _violates(db, constraint: str, obj) -> None:
    """Flushing `obj` must fail on exactly `constraint`, leaving the session usable (savepoint)."""
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            db.add(obj)
            await db.flush()
    assert f'"{constraint}"' in str(exc.value), f"expected {constraint}, got: {str(exc.value)[:300]}"


async def _staff(db, permissions):
    """An active jaz_staff account holding `permissions` through a real (throwaway) role, and its StaffContext."""
    user = await _user(db)
    role = StaffRole(id=uuid.uuid4(), module="sales", key=f"t_{uuid.uuid4().hex[:10]}", name_en="t", name_ar="t")
    db.add(role)
    await db.flush()
    for key in permissions:
        db.add(StaffRolePermission(role_id=role.id, permission_key=key))
    await db.flush()
    db.add(StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id, granted_by=user.id))
    await db.flush()
    ctx = StaffContext(
        {"id": str(user.id), "name": user.name, "role": "jaz_staff", "status": "active"}, user.id, False,
        ({"key": role.key, "name_en": "t", "name_ar": "t"},), frozenset(permissions),
    )
    return user, ctx


def _rows_for(lead, user, n=1):
    """Valid rows of every kind for `lead`, made by `user`."""
    t = now()
    return {
        "call": [SalesCall(id=uuid.uuid4(), lead_id=lead.id, employee_id=user.id, result="busy", called_at=t - timedelta(minutes=i)) for i in range(n)],
        "followup": [SalesFollowup(id=uuid.uuid4(), lead_id=lead.id, assigned_to=user.id, created_by=user.id, due_at=t + timedelta(days=i + 1)) for i in range(n)],
        "demo": [SalesDemo(id=uuid.uuid4(), lead_id=lead.id, assigned_to=user.id, created_by=user.id, scheduled_at=t + timedelta(days=i + 1)) for i in range(n)],
        "trial": [SalesTrial(id=uuid.uuid4(), lead_id=lead.id, created_by=user.id, started_at=t - timedelta(days=1), expected_end_at=t + timedelta(days=13),
                             status="completed" if i else "active", actual_end_at=(t - timedelta(hours=i)) if i else None) for i in range(n)],
    }


# =============================================================================
class TestSeed:
    def test_the_eight_permissions_are_in_the_catalog_with_the_codes_descriptions(self):
        async def _run():
            async with SessionLocal() as db:
                rows = {p.key: p for p in (await db.execute(select(StaffPermission).where(StaffPermission.key.in_(PHASE3_PERMISSIONS)))).scalars()}
                assert sorted(rows) == PHASE3_PERMISSIONS
                assert all(r.module == "sales" and r.description == P.ALL_PERMISSIONS[k] for k, r in rows.items())
        run(_run())

    def test_only_manager_and_employee_hold_them_through_the_system_roles(self):
        async def _run():
            async with SessionLocal() as db:
                grants = (await db.execute(
                    select(StaffRole.key, StaffRolePermission.permission_key).join(StaffRolePermission, StaffRolePermission.role_id == StaffRole.id)
                    .where(StaffRole.is_system.is_(True), StaffRolePermission.permission_key.in_(PHASE3_PERMISSIONS))
                )).all()
                by_role = {}
                for role, key in grants:
                    by_role.setdefault(role, set()).add(key)
                assert by_role == {"sales_manager": set(PHASE3_PERMISSIONS), "sales_employee": set(PHASE3_PERMISSIONS)}
        run(_run())


# =============================================================================
class TestCallConstraints:
    def test_every_check_is_enforced(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, employee_id=user.id, result="answered")
                await _violates(db, "ck_sales_calls_result", SalesCall(**{**base, "result": "great"}))
                await _violates(db, "ck_sales_calls_result", SalesCall(**{**base, "result": ""}))
                await _violates(db, "ck_sales_calls_duration", SalesCall(**base, duration_seconds=-1))
                await _violates(db, "ck_sales_calls_duration", SalesCall(**base, duration_seconds=C.MAX_CALL_SECONDS + 1))
                await _violates(db, "ck_sales_calls_notes", SalesCall(**base, notes="x" * 5001))
                await db.rollback()
        run(_run())

    def test_valid_combinations_are_accepted_and_defaults_apply(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                for result in C.CALL_RESULTS:                                                    # every result in the vocabulary
                    db.add(SalesCall(lead_id=lead.id, employee_id=user.id, result=result))
                for seconds in (None, 0, C.MAX_CALL_SECONDS):
                    db.add(SalesCall(lead_id=lead.id, employee_id=user.id, result="busy", duration_seconds=seconds))
                db.add(SalesCall(lead_id=lead.id, employee_id=user.id, result="busy", notes="x" * 5000))
                await db.flush()
                first = (await db.execute(select(SalesCall).where(SalesCall.lead_id == lead.id).limit(1))).scalar_one()
                await db.refresh(first)
                assert first.called_at is not None and first.created_at is not None and first.updated_at is not None
                assert abs(first.called_at - now()) < timedelta(minutes=1)                       # called_at defaults to now
                await db.rollback()
        run(_run())

    def test_foreign_keys(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                await _violates(db, "sales_calls_lead_id_fkey", SalesCall(lead_id=uuid.uuid4(), employee_id=user.id, result="busy"))
                await _violates(db, "sales_calls_employee_id_fkey", SalesCall(lead_id=lead.id, employee_id=uuid.uuid4(), result="busy"))
                await db.rollback()
        run(_run())


class TestFollowupConstraints:
    def test_every_check_is_enforced(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, due_at=now())
                await _violates(db, "ck_sales_followups_status", SalesFollowup(**base, status="done"))
                await _violates(db, "ck_sales_followups_completed_pair", SalesFollowup(**base, status="completed"))                   # completed needs its time ...
                await _violates(db, "ck_sales_followups_completed_pair", SalesFollowup(**base, status="pending", completed_at=now()))   # ... and nothing else has one
                await _violates(db, "ck_sales_followups_completed_pair", SalesFollowup(**base, status="cancelled", completed_at=now()))
                await _violates(db, "ck_sales_followups_notes", SalesFollowup(**base, notes="x" * 5001))
                await db.rollback()
        run(_run())

    def test_valid_combinations_are_accepted_and_the_default_is_pending(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, due_at=now())
                plain = SalesFollowup(**base)
                db.add_all([plain, SalesFollowup(**base, status="pending"), SalesFollowup(**base, status="completed", completed_at=now()), SalesFollowup(**base, status="cancelled")])
                await db.flush()
                await db.refresh(plain)
                assert plain.status == "pending" and plain.completed_at is None
                await db.rollback()
        run(_run())

    def test_foreign_keys(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, due_at=now())
                await _violates(db, "sales_followups_lead_id_fkey", SalesFollowup(**{**base, "lead_id": uuid.uuid4()}))
                await _violates(db, "sales_followups_assigned_to_fkey", SalesFollowup(**{**base, "assigned_to": uuid.uuid4()}))
                await _violates(db, "sales_followups_created_by_fkey", SalesFollowup(**{**base, "created_by": uuid.uuid4()}))
                await db.rollback()
        run(_run())


class TestDemoConstraints:
    def test_every_check_is_enforced(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, scheduled_at=now())
                await _violates(db, "ck_sales_demos_status", SalesDemo(**base, status="held"))
                await _violates(db, "ck_sales_demos_completed_pair", SalesDemo(**base, status="completed"))
                await _violates(db, "ck_sales_demos_completed_pair", SalesDemo(**base, status="scheduled", completed_at=now()))
                await _violates(db, "ck_sales_demos_completed_pair", SalesDemo(**base, status="no_show", completed_at=now()))
                await _violates(db, "ck_sales_demos_completed_pair", SalesDemo(**base, status="cancelled", completed_at=now()))
                await _violates(db, "ck_sales_demos_notes", SalesDemo(**base, notes="x" * 5001))
                await db.rollback()
        run(_run())

    def test_valid_combinations_are_accepted_and_the_default_is_scheduled(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, scheduled_at=now())
                plain = SalesDemo(**base)
                db.add(plain)
                for status in C.DEMO_STATUSES:                                                    # every status in the vocabulary
                    db.add(SalesDemo(**base, status=status, completed_at=now() if status == "completed" else None))
                await db.flush()
                await db.refresh(plain)
                assert plain.status == "scheduled"
                await db.rollback()
        run(_run())

    def test_foreign_keys(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, assigned_to=user.id, created_by=user.id, scheduled_at=now())
                await _violates(db, "sales_demos_lead_id_fkey", SalesDemo(**{**base, "lead_id": uuid.uuid4()}))
                await _violates(db, "sales_demos_assigned_to_fkey", SalesDemo(**{**base, "assigned_to": uuid.uuid4()}))
                await _violates(db, "sales_demos_created_by_fkey", SalesDemo(**{**base, "created_by": uuid.uuid4()}))
                await db.rollback()
        run(_run())


class TestTrialConstraints:
    def test_every_check_is_enforced(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                t = now()
                base = dict(created_by=user.id, started_at=t - timedelta(days=2), expected_end_at=t + timedelta(days=5))

                def fresh_lead():
                    lead = _lead(user.id)
                    db.add(lead)
                    return lead

                lead = fresh_lead()
                await db.flush()
                await _violates(db, "ck_sales_trials_status", SalesTrial(lead_id=lead.id, **base, status="paused", actual_end_at=t))
                await _violates(db, "ck_sales_trials_end_pair", SalesTrial(lead_id=lead.id, **base, status="active", actual_end_at=t))        # active has no end
                await _violates(db, "ck_sales_trials_end_pair", SalesTrial(lead_id=lead.id, **base, status="completed"))                       # ended ones do
                await _violates(db, "ck_sales_trials_end_pair", SalesTrial(lead_id=lead.id, **base, status="cancelled"))
                await _violates(db, "ck_sales_trials_expected_end", SalesTrial(lead_id=lead.id, **{**base, "expected_end_at": base["started_at"]}))
                await _violates(db, "ck_sales_trials_expected_end", SalesTrial(lead_id=lead.id, **{**base, "expected_end_at": base["started_at"] - timedelta(hours=1)}))
                await _violates(db, "ck_sales_trials_actual_end", SalesTrial(lead_id=lead.id, **base, status="completed", actual_end_at=base["started_at"] - timedelta(seconds=1)))
                await _violates(db, "ck_sales_trials_notes", SalesTrial(lead_id=lead.id, **base, notes="x" * 5001))
                await db.rollback()
        run(_run())

    def test_valid_combinations_are_accepted_and_the_default_is_active(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                t = now()
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, created_by=user.id, started_at=t - timedelta(days=2), expected_end_at=t + timedelta(days=5))
                plain = SalesTrial(**base)
                db.add(plain)
                await db.flush()
                await db.refresh(plain)
                assert plain.status == "active" and plain.actual_end_at is None
                plain.status, plain.actual_end_at = "completed", t                                # ended: the one active slot frees up
                db.add_all([SalesTrial(**base, status="cancelled", actual_end_at=t), SalesTrial(**base, status="completed", actual_end_at=base["started_at"])])
                await db.flush()
                other = _lead(user.id)
                db.add(other)
                await db.flush()
                db.add(SalesTrial(lead_id=other.id, created_by=user.id, expected_end_at=t + timedelta(days=1)))   # started_at defaults to now
                await db.flush()
                await db.rollback()
        run(_run())

    def test_foreign_keys(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(lead_id=lead.id, created_by=user.id, expected_end_at=now() + timedelta(days=3))
                await _violates(db, "sales_trials_lead_id_fkey", SalesTrial(**{**base, "lead_id": uuid.uuid4()}))
                await _violates(db, "sales_trials_created_by_fkey", SalesTrial(**{**base, "created_by": uuid.uuid4()}))
                await db.rollback()
        run(_run())


class TestOneActiveTrialPerLead:
    def test_the_partial_unique_index_allows_one_active_and_any_number_of_ended_trials(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                t = now()
                base = dict(lead_id=lead.id, created_by=user.id, started_at=t - timedelta(days=1), expected_end_at=t + timedelta(days=5))
                first = SalesTrial(**base)
                db.add(first)
                await db.flush()
                await _violates(db, "uq_sales_trials_one_active", SalesTrial(**base))                                                # a second active: refused
                db.add_all([SalesTrial(**base, status="completed", actual_end_at=t), SalesTrial(**base, status="cancelled", actual_end_at=t)])
                await db.flush()                                                                                                    # ended ones: fine
                first.status, first.actual_end_at = "completed", t
                await db.flush()
                db.add(SalesTrial(**base))                                                                                          # the slot is free again
                await db.flush()
                # and the OTHER direction: ending or re-activating must respect it too
                await _violates(db, "uq_sales_trials_one_active", SalesTrial(**base))
                await db.rollback()
        run(_run())

    def test_the_index_is_unique_and_partial_in_the_catalog(self):
        async def _run():
            async with SessionLocal() as db:
                definition = (await db.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_sales_trials_one_active'"))).scalar_one()
                assert "UNIQUE" in definition and "(lead_id)" in definition and "status" in definition and "'active'" in definition
        run(_run())

    def test_a_start_that_loses_the_race_answers_409_and_leaves_the_transaction_usable(self, monkeypatch):
        """The HTTP race test is timing-dependent (the pre-check usually catches the loser). Here the pre-check is made to
        lose deterministically - it sees no active trial although one exists - so ONLY the unique index can stop the
        second start. The service must turn that into the same clean 409 (never a raw database error / 500), write no
        event, and leave the surrounding transaction alive (the insert runs in a savepoint)."""
        async def _run():
            async with SessionLocal() as db:
                staff, ctx = await _staff(db, PHASE3_PERMISSIONS + [P.PERM_LEADS_SCOPE_ALL])
                lead = _lead(staff.id)
                db.add(lead)
                await db.flush()
                t = now()
                db.add(SalesTrial(id=uuid.uuid4(), lead_id=lead.id, created_by=staff.id, started_at=t - timedelta(days=1), expected_end_at=t + timedelta(days=5)))
                await db.flush()                                                                     # the winner of the race

                async def sees_nothing(db_, lead_id):
                    return None
                monkeypatch.setattr(trials_repo, "get_active_for_lead", sees_nothing)
                body = S.TrialCreate(lead_id=lead.id, expected_end_at=t + timedelta(days=14))
                with pytest.raises(HTTPException) as exc:
                    await trials_service.start_trial(db, ctx, body, audit_service.new_audit_context("race"))
                assert exc.value.status_code == 409 and exc.value.detail["code"] == "active_trial_exists" and exc.value.detail["field"] == "lead"
                monkeypatch.undo()

                assert (await db.execute(select(func.count()).select_from(SalesTrial).where(SalesTrial.lead_id == lead.id))).scalar_one() == 1
                assert (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id))).scalar_one() == 0
                await db.rollback()
        run(_run())


# =============================================================================
class TestRestrictiveForeignKeys:
    @pytest.mark.parametrize("kind,constraint", [("call", "sales_calls_lead_id_fkey"), ("followup", "sales_followups_lead_id_fkey"),
                                                 ("demo", "sales_demos_lead_id_fkey"), ("trial", "sales_trials_lead_id_fkey")])
    def test_a_lead_with_that_kind_of_work_cannot_be_deleted_and_the_work_survives(self, kind, constraint):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                (row,) = _rows_for(lead, user)[kind]
                db.add(row)
                await db.flush()
                with pytest.raises(IntegrityError) as exc:
                    async with db.begin_nested():
                        await db.execute(delete(SalesLead).where(SalesLead.id == lead.id))
                assert constraint in str(exc.value)
                assert (await db.execute(select(func.count()).select_from(type(row)).where(type(row).lead_id == lead.id))).scalar_one() == 1
                await db.rollback()
        run(_run())


# =============================================================================
class TestTimelineAcceptsThePhase3Events:
    EVENTS = sorted({t for types in activity_service.WORK_EVENT_TYPES.values() for t in types})

    def test_every_new_event_type_is_accepted_by_the_append_only_table(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                for event_type in self.EVENTS:
                    await activities_repo.insert_event(
                        db, lead_id=lead.id, event_type=event_type, actor_user_id=user.id, before={"a": 1}, after={"b": 2}, note="n",
                        metadata={"call_id": str(uuid.uuid4())}, correlation_id=uuid.uuid4(),
                    )
                assert (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id))).scalar_one() == len(self.EVENTS)
                await db.rollback()
        run(_run())

    def test_and_they_are_as_immutable_as_every_other_event(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                await activities_repo.insert_event(
                    db, lead_id=lead.id, event_type="call_created", actor_user_id=user.id, before=None, after={"result": "busy"}, note=None,
                    metadata={}, correlation_id=uuid.uuid4(),
                )
                for statement in (
                    update(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id).values(note="rewritten"),
                    delete(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id),
                    text("TRUNCATE sales_lead_activities"),
                ):
                    with pytest.raises(DBAPIError) as exc:
                        async with db.begin_nested():
                            await db.execute(statement)
                    assert "append-only" in str(exc.value)
                await db.rollback()
        run(_run())

    def test_the_reader_filter_is_an_allow_list_and_counts_only_what_it_returns(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                for event_type in ("lead_created", "call_created", "call_updated", "followup_created", "demo_scheduled"):
                    await activities_repo.insert_event(
                        db, lead_id=lead.id, event_type=event_type, actor_user_id=user.id, before=None, after=None, note=None, metadata={}, correlation_id=uuid.uuid4(),
                    )
                names = lambda rows: [e.event_type for e, _ in rows]  # noqa: E731
                rows, total = await activities_repo.list_for_lead(db, lead.id, 50, 0)
                assert total == 5 and names(rows) == ["demo_scheduled", "followup_created", "call_updated", "call_created", "lead_created"]      # no filter: everything
                rows, total = await activities_repo.list_for_lead(db, lead.id, 50, 0, {"lead_created", "call_created", "call_updated"})
                assert total == 3 and names(rows) == ["call_updated", "call_created", "lead_created"]
                rows, total = await activities_repo.list_for_lead(db, lead.id, 2, 2, {"lead_created", "call_created", "call_updated"})
                assert total == 3 and names(rows) == ["lead_created"]                                                                             # paging within the filtered set
                rows, total = await activities_repo.list_for_lead(db, lead.id, 50, 0, set())
                assert total == 0 and rows == []                                                                                                   # an empty allow-list shows nothing
                rows, total = await activities_repo.list_for_lead(db, lead.id, 50, 0, {"some_unknown_event"})
                assert total == 0
                await db.rollback()
        run(_run())


# =============================================================================
class TestNoNPlusOne:
    """A page of work items must cost a constant number of statements, however many rows it holds."""

    def _count_statements(self):
        statements = []

        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", before)
        return statements, lambda: event.remove(engine.sync_engine, "before_cursor_execute", before)

    def test_listing_counting_and_reading(self):
        marker = f"n1-{uuid.uuid4().hex[:10]}"

        async def _run():
            async with SessionLocal() as db:
                creator, worker = await _user(db), await _user(db)
                leads = []
                for i in range(6):
                    lead = _lead(creator.id, business_name=f"{marker} {i}", name_norm=marker, assigned_to=worker.id, assigned_at=func.now())
                    db.add(lead)
                    leads.append(lead)
                await db.flush()
                for lead in leads:
                    for rows in _rows_for(lead, worker, n=1).values():
                        db.add_all(rows)
                    for rows in _rows_for(lead, worker, n=1).values():          # a second, different item of each kind (one active trial per lead: skip trials)
                        db.add_all([r for r in rows if not isinstance(r, SalesTrial)])
                await db.commit()
                ctx = StaffContext({"role": "jaz_staff"}, creator.id, False, (), frozenset({P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_VIEW, *PHASE3_PERMISSIONS}))
                first_lead = leads[0].id
                one_call = (await db.execute(select(SalesCall.id).where(SalesCall.lead_id == first_lead).limit(1))).scalar_one()

            statements, unlisten = self._count_statements()
            try:
                async with SessionLocal() as db:
                    page = await calls_service.list_calls(db, ctx, CallFilters(q=marker), descending=True, limit=25, offset=0)
                    assert page["total"] == 12 and len(page["items"]) == 12
                    assert all(i["employee"]["id"] == str(worker.id) and i["lead"]["business_name"].startswith(marker) for i in page["items"])
                    assert len(statements) == 2, statements                        # one COUNT + one page query, every join included
                    del statements[:]

                    assert (await calls_service.result_counts(db, ctx, CallFilters(q=marker)))["total"] == 12 and len(statements) == 1, statements
                    del statements[:]
                    assert (await calls_service.get_call(db, ctx, str(one_call)))["employee"]["name"] == "Activity DB Test" and len(statements) == 1, statements
                    del statements[:]

                    page = await followups_service.list_followups(db, ctx, FollowupFilters(q=marker), sort="due_at", descending=False, limit=25, offset=0)
                    assert page["total"] == 12 and all(i["assigned_to"]["name"] == "Activity DB Test" and i["created_by"]["name"] for i in page["items"])
                    assert len(statements) == 2, statements
                    del statements[:]
                    assert (await followups_service.counts(db, ctx, FollowupFilters(q=marker)))["pending"] == 12 and len(statements) == 1, statements
                    del statements[:]

                    page = await demos_service.list_demos(db, ctx, DemoFilters(q=marker), descending=False, limit=25, offset=0)
                    assert page["total"] == 12 and all(i["assigned_to"]["id"] == str(worker.id) for i in page["items"]) and len(statements) == 2, statements
                    del statements[:]
                    assert (await demos_service.counts(db, ctx, DemoFilters(q=marker)))["scheduled"] == 12 and len(statements) == 1, statements
                    del statements[:]

                    page = await trials_service.list_trials(db, ctx, TrialFilters(q=marker), sort="expected_end_at", descending=False, limit=25, offset=0)
                    assert page["total"] == 6 and all(i["created_by"]["name"] for i in page["items"]) and len(statements) == 2, statements
                    del statements[:]
                    assert (await trials_service.counts(db, ctx, TrialFilters(q=marker), 3))["active"] == 6 and len(statements) == 1, statements
                    del statements[:]

                    events = await leads_service.list_activities(db, ctx, str(first_lead), 50, 0)
                    assert len(statements) == 3, statements                        # the lead (scope check) + COUNT + events joined with their actor
            finally:
                unlisten()

        run(_run())


# =============================================================================
class TestScopePredicateOnWorkItems:
    def test_the_sql_scope_applied_to_every_kind_agrees_with_the_python_mirror(self):
        marker = f"scope-{uuid.uuid4().hex[:10]}"

        async def _setup():
            async with SessionLocal() as db:
                me, other = await _user(db), await _user(db)
                specs = {
                    "mine_created": dict(created_by=me.id),                                                     # I created it, unassigned
                    "mine_assigned": dict(created_by=other.id, assigned_to=me.id, assigned_at=func.now()),      # assigned to me
                    "theirs_assigned": dict(created_by=other.id, assigned_to=other.id, assigned_at=func.now()),  # nothing of mine
                    "pool": dict(created_by=other.id),                                                          # somebody else's unassigned lead
                    "created_then_assigned": dict(created_by=me.id, assigned_to=other.id, assigned_at=func.now()),
                }
                leads = {}
                for name, kw in specs.items():
                    lead = _lead(kw.pop("created_by"), business_name=f"{marker} {name}", name_norm=marker, **kw)
                    db.add(lead)
                    await db.flush()
                    leads[name] = lead
                    for rows in _rows_for(lead, other).values():
                        db.add_all(rows)
                await db.commit()
                return me.id, {k: (v.id, v.created_by, v.assigned_to) for k, v in leads.items()}

        async def _check(me_id, leads):
            async with SessionLocal() as db:
                scopes = [P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE]
                for size in range(len(scopes) + 1):
                    for subset in itertools.combinations(scopes, size):
                        ctx = StaffContext({"role": "jaz_staff"}, me_id, False, (), frozenset(subset))
                        expected = {lid for lid, created_by, assigned_to in leads.values() if can_see(ctx, created_by=created_by, assigned_to=assigned_to)}
                        for lister, rows_filter, extra in (
                            (calls_repo.list_rows, CallFilters(q=marker), {}),
                            (followups_repo.list_rows, FollowupFilters(q=marker), {"sort": "due_at"}),
                            (demos_repo.list_rows, DemoFilters(q=marker), {}),
                            (trials_repo.list_rows, TrialFilters(q=marker), {"sort": "expected_end_at"}),
                        ):
                            rows, total = await lister(db, visibility=lead_visibility(ctx), filters=rows_filter, descending=False, limit=100, offset=0, **extra)
                            seen = {r.lead.id for r in rows}
                            assert seen == expected and total == len(rows), (subset, lister.__module__)
                            if not subset:
                                assert seen == set()                                                            # no scope at all reaches nothing (fail closed)

        me_id, leads = run(_setup())
        run(_check(me_id, leads))


# =============================================================================
class TestLocks:
    """The row locks the services rely on: a lead being worked cannot be archived / reassigned / re-staged meanwhile, an
    item being changed cannot be changed by somebody else meanwhile, and an assignee cannot be deactivated mid-write."""

    async def _setup(self):
        async with SessionLocal() as db:
            user = await _user(db)
            lead = _lead(user.id, assigned_to=user.id, assigned_at=func.now())
            db.add(lead)
            await db.flush()
            rows = _rows_for(lead, user)
            for r in rows.values():
                db.add_all(r)
            await db.commit()
            return user.id, lead.id, rows["followup"][0].id

    def test_the_shared_lead_lock_excludes_exclusive_changes_but_not_other_shared_readers(self):
        async def _run():
            _, lead_id, _ = await self._setup()
            async with SessionLocal() as a, SessionLocal() as b:
                assert (await work_repo.get_lead_shared(a, lead_id)).id == lead_id                              # A: logging work on the lead
                await b.execute(select(SalesLead.id).where(SalesLead.id == lead_id).with_for_update(read=True, nowait=True))   # B: another activity: fine
                await b.rollback()
                with pytest.raises(DBAPIError) as exc:                                                          # B: archive / assign / stage change: must wait
                    await b.execute(select(SalesLead.id).where(SalesLead.id == lead_id).with_for_update(nowait=True))
                assert "could not obtain lock" in str(exc.value)
                await b.rollback()
                await a.rollback()                                                                              # A finished ...
                await b.execute(select(SalesLead.id).where(SalesLead.id == lead_id).with_for_update(nowait=True))              # ... now B may
                await b.rollback()
        run(_run())

    def test_the_item_lock_serialises_two_changes_to_one_item(self):
        async def _run():
            _, _, followup_id = await self._setup()
            async with SessionLocal() as a, SessionLocal() as b:
                item = await followups_repo.get(a, followup_id, for_update=True)
                assert item is not None
                with pytest.raises(DBAPIError) as exc:
                    await b.execute(select(SalesFollowup.id).where(SalesFollowup.id == followup_id).with_for_update(nowait=True))
                assert "could not obtain lock" in str(exc.value)
                await b.rollback()
                await a.rollback()
        run(_run())

    def test_the_locked_read_sees_what_the_other_transaction_committed(self):
        """for_update re-reads the row (populate_existing): a status check made after the wait sees the truth."""
        async def _run():
            _, _, followup_id = await self._setup()
            async with SessionLocal() as a:
                stale = await followups_repo.get(a, followup_id)                                                # A already holds it in its session ...
                assert stale.status == "pending"
                async with SessionLocal() as b:                                                                 # ... B completes it and commits
                    await b.execute(update(SalesFollowup).where(SalesFollowup.id == followup_id).values(status="completed", completed_at=func.now()))
                    await b.commit()
                fresh = await followups_repo.get(a, followup_id, for_update=True)
                assert fresh is stale and fresh.status == "completed"                                           # same object, refreshed - not the stale copy
                await a.rollback()
        run(_run())

    def test_a_staff_account_read_for_assignment_cannot_be_deactivated_until_the_write_commits(self):
        async def _run():
            user_id, _, _ = await self._setup()
            async with SessionLocal() as a, SessionLocal() as b:
                assert (await work_repo.get_usable_staff_user(a, user_id, lock=True)).id == user_id
                with pytest.raises(DBAPIError) as exc:
                    await b.execute(select(User.id).where(User.id == user_id).with_for_update(nowait=True))    # what an UPDATE of the account needs
                assert "could not obtain lock" in str(exc.value)
                await b.rollback()
                await a.rollback()
        run(_run())

    def test_only_usable_staff_accounts_are_found(self):
        async def _run():
            async with SessionLocal() as db:
                active = await _user(db)
                inactive = await _user(db, status="inactive")
                customer = await _user(db, role="company_owner")
                deleted = await _user(db)
                deleted.deleted_at = now()
                await db.flush()
                assert (await work_repo.get_usable_staff_user(db, active.id)).id == active.id
                for u in (inactive, customer, deleted):
                    assert await work_repo.get_usable_staff_user(db, u.id) is None
                assert await work_repo.get_usable_staff_user(db, uuid.uuid4()) is None
                await db.rollback()
        run(_run())


# =============================================================================
class TestAssigneeEligibility:
    """`require_assignee` is what stops a follow-up / demo going to somebody who could not do it. Each condition is
    tested ALONE: a Super Admin normally fails by having no role grants, so the platform-role check would go untested if
    every account here were role-less - these accounts hold every grant and fail on the one property being tested."""

    def test_each_condition_alone_makes_an_account_ineligible(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _ = await _staff(db, PHASE3_PERMISSIONS + [P.PERM_LEADS_SCOPE_ALL])
                lead = _lead(creator.id)
                db.add(lead)
                await db.flush()

                async def refused(user_id, permission=P.PERM_FOLLOWUPS_MANAGE) -> str:
                    try:
                        await work_service.require_assignee(db, lead, user_id, permission=permission)
                    except HTTPException as exc:
                        return exc.detail["code"]
                    return "eligible"

                grants = PHASE3_PERMISSIONS + [P.PERM_LEADS_SCOPE_ALL]
                user, _ = await _staff(db, grants)
                assert await refused(user.id) == "eligible"                                                    # the control: everything in place

                user.role = "employee"                                                                          # holds every grant, but is a customer-side account
                await db.flush()
                assert await refused(user.id) == "activity_assignee_not_eligible"
                user.role = "super_admin"                                                                       # ... or the platform owner holding staff grants
                await db.flush()
                assert await refused(user.id) == "activity_assignee_not_eligible"
                user.role = "jaz_staff"
                await db.flush()
                assert await refused(user.id) == "eligible"

                user.status = "inactive"                                                                        # deactivated
                await db.flush()
                assert await refused(user.id) == "activity_assignee_not_eligible"
                user.status = "active"
                user.deleted_at = now()                                                                         # soft-deleted
                await db.flush()
                assert await refused(user.id) == "activity_assignee_not_eligible"
                user.deleted_at = None
                await db.flush()
                assert await refused(user.id) == "eligible"

                viewer, _ = await _staff(db, [P.PERM_FOLLOWUPS_VIEW, P.PERM_DEMOS_VIEW, P.PERM_LEADS_SCOPE_ALL])   # may look at follow-ups, not do them
                assert await refused(viewer.id) == "activity_assignee_not_eligible"
                blind, _ = await _staff(db, PHASE3_PERMISSIONS)                                                # may do them, but cannot see this lead
                assert await refused(blind.id) == "activity_assignee_cannot_see_lead"
                assert await refused(uuid.uuid4()) == "activity_assignee_not_eligible"                          # a made-up id
                await db.rollback()
        run(_run())


class TestAFailingTimelineWriterRollsBackTheChange:
    """Every write records its timeline event through the caller's own session, so the two commit or roll back together.
    Here the writer is made to fail: nothing of the change may remain - and the same operation must succeed (with exactly
    one event) once the writer works, so the failure above was the writer and nothing else."""

    @staticmethod
    async def _in_request(fn):
        """Drive the REAL get_db dependency the way FastAPI does: commit on success, rollback + re-raise on error."""
        gen = get_db()
        db = await gen.__anext__()
        try:
            result = await fn(db)
        except BaseException as exc:
            await gen.athrow(exc)
            raise
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        return result

    @staticmethod
    async def _world():
        async with SessionLocal() as db:
            staff, ctx = await _staff(db, PHASE3_PERMISSIONS + [P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_ASSIGN])
            busy, empty = _lead(staff.id), _lead(staff.id)                     # `busy` holds one open item of each kind, `empty` none (for the create operations)
            db.add_all([busy, empty])
            await db.flush()
            rows = _rows_for(busy, staff)
            for r in rows.values():
                db.add_all(r)
            await db.commit()
            return SimpleNamespace(
                ctx=ctx, busy=busy.id, empty=empty.id, call=rows["call"][0].id, followup=rows["followup"][0].id, demo=rows["demo"][0].id, trial=rows["trial"][0].id,
            )

    @staticmethod
    async def _fingerprint(w):
        async with SessionLocal() as db:
            state = []
            for model in TABLES:
                rows = (await db.execute(select(model).where(model.lead_id.in_([w.busy, w.empty])).order_by(model.id))).scalars().all()
                state.append([tuple(getattr(r, c.name) for c in model.__table__.columns) for r in rows])
            events = (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(SalesLeadActivity.lead_id.in_([w.busy, w.empty])))).scalar_one()
            return state, events

    OPERATIONS = {
        "create_call": lambda db, w, a: calls_service.create_call(db, w.ctx, S.CallCreate(lead_id=w.empty, result="answered"), a),
        "update_call": lambda db, w, a: calls_service.update_call(db, w.ctx, str(w.call), S.CallUpdate(result="answered"), a),
        "create_followup": lambda db, w, a: followups_service.create_followup(db, w.ctx, S.FollowupCreate(lead_id=w.empty, due_at=now() + timedelta(days=2)), a),
        "update_followup": lambda db, w, a: followups_service.update_followup(db, w.ctx, str(w.followup), S.FollowupUpdate(notes="changed"), a),
        "complete_followup": lambda db, w, a: followups_service.complete_followup(db, w.ctx, str(w.followup), "done", a),
        "cancel_followup": lambda db, w, a: followups_service.cancel_followup(db, w.ctx, str(w.followup), "no longer needed", a),
        "schedule_demo": lambda db, w, a: demos_service.schedule_demo(db, w.ctx, S.DemoCreate(lead_id=w.empty, scheduled_at=now() + timedelta(days=2)), a),
        "update_demo": lambda db, w, a: demos_service.update_demo(db, w.ctx, str(w.demo), S.DemoUpdate(notes="changed"), a),
        "reschedule_demo": lambda db, w, a: demos_service.reschedule_demo(db, w.ctx, str(w.demo), S.DemoReschedule(scheduled_at=now() + timedelta(days=9)), a),
        "complete_demo": lambda db, w, a: demos_service.complete_demo(db, w.ctx, str(w.demo), "went well", a),
        "cancel_demo": lambda db, w, a: demos_service.cancel_demo(db, w.ctx, str(w.demo), None, a),
        "no_show_demo": lambda db, w, a: demos_service.mark_no_show(db, w.ctx, str(w.demo), None, a),
        "start_trial": lambda db, w, a: trials_service.start_trial(db, w.ctx, S.TrialCreate(lead_id=w.empty, expected_end_at=now() + timedelta(days=14)), a),
        "update_trial": lambda db, w, a: trials_service.update_trial(db, w.ctx, str(w.trial), S.TrialUpdate(notes="changed"), a),
        "complete_trial": lambda db, w, a: trials_service.complete_trial(db, w.ctx, str(w.trial), S.TrialComplete(), a),
        "cancel_trial": lambda db, w, a: trials_service.cancel_trial(db, w.ctx, str(w.trial), "stopped", a),
    }

    @pytest.mark.parametrize("operation", list(OPERATIONS))
    def test_operation(self, operation, monkeypatch):
        async def _run():
            w = await self._world()
            audit = audit_service.new_audit_context("atomicity")
            before = await self._fingerprint(w)
            assert before[1] == 0

            async def broken_writer(*args, **kwargs):
                raise RuntimeError("timeline store unavailable")
            monkeypatch.setattr(activity_service, "record", broken_writer)
            with pytest.raises(RuntimeError, match="timeline store unavailable"):
                await self._in_request(lambda db: self.OPERATIONS[operation](db, w, audit))
            monkeypatch.undo()
            assert await self._fingerprint(w) == before                              # not one row, one column or one event changed

            await self._in_request(lambda db: self.OPERATIONS[operation](db, w, audit))
            state, events = await self._fingerprint(w)
            assert state != before[0] and events == 1                                # the same operation now commits: the change AND exactly one event

        run(_run())



# =============================================================================
class TestModelMatchesDatabase:
    """The SQLAlchemy models mirror the migration: every declared column, CHECK, unique constraint and index exists in the
    migrated scratch database (and nothing NOT NULL in the DB is nullable in the model, or vice versa)."""

    def test_columns_constraints_and_indexes_exist(self):
        def _reflect(sync_conn):
            insp = inspect(sync_conn)
            out = {}
            for model in TABLES:
                name = model.__tablename__
                names = lambda sql: {r[0] for r in sync_conn.execute(text(sql), {"t": name})}  # noqa: E731
                out[name] = {
                    "columns": {c["name"]: c["nullable"] for c in insp.get_columns(name)},
                    "checks": names("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"),
                    "fks": names("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"),
                    "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t"),
                }
            return out

        async def _run():
            async with engine.connect() as conn:
                reflected = await conn.run_sync(_reflect)
            for model in TABLES:
                db_table, declared = reflected[model.__tablename__], model.__table__
                assert set(db_table["columns"]) == {c.name for c in declared.columns}, model.__tablename__
                for column in declared.columns:
                    assert db_table["columns"][column.name] == column.nullable, (model.__tablename__, column.name)
                declared_checks = {c.name for c in declared.constraints if c.name and c.name.startswith("ck_")}
                assert declared_checks == {n for n in db_table["checks"] if n.startswith("ck_")}, model.__tablename__     # the same set, both ways
                assert {i.name for i in declared.indexes} <= db_table["indexes"], {i.name for i in declared.indexes} - db_table["indexes"]
                assert len(declared.foreign_keys) == len(db_table["fks"]), model.__tablename__

        run(_run())

    def test_the_lead_and_user_foreign_keys_of_every_table(self):
        async def _run():
            async with SessionLocal() as db:
                rows = (await db.execute(text(
                    "SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype::text FROM pg_constraint "
                    "WHERE contype = 'f' AND conrelid::regclass::text = ANY(:tables)"
                ), {"tables": [m.__tablename__ for m in TABLES]})).all()
                to_leads = {(t, d) for t, ref, d in rows if ref == "sales_leads"}
                assert to_leads == {(m.__tablename__, "r") for m in TABLES}                                               # RESTRICT ('r') on every lead FK
                assert all(d == "a" for t, ref, d in rows if ref == "users")                                              # NO ACTION ('a') on every user FK, like all others
        run(_run())
