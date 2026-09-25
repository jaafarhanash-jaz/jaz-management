"""JAZ Sales - Phase 4: customers, onboarding and conversion at the DATABASE level.

Three things live in the schema and are checked here directly, not through the API: the constraints that make bad states
unrepresentable (one customer per lead and per company, an owner exactly when an onboarding has left `won`, a completion
time exactly while it is `activated`, RESTRICT foreign keys), the atomicity of every write (company, owner account,
customer, onboarding and timeline event commit or roll back together - proved by making the timeline / audit writer fail
under the REAL request transaction), and the queries themselves (constant statement counts, row locks, scope predicates).
Direct-DB helpers create rows only in the disposable scratch database (sales_test_utils.assert_scratch_target).
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from database import SessionLocal, engine, get_db
from models import Company, SubscriptionPlan, User
from sales import customer_schemas as S
from sales import permissions as P
from sales.constants import (
    CUSTOMER_STATUSES,
    ONBOARDING_STAGES,
    customer_status_for_stage,
)
from sales.models import (
    SalesCustomer,
    SalesLead,
    SalesLeadActivity,
    SalesOnboarding,
    StaffAuditEvent,
    StaffRole,
    StaffRolePermission,
    StaffUserRole,
)
from sales.repositories import customers as customers_repo
from sales.repositories import onboarding as onboarding_repo
from sales.repositories.customers import CustomerFilters
from sales.repositories.onboarding import OnboardingFilters
from sales.services import activity as activity_service
from sales.services import audit as audit_service
from sales.services import conversion as conversion_service
from sales.services import customers as customers_service
from sales.services import onboarding as onboarding_service
from sales.services.access import StaffContext
from sales.services.lead_access import lead_visibility
from sales.services.onboarding_access import can_see_onboarding, onboarding_visibility

TABLES = (SalesCustomer, SalesOnboarding)
MANAGER_KEYS = [
    P.PERM_CUSTOMERS_VIEW, P.PERM_CUSTOMERS_CONVERT, P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_ASSIGN,
    P.PERM_ONBOARDING_SCOPE_ALL, P.PERM_LEADS_VIEW, P.PERM_LEADS_SCOPE_ALL,
]
WORKER_KEYS = [P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_SCOPE_ASSIGNED]


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
        id=uuid.uuid4(), email=f"p4db-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1777{random.randint(1000000, 9999999)}",
        password="not-a-real-hash", name="Phase4 DB Test", role=role, status=status,
    )
    db.add(user)
    await db.flush()
    return user


async def _staff(db, permissions, *, status="active"):
    """An account holding `permissions` through a real (throwaway) role, and its StaffContext."""
    user = await _user(db, status=status)
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
    return user, ctx, role


def _lead(created_by, *, won=False, assignee=None, **kw) -> SalesLead:
    token = uuid.uuid4().hex[:10]
    values = dict(id=uuid.uuid4(), created_by=created_by, business_name=f"P4 DB {token}", name_norm=f"p4 db {token}")
    if won:
        values.update(pipeline_stage="won", closed_at=now(), assigned_to=assignee or created_by, assigned_at=now())
    values.update(kw)
    return SalesLead(**values)


async def _plan_id(db) -> uuid.UUID:
    """An active subscription plan. A database restored from a SCHEMA-ONLY dump (the PostgreSQL 17 rehearsal) has none, so
    the tests create one instead of relying on seeded data."""
    found = (await db.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.name).limit(1))).scalar_one_or_none()
    if found is not None:
        return found
    plan = SubscriptionPlan(id=uuid.uuid4(), name=f"P4 DB plan {uuid.uuid4().hex[:8]}", max_employees=5, price=1, duration_months=1, features=[], is_active=True)
    db.add(plan)
    await db.flush()
    return plan.id


async def _body(db, lead, **kw) -> S.ConvertLead:
    values = dict(
        business_name=lead.business_name, owner_name="Owner", owner_email=f"owner-{uuid.uuid4().hex[:10]}@p4db.example.com",
        owner_phone=f"+1888{random.randint(1000000, 9999999)}", owner_password="Owner#Pass-2026", subscription_plan_id=await _plan_id(db),
    )
    values.update(kw)
    return S.ConvertLead(**values)


async def _company(db) -> Company:
    """A minimal company row (direct insert - the conversion tests use the real service) with its own owner account."""
    owner = await _user(db, role="company_owner")
    company = Company(id=uuid.uuid4(), name=f"P4 Co {uuid.uuid4().hex[:8]}", owner_id=owner.id, qr_code="x", qr_token="t")
    db.add(company)
    await db.flush()
    return company


async def _customer(db, creator, *, worker=None, stage="won") -> SimpleNamespace:
    """A lead (won), a company, a customer and its onboarding - built directly. `worker` owns the onboarding (needed
    for every stage but `won`)."""
    lead = _lead(creator.id, won=True)
    db.add(lead)
    await db.flush()
    company = await _company(db)
    customer = SalesCustomer(id=uuid.uuid4(), lead_id=lead.id, company_id=company.id, converted_by=creator.id, status=customer_status_for_stage(stage))
    db.add(customer)
    await db.flush()
    onboarding = SalesOnboarding(
        id=uuid.uuid4(), customer_id=customer.id, stage=stage, assigned_to=worker.id if worker is not None else None,
        completed_at=now() if stage == "activated" else None,
    )
    db.add(onboarding)
    await db.flush()
    return SimpleNamespace(lead=lead, company=company, customer=customer, onboarding=onboarding)


async def _violates(db, constraint: str, statement) -> None:
    """Running `statement` (an awaitable factory) must fail on exactly `constraint`, leaving the session usable."""
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        async with db.begin_nested():
            await statement()
    assert f'"{constraint}"' in str(exc.value), f"expected {constraint}, got: {str(exc.value)[:300]}"


# =============================================================================
class TestCustomerConstraints:
    def test_status_is_one_of_the_three(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                made = await _customer(db, creator)
                for status in CUSTOMER_STATUSES:
                    made.customer.status = status
                    await db.flush()
                async def bad():
                    made.customer.status = "archived"
                    await db.flush()
                await _violates(db, "ck_sales_customers_status", bad)
                await db.rollback()
        run(_run())

    def test_one_customer_per_lead_and_one_per_company(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                made = await _customer(db, creator)
                other_lead = _lead(creator.id, won=True)
                other_company = await _company(db)
                db.add(other_lead)
                await db.flush()

                async def same_lead():
                    db.add(SalesCustomer(id=uuid.uuid4(), lead_id=made.lead.id, company_id=other_company.id, converted_by=creator.id))
                    await db.flush()

                async def same_company():
                    db.add(SalesCustomer(id=uuid.uuid4(), lead_id=other_lead.id, company_id=made.company.id, converted_by=creator.id))
                    await db.flush()

                await _violates(db, "uq_sales_customers_lead", same_lead)
                await _violates(db, "uq_sales_customers_company", same_company)
                await db.rollback()
        run(_run())

    def test_a_lead_a_company_or_a_customer_that_is_referenced_cannot_be_deleted(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                made = await _customer(db, creator)
                for table, key, value in ((SalesLead, "id", made.lead.id), (Company, "id", made.company.id), (SalesCustomer, "id", made.customer.id)):
                    with pytest.raises(IntegrityError) as exc:
                        async with db.begin_nested():
                            await db.execute(delete(table).where(getattr(table, key) == value))
                    assert "foreign key" in str(exc.value).lower(), table.__tablename__
                await db.rollback()
        run(_run())

    def test_the_columns_have_their_defaults(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                lead, company = _lead(creator.id, won=True), await _company(db)
                db.add(lead)
                await db.flush()
                row = SalesCustomer(id=uuid.uuid4(), lead_id=lead.id, company_id=company.id, converted_by=creator.id)
                db.add(row)
                await db.flush()
                await db.refresh(row)
                assert row.status == "active" and row.converted_at is not None and row.created_at and row.updated_at
                await db.rollback()
        run(_run())


class TestOnboardingConstraints:
    def test_stage_is_one_of_the_eight(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                made = await _customer(db, creator, worker=worker, stage="assigned")
                for stage in ONBOARDING_STAGES[1:-1]:
                    made.onboarding.stage = stage
                    await db.flush()
                async def bad():
                    made.onboarding.stage = "finished"
                    await db.flush()
                await _violates(db, "ck_sales_onboarding_stage", bad)
                await db.rollback()
        run(_run())

    def test_an_owner_exactly_when_the_stage_is_not_won(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                unowned = await _customer(db, creator)                                                       # won, nobody
                owned = await _customer(db, creator, worker=worker, stage="assigned")

                async def owner_at_won():
                    unowned.onboarding.assigned_to = worker.id
                    await db.flush()

                async def no_owner_past_won():
                    owned.onboarding.assigned_to = None
                    await db.flush()

                await _violates(db, "ck_sales_onboarding_owner_pair", owner_at_won)
                await _violates(db, "ck_sales_onboarding_owner_pair", no_owner_past_won)
                await db.rollback()
        run(_run())

    def test_a_completion_time_exactly_while_activated(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                working = await _customer(db, creator, worker=worker, stage="training")
                done = await _customer(db, creator, worker=worker, stage="activated")

                async def activated_without_time():
                    working.onboarding.stage = "activated"
                    await db.flush()

                async def time_without_activation():
                    working.onboarding.completed_at = now()
                    await db.flush()

                async def reopened_but_still_timed():
                    done.onboarding.stage = "training"
                    await db.flush()

                await _violates(db, "ck_sales_onboarding_completed_pair", activated_without_time)
                await db.refresh(working.onboarding)
                await _violates(db, "ck_sales_onboarding_completed_pair", time_without_activation)
                await db.refresh(done.onboarding)
                await _violates(db, "ck_sales_onboarding_completed_pair", reopened_but_still_timed)
                await db.rollback()
        run(_run())

    def test_completion_cannot_precede_the_start(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                made = await _customer(db, creator, worker=worker, stage="activated")

                async def before_start():
                    made.onboarding.completed_at = made.onboarding.started_at - timedelta(seconds=1)
                    await db.flush()

                await _violates(db, "ck_sales_onboarding_completed_after_start", before_start)
                await db.rollback()
        run(_run())

    def test_the_notes_limit_and_one_onboarding_per_customer(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                made = await _customer(db, creator)
                made.onboarding.notes = "n" * 5000
                await db.flush()

                async def too_long():
                    made.onboarding.notes = "n" * 5001
                    await db.flush()

                async def second_onboarding():
                    db.add(SalesOnboarding(id=uuid.uuid4(), customer_id=made.customer.id))
                    await db.flush()

                await _violates(db, "ck_sales_onboarding_notes", too_long)
                await _violates(db, "uq_sales_onboarding_customer", second_onboarding)
                await db.rollback()
        run(_run())

    def test_the_defaults_start_an_unowned_record_at_won(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                made = await _customer(db, creator)
                await db.refresh(made.onboarding)
                o = made.onboarding
                assert (o.stage, o.assigned_to, o.notes, o.completed_at) == ("won", None, None, None) and o.started_at and o.created_at and o.updated_at
                await db.rollback()
        run(_run())


# =============================================================================
class TestTimelineAcceptsThePhase4Events:
    def test_every_event_type_is_stored_read_back_and_immutable(self):
        async def _run():
            async with SessionLocal() as db:
                creator, ctx, _ = await _staff(db, MANAGER_KEYS)
                lead = _lead(creator.id, won=True)
                db.add(lead)
                await db.flush()
                audit = audit_service.new_audit_context("p4-db")
                for event_type in ("lead_converted", "onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated"):
                    await activity_service.record(db, ctx, audit, lead.id, event_type, before={"stage": "won"}, after={"stage": "assigned"}, note="n", metadata={"onboarding_id": str(uuid.uuid4())})
                page = await activity_service.list_events(db, lead.id, 50, 0, activity_service.visible_event_types(ctx))
                assert [e["event_type"] for e in page["items"]] == ["onboarding_notes_updated", "onboarding_stage_changed", "onboarding_assigned", "lead_converted"]
                only_onboarding = await activity_service.list_events(db, lead.id, 50, 0, activity_service.ONBOARDING_EVENT_TYPES)
                assert only_onboarding["total"] == 3 and all(e["event_type"].startswith("onboarding_") for e in only_onboarding["items"])
                event_id = page["items"][0]["id"]
                with pytest.raises(DBAPIError) as exc:
                    async with db.begin_nested():
                        await db.execute(update(SalesLeadActivity).where(SalesLeadActivity.id == uuid.UUID(event_id)).values(note="edited"))
                assert "append-only" in str(exc.value)
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(delete(SalesLeadActivity).where(SalesLeadActivity.id == uuid.UUID(event_id)))
                await db.rollback()
        run(_run())

    def test_a_secret_can_never_be_written_into_an_event(self):
        async def _run():
            async with SessionLocal() as db:
                creator, ctx, _ = await _staff(db, MANAGER_KEYS)
                lead = _lead(creator.id, won=True)
                db.add(lead)
                await db.flush()
                for key in ("password", "owner_password", "password_hash", "token"):
                    with pytest.raises(ValueError):
                        await activity_service.record(db, ctx, audit_service.new_audit_context(), lead.id, "lead_converted", after={key: "x"})
                await db.rollback()
        run(_run())


# =============================================================================
class TestAssigneeEligibility:
    """`_require_assignee` is what stops an onboarding going to somebody who could not work it. Each condition is tested
    ALONE: these accounts hold every grant but one, and fail on the one property being tested."""

    def test_each_condition_alone_makes_an_account_ineligible(self):
        async def _run():
            async with SessionLocal() as db:
                async def eligible(user_id) -> bool:
                    return (await onboarding_repo.get_assignable_user(db, user_id)) is not None

                user, _, role = await _staff(db, WORKER_KEYS)
                assert await eligible(user.id)                                                              # the control: everything in place

                user.role = "employee"                                                                      # a customer-side account with every grant
                await db.flush()
                assert not await eligible(user.id)
                user.role = "super_admin"
                await db.flush()
                assert not await eligible(user.id)
                user.role = "jaz_staff"
                user.status = "inactive"                                                                    # deactivated
                await db.flush()
                assert not await eligible(user.id)
                user.status = "active"
                user.deleted_at = now()                                                                     # soft-deleted
                await db.flush()
                assert not await eligible(user.id)
                user.deleted_at = None
                await db.flush()
                assert await eligible(user.id)

                role.is_active = False                                                                      # a retired role grants nothing
                await db.flush()
                assert not await eligible(user.id)
                role.is_active = True
                grant = (await db.execute(select(StaffUserRole).where(StaffUserRole.user_id == user.id))).scalar_one()
                grant.revoked_at, grant.revoked_by = now(), user.id                                         # a revoked grant grants nothing
                await db.flush()
                assert not await eligible(user.id)

                for keys in ([P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_SCOPE_ASSIGNED],                    # cannot WORK it
                             [P.PERM_ONBOARDING_MANAGE],                                                    # will not SEE it
                             [P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_SCOPE_ALL],                       # scope_all is not the assigned scope
                             [P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_VIEW, P.PERM_LEADS_UPDATE],         # a Sales Employee
                             []):
                    other, _, _ = await _staff(db, keys)
                    assert not await eligible(other.id), keys
                assert not await eligible(uuid.uuid4())                                                     # a made-up id
                await db.rollback()
        run(_run())

    def test_the_two_permissions_may_come_from_two_different_roles(self):
        async def _run():
            async with SessionLocal() as db:
                user, _, _ = await _staff(db, [P.PERM_ONBOARDING_MANAGE])
                second = StaffRole(id=uuid.uuid4(), module="sales", key=f"t_{uuid.uuid4().hex[:10]}", name_en="t", name_ar="t")
                db.add(second)
                await db.flush()
                db.add(StaffRolePermission(role_id=second.id, permission_key=P.PERM_ONBOARDING_SCOPE_ASSIGNED))
                await db.flush()
                assert (await onboarding_repo.get_assignable_user(db, user.id)) is None
                db.add(StaffUserRole(id=uuid.uuid4(), user_id=user.id, role_id=second.id, granted_by=user.id))
                await db.flush()
                assert (await onboarding_repo.get_assignable_user(db, user.id)) is not None                 # permissions are the UNION of the roles
                await db.rollback()
        run(_run())

    def test_the_assignee_list_is_the_same_set_with_the_open_workload(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                gone, _, _ = await _staff(db, WORKER_KEYS, status="inactive")
                await _customer(db, creator, worker=worker, stage="training")
                await _customer(db, creator, worker=worker, stage="activated")                             # finished: not workload
                listed = {u.id: n for u, n in await onboarding_repo.list_assignees(db)}
                assert listed[worker.id] == 1 and gone.id not in listed and creator.id not in listed
                await db.rollback()
        run(_run())


# =============================================================================
class TestAtomicity:
    """Every write records its timeline event through the caller's own session, so the two commit or roll back together.
    The writer is made to fail under the REAL request transaction (get_db): nothing of the change may remain - and the
    same operation must succeed (with exactly one event) once the writer works, so the failure was the writer alone."""

    @staticmethod
    async def _in_request(fn):
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
            manager, ctx, _ = await _staff(db, MANAGER_KEYS)
            worker, _, _ = await _staff(db, WORKER_KEYS)
            other, _, _ = await _staff(db, WORKER_KEYS)
            lead = _lead(manager.id, won=True)                                                              # for the conversion
            db.add(lead)
            made = await _customer(db, manager, worker=worker, stage="assigned")                            # for the onboarding operations
            unowned = await _customer(db, manager)
            body = await _body(db, lead)
            await db.commit()
            return SimpleNamespace(
                ctx=ctx, lead=lead.id, body=body, onboarding=made.onboarding.id, customer=made.customer.id, lead_of_customer=made.lead.id,
                unowned=unowned.onboarding.id, unowned_lead=unowned.lead.id, other=other.id, worker=worker.id,
            )

    @staticmethod
    async def _fingerprint(w):
        """Everything this test's world can change, and only that (other test workers create companies and users at the
        same time, so nothing global is compared): the customer / onboarding rows of its leads, the timeline and audit
        events of those leads, and the company and owner account the conversion would create."""
        leads = [w.lead, w.lead_of_customer, w.unowned_lead]
        async with SessionLocal() as db:
            customers = (await db.execute(select(SalesCustomer).where(SalesCustomer.lead_id.in_(leads)).order_by(SalesCustomer.id))).scalars().all()
            onboardings = (await db.execute(select(SalesOnboarding).where(SalesOnboarding.customer_id.in_([c.id for c in customers])).order_by(SalesOnboarding.id))).scalars().all()
            rows = [[tuple(getattr(r, c.name) for c in r.__table__.columns) for r in group] for group in (customers, onboardings)]
            events = (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(SalesLeadActivity.lead_id.in_(leads)))).scalar_one()
            audits = (await db.execute(select(func.count()).select_from(StaffAuditEvent).where(
                StaffAuditEvent.action == "customer_converted", StaffAuditEvent.after_data["lead_id"].astext == str(w.lead)))).scalar_one()
            companies = (await db.execute(select(func.count()).select_from(Company).where(Company.name == w.body.business_name))).scalar_one()
            owners = (await db.execute(select(func.count()).select_from(User).where(User.email == w.body.owner_email))).scalar_one()
            return {"rows": rows, "events": events, "audits": audits, "companies": companies, "owners": owners}

    OPERATIONS = {
        "assign": lambda db, w, a: onboarding_service.assign_onboarding(db, w.ctx, str(w.onboarding), S.OnboardingAssign(assigned_to=w.other, note="handing over"), a),
        "first_assign": lambda db, w, a: onboarding_service.assign_onboarding(db, w.ctx, str(w.unowned), S.OnboardingAssign(assigned_to=w.worker), a),
        "stage": lambda db, w, a: onboarding_service.change_stage(db, w.ctx, str(w.onboarding), S.OnboardingStageChange(stage="activated", note="done"), a),
        "notes": lambda db, w, a: onboarding_service.update_onboarding(db, w.ctx, str(w.onboarding), S.OnboardingUpdate(notes="changed"), a),
    }

    @pytest.mark.parametrize("operation", list(OPERATIONS))
    def test_onboarding_operation(self, operation, monkeypatch):
        async def _run():
            w = await self._world()
            audit = audit_service.new_audit_context("atomicity")
            before = await self._fingerprint(w)

            async def broken_writer(*args, **kwargs):
                raise RuntimeError("timeline store unavailable")
            monkeypatch.setattr(activity_service, "record", broken_writer)
            with pytest.raises(RuntimeError, match="timeline store unavailable"):
                await self._in_request(lambda db: self.OPERATIONS[operation](db, w, audit))
            monkeypatch.undo()
            assert await self._fingerprint(w) == before                                                     # not one row, column or event changed

            await self._in_request(lambda db: self.OPERATIONS[operation](db, w, audit))
            after = await self._fingerprint(w)
            assert after["rows"] != before["rows"] and after["events"] == before["events"] + 1               # the same operation now commits: the change AND one event
        run(_run())

    @pytest.mark.parametrize("failing", ["timeline", "audit"])
    def test_a_conversion_leaves_nothing_behind_when_its_trail_cannot_be_written(self, failing, monkeypatch):
        async def _run():
            w = await self._world()
            audit = audit_service.new_audit_context("atomicity")
            before = await self._fingerprint(w)

            async def broken_writer(*args, **kwargs):
                raise RuntimeError("trail unavailable")
            target = activity_service if failing == "timeline" else audit_service
            monkeypatch.setattr(target, "record", broken_writer)
            with pytest.raises(RuntimeError, match="trail unavailable"):
                await self._in_request(lambda db: conversion_service.convert_lead(db, w.ctx, str(w.lead), w.body, audit))
            monkeypatch.undo()
            assert await self._fingerprint(w) == before                                                     # no company, no owner ACCOUNT, no customer, no onboarding

            result = await self._in_request(lambda db: conversion_service.convert_lead(db, w.ctx, str(w.lead), w.body, audit))
            assert result["already_converted"] is False
            after = await self._fingerprint(w)
            assert (after["events"], after["audits"], after["companies"], after["owners"]) == (before["events"] + 1, 1, 1, 1)
            assert len(after["rows"][0]) == len(before["rows"][0]) + 1 and len(after["rows"][1]) == len(before["rows"][1]) + 1     # a customer AND its onboarding
        run(_run())

    def test_a_failure_after_the_company_was_created_still_rolls_the_company_back(self, monkeypatch):
        """The company (and its owner account) is created BEFORE the customer row: make the customer insert fail and check the
        company is not left behind."""
        async def _run():
            w = await self._world()
            audit = audit_service.new_audit_context("atomicity")
            before = await self._fingerprint(w)
            # a customer already exists for this lead behind the service's back (as a racing writer would leave it)
            async with SessionLocal() as db:
                company = await _company(db)
                db.add(SalesCustomer(id=uuid.uuid4(), lead_id=w.lead, company_id=company.id, converted_by=w.ctx.user_id))
                await db.commit()
            before = await self._fingerprint(w)

            async def blind(db, lead_id):
                return None
            monkeypatch.setattr(customers_repo, "get_row_by_lead", blind)                                 # the idempotency read misses it
            with pytest.raises(Exception) as exc:
                await self._in_request(lambda db: conversion_service.convert_lead(db, w.ctx, str(w.lead), w.body, audit))
            monkeypatch.undo()
            assert getattr(exc.value, "status_code", None) == 409 and exc.value.detail["code"] == "already_converted"     # the unique index answered, cleanly
            assert await self._fingerprint(w) == before                                                     # and the company the service had just created is gone
        run(_run())


# =============================================================================
class TestLocks:
    def test_the_onboarding_locked_read_sees_what_the_other_transaction_committed(self):
        async def _run():
            async with SessionLocal() as db:
                creator, _, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                made = await _customer(db, creator, worker=worker, stage="assigned")
                oid = made.onboarding.id
                await db.commit()

            async def hold_and_change(started, release):
                async with SessionLocal() as a:
                    row = await onboarding_repo.get(a, oid, for_update=True)
                    started.set()
                    await release.wait()
                    row.stage = "training"
                    await a.commit()

            async def wait_for_it(started):
                await started.wait()
                async with SessionLocal() as b:
                    stale = await onboarding_repo.get(b, oid)                                             # an unlocked read before the wait: assigned
                    assert stale.stage == "assigned"
                    task = asyncio.ensure_future(onboarding_repo.get(b, oid, for_update=True))
                    await asyncio.sleep(0.3)
                    assert not task.done()                                                                # it is waiting for the lock
                    release.set()
                    fresh = await task
                    return fresh.stage                                                                    # re-read AFTER the wait: the other transaction's stage

            started, release = asyncio.Event(), asyncio.Event()
            _, stage = await asyncio.gather(hold_and_change(started, release), wait_for_it(started))
            assert stage == "training"
        run(_run())

    def test_a_conversion_locks_the_lead_for_its_whole_duration(self):
        async def _run():
            async with SessionLocal() as db:
                manager, ctx, _ = await _staff(db, MANAGER_KEYS)
                lead = _lead(manager.id, won=True)
                db.add(lead)
                await db.flush()
                body = await _body(db, lead)
                await db.commit()
            async with SessionLocal() as holder:
                await holder.execute(select(SalesLead).where(SalesLead.id == lead.id).with_for_update())     # somebody else is changing the lead
                async with SessionLocal() as db:
                    await db.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    with pytest.raises(DBAPIError) as exc:
                        await conversion_service.convert_lead(db, ctx, str(lead.id), body, audit_service.new_audit_context())
                    assert "lock" in str(exc.value).lower()
                    await db.rollback()
                await holder.rollback()
        run(_run())

    def test_an_onboarding_change_locks_its_record(self):
        async def _run():
            async with SessionLocal() as db:
                manager, ctx, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                made = await _customer(db, manager, worker=worker, stage="assigned")
                oid = made.onboarding.id
                await db.commit()
            async with SessionLocal() as holder:
                await onboarding_repo.get(holder, oid, for_update=True)
                async with SessionLocal() as db:
                    await db.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    with pytest.raises(DBAPIError) as exc:
                        await onboarding_service.change_stage(db, ctx, str(oid), S.OnboardingStageChange(stage="training"), audit_service.new_audit_context())
                    assert "lock" in str(exc.value).lower()
                    await db.rollback()
                await holder.rollback()
        run(_run())

    def test_the_assignee_is_share_locked_so_it_cannot_be_deactivated_mid_assignment(self):
        async def _run():
            async with SessionLocal() as db:
                worker, _, _ = await _staff(db, WORKER_KEYS)
                await db.commit()
            async with SessionLocal() as holder:
                assert (await onboarding_repo.get_assignable_user(holder, worker.id, lock=True)) is not None
                async with SessionLocal() as other:
                    await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    with pytest.raises(DBAPIError):                                                       # a deactivation needs the row exclusively
                        await other.execute(update(User).where(User.id == worker.id).values(status="inactive"))
                    await other.rollback()
                    assert (await onboarding_repo.get_assignable_user(other, worker.id, lock=True)) is not None      # another shared reader is fine
                await holder.rollback()
        run(_run())


# =============================================================================
class TestNoNPlusOne:
    """A page of customers / onboarding records must cost a constant number of statements, however many rows it holds."""

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
                manager, ctx, _ = await _staff(db, MANAGER_KEYS)
                worker, _, _ = await _staff(db, WORKER_KEYS)
                ids = []
                for i in range(6):
                    made = await _customer(db, manager, worker=worker if i % 2 else None, stage="training" if i % 2 else "won")
                    made.lead.business_name = f"{marker} {i}"
                    ids.append(made)
                await db.commit()
                first_customer, first_onboarding = ids[0].customer.id, ids[1].onboarding.id

            statements, unlisten = self._count_statements()
            try:
                async with SessionLocal() as db:
                    page = await customers_service.list_customers(db, ctx, CustomerFilters(q=marker), sort="converted_at", descending=True, limit=25, offset=0)
                    assert page["total"] == 6 and len(page["items"]) == 6
                    assert all(i["company"]["name"] and i["converted_by"]["name"] for i in page["items"])
                    assert len(statements) == 2, statements                                              # one COUNT + one page query, every join included
                    del statements[:]
                    assert (await customers_service.status_counts(db, ctx, CustomerFilters(q=marker)))["total"] == 6 and len(statements) == 1, statements
                    del statements[:]
                    assert (await customers_service.get_customer(db, ctx, str(first_customer)))["company"]["employee_count"] == 0 and len(statements) == 2, statements
                    del statements[:]

                    page = await onboarding_service.list_onboarding(db, ctx, OnboardingFilters(q=marker), sort="started_at", descending=True, limit=25, offset=0)
                    assert page["total"] == 6 and sum(1 for i in page["items"] if i["assigned_to"] and i["assigned_to"]["name"]) == 3
                    assert len(statements) == 2, statements
                    del statements[:]
                    assert (await onboarding_service.counts(db, ctx, OnboardingFilters(q=marker)))["total"] == 6 and len(statements) == 1, statements
                    del statements[:]
                    assert (await onboarding_service.get_onboarding(db, ctx, str(first_onboarding)))["customer"]["business_name"].startswith(marker) and len(statements) == 1, statements
                    del statements[:]
                    assert len(await onboarding_service.list_assignees(db)) >= 1 and len(statements) == 1, statements
                    del statements[:]
                    timeline = await onboarding_service.list_timeline(db, ctx, str(first_onboarding), 50, 0)
                    assert timeline["total"] == 0 and len(statements) == 3, statements                     # the record + COUNT + events joined with their actor
            finally:
                unlisten()

        run(_run())


# =============================================================================
class TestScopePredicates:
    """The SQL predicate (lists) and its Python mirror (single records) must agree for every scope combination."""

    def test_onboarding_visibility_matches_can_see_onboarding(self):
        async def _run():
            async with SessionLocal() as db:
                manager, _, _ = await _staff(db, MANAGER_KEYS)
                me, _, _ = await _staff(db, WORKER_KEYS)
                someone, _, _ = await _staff(db, WORKER_KEYS)
                rows = {
                    "unowned": (await _customer(db, manager)).onboarding,
                    "mine": (await _customer(db, manager, worker=me, stage="contacted")).onboarding,
                    "theirs": (await _customer(db, manager, worker=someone, stage="contacted")).onboarding,
                    "mine_done": (await _customer(db, manager, worker=me, stage="activated")).onboarding,
                }
                for scopes in ((), (P.PERM_ONBOARDING_SCOPE_ALL,), (P.PERM_ONBOARDING_SCOPE_ASSIGNED,), (P.PERM_ONBOARDING_SCOPE_ALL, P.PERM_ONBOARDING_SCOPE_ASSIGNED),
                               (P.PERM_LEADS_SCOPE_ALL,), (P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE)):
                    ctx = StaffContext({"role": "jaz_staff"}, me.id, False, (), frozenset(scopes))
                    in_sql = set((await db.execute(select(SalesOnboarding.id).where(onboarding_visibility(ctx), SalesOnboarding.id.in_([r.id for r in rows.values()])))).scalars().all())
                    in_python = {r.id for r in rows.values() if can_see_onboarding(ctx, assigned_to=r.assigned_to)}
                    assert in_sql == in_python, scopes
                    expected = {r.id for name, r in rows.items() if P.PERM_ONBOARDING_SCOPE_ALL in scopes or (P.PERM_ONBOARDING_SCOPE_ASSIGNED in scopes and name.startswith("mine"))}
                    assert in_sql == expected, scopes                                                          # and both equal what the rule says
                await db.rollback()
        run(_run())

    def test_customers_follow_the_lead_scope_predicate(self):
        async def _run():
            async with SessionLocal() as db:
                manager, _, _ = await _staff(db, MANAGER_KEYS)
                me, _, _ = await _staff(db, [P.PERM_LEADS_SCOPE_ASSIGNED])
                mine = await _customer(db, manager)
                mine.lead.assigned_to = me.id
                theirs = await _customer(db, manager)
                await db.flush()
                ids = [mine.customer.id, theirs.customer.id]
                for scopes, expected in (((P.PERM_LEADS_SCOPE_ALL,), set(ids)), ((P.PERM_LEADS_SCOPE_ASSIGNED,), {mine.customer.id}), ((), set())):
                    ctx = StaffContext({"role": "jaz_staff"}, me.id, False, (), frozenset(scopes))
                    rows, total = await customers_repo.list_rows(db, visibility=lead_visibility(ctx), filters=CustomerFilters(), sort="converted_at", descending=True, limit=100, offset=0)
                    got = {r.customer.id for r in rows} & set(ids)
                    assert got == expected, scopes
                await db.rollback()
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
                assert {i.name for i in declared.indexes} == {n for n in db_table["indexes"] if n != f"{model.__tablename__}_pkey"}, model.__tablename__
                assert len(declared.foreign_keys) == len(db_table["fks"]), model.__tablename__
        run(_run())

    def test_the_foreign_keys_of_both_tables(self):
        async def _run():
            async with SessionLocal() as db:
                rows = (await db.execute(text(
                    "SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype::text FROM pg_constraint "
                    "WHERE contype = 'f' AND conrelid::regclass::text = ANY(:tables)"
                ), {"tables": [m.__tablename__ for m in TABLES]})).all()
                by_target = {(t, ref): d for t, ref, d in rows}
                assert by_target[("sales_customers", "sales_leads")] == "r"                                       # RESTRICT: a lead with a customer cannot go
                assert by_target[("sales_customers", "companies")] == "r"                                         # RESTRICT: nor a company
                assert by_target[("sales_onboarding", "sales_customers")] == "r"
                assert {d for (t, ref), d in by_target.items() if ref == "users"} == {"a"}                        # NO ACTION on every user FK, like all others
        run(_run())

    def test_the_seeded_permissions_and_grants_are_exactly_the_approved_ones(self):
        async def _run():
            async with SessionLocal() as db:
                keys = set((await db.execute(text("SELECT key FROM staff_permissions WHERE key LIKE 'sales.customers.%' OR key LIKE 'sales.onboarding.%'"))).scalars().all())
                assert keys == {k for k in P.ALL_PERMISSIONS if k.split(".")[1] in ("customers", "onboarding")}
                grants = (await db.execute(text(
                    "SELECT r.key, p.permission_key FROM staff_role_permissions p JOIN staff_roles r ON r.id = p.role_id "
                    "WHERE r.is_system AND (p.permission_key LIKE 'sales.customers.%' OR p.permission_key LIKE 'sales.onboarding.%')"
                ))).all()
                by_role = {}
                for role, key in grants:
                    by_role.setdefault(role, set()).add(key)
                assert by_role == {
                    "sales_manager": {"sales.customers.view", "sales.customers.convert", "sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.assign", "sales.onboarding.scope_all"},
                    "sales_employee": {"sales.customers.view"},
                    "onboarding_employee": {"sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"},
                }                                                                                                  # and nothing for lead_data_entry
        run(_run())
