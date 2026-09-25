"""Onboarding persistence (Phase 4). See repositories/work.py for the shared conventions.

An onboarding row is read with its customer, the lead's contact details, the company's name and the assignee in ONE
query - a page costs one query for the rows + one for the total, never one per record. Lists apply the caller's
ONBOARDING scope (services/onboarding_access.onboarding_visibility) as a predicate: they never widen it through the lead
scope, because the people who run onboarding are not salespeople and must not reach the lead.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import Company, User
from sales import permissions as perms
from sales.constants import ONBOARDING_DONE, ONBOARDING_STAGES
from sales.models import SalesCustomer, SalesLead, SalesOnboarding, StaffRole, StaffRolePermission, StaffUserRole
from sales.repositories import work

_Assignee = aliased(User)

SORT_FIELDS = ("started_at", "updated_at", "completed_at", "business_name")


@dataclass(frozen=True)
class OnboardingRow:
    onboarding: SalesOnboarding
    customer_status: str
    company_id: uuid.UUID
    company_name: Optional[str]
    converted_at: datetime
    lead_id: uuid.UUID
    lead_created_by: uuid.UUID               # with lead_assigned_to: what the lead-scope check needs (see the service)
    lead_assigned_to: Optional[uuid.UUID]
    business_name: str
    contact_name: Optional[str]
    contact_position: Optional[str]
    phone: Optional[str]
    whatsapp: Optional[str]
    email: Optional[str]
    city: Optional[str]
    country: Optional[str]
    assignee_name: Optional[str]
    assignee_status: Optional[str]


@dataclass
class OnboardingFilters:
    stages: Optional[Sequence[str]] = None
    assigned_to: Optional[uuid.UUID] = None
    unassigned: bool = False                 # nobody owns it yet (the `won` stage)
    open_only: bool = False                  # not yet activated
    started_from: Optional[date] = None      # inclusive calendar dates, Baghdad time
    started_to: Optional[date] = None
    q: Optional[str] = None                  # the customer's business or contact name


def _row_select():
    return (
        select(
            SalesOnboarding, SalesCustomer.status, SalesCustomer.company_id, Company.name, SalesCustomer.converted_at,
            SalesLead.id, SalesLead.created_by, SalesLead.assigned_to, SalesLead.business_name, SalesLead.contact_name,
            SalesLead.contact_position, SalesLead.phone, SalesLead.whatsapp, SalesLead.email, SalesLead.city, SalesLead.country,
            _Assignee.name, _Assignee.status,
        )
        .select_from(SalesOnboarding)
        .join(SalesCustomer, SalesCustomer.id == SalesOnboarding.customer_id)
        .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
        .join(Company, Company.id == SalesCustomer.company_id)
        .outerjoin(_Assignee, _Assignee.id == SalesOnboarding.assigned_to)
    )


def _to_row(t) -> OnboardingRow:
    return OnboardingRow(
        onboarding=t[0], customer_status=t[1], company_id=t[2], company_name=t[3], converted_at=t[4],
        lead_id=t[5], lead_created_by=t[6], lead_assigned_to=t[7], business_name=t[8], contact_name=t[9],
        contact_position=t[10], phone=t[11], whatsapp=t[12], email=t[13], city=t[14], country=t[15],
        assignee_name=t[16], assignee_status=t[17],
    )


def _joined_count():
    return (
        select(func.count()).select_from(SalesOnboarding)
        .join(SalesCustomer, SalesCustomer.id == SalesOnboarding.customer_id)
        .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
    )


async def get_row(db: AsyncSession, onboarding_id: uuid.UUID) -> Optional[OnboardingRow]:
    row = (await db.execute(_row_select().where(SalesOnboarding.id == onboarding_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get_row_by_customer(db: AsyncSession, customer_id: uuid.UUID) -> Optional[OnboardingRow]:
    row = (await db.execute(_row_select().where(SalesOnboarding.customer_id == customer_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, onboarding_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesOnboarding]:
    """The bare onboarding. for_update=True takes a row lock and re-reads the row, so its stage is checked against
    what the database really holds, not against what was there before a wait."""
    stmt = select(SalesOnboarding).where(SalesOnboarding.id == onboarding_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


def _conditions(visibility, filters: OnboardingFilters) -> list:
    conds = [visibility]
    if filters.stages:
        conds.append(SalesOnboarding.stage.in_(list(filters.stages)))
    if filters.assigned_to is not None:
        conds.append(SalesOnboarding.assigned_to == filters.assigned_to)
    if filters.unassigned:
        conds.append(SalesOnboarding.assigned_to.is_(None))
    if filters.open_only:
        conds.append(SalesOnboarding.stage != ONBOARDING_DONE)
    lower, upper = work.day_bounds(filters.started_from, filters.started_to)
    if lower is not None:
        conds.append(SalesOnboarding.started_at >= lower)
    if upper is not None:
        conds.append(SalesOnboarding.started_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\")))
    return conds


def _sort_column(sort: str):
    return {
        "started_at": SalesOnboarding.started_at, "updated_at": SalesOnboarding.updated_at,
        "completed_at": SalesOnboarding.completed_at, "business_name": func.lower(SalesLead.business_name),
    }[sort]


async def list_rows(
    db: AsyncSession, *, visibility, filters: OnboardingFilters, sort: str, descending: bool, limit: int, offset: int
) -> Tuple[List[OnboardingRow], int]:
    conds = _conditions(visibility, filters)
    total = (await db.execute(_joined_count().where(*conds))).scalar_one()
    column = _sort_column(sort)
    ordered = column.desc().nulls_last() if descending else column.asc().nulls_last()
    stmt = _row_select().where(*conds).order_by(ordered, SalesOnboarding.id.desc() if descending else SalesOnboarding.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def counts(db: AsyncSession, *, visibility, filters: OnboardingFilters, me: uuid.UUID) -> dict:
    """One query: the numbers behind the tabs, within the caller's scope (and the search filter; the stage / owner
    filters are ignored - they are what is being counted). `mine` is what is assigned to `me`."""
    conds = _conditions(visibility, OnboardingFilters(q=filters.q))
    stage_counts = [func.count().filter(SalesOnboarding.stage == stage) for stage in ONBOARDING_STAGES]
    row = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(SalesOnboarding.stage != ONBOARDING_DONE),
                func.count().filter(SalesOnboarding.assigned_to.is_(None)),
                func.count().filter(and_(SalesOnboarding.assigned_to == me, SalesOnboarding.stage != ONBOARDING_DONE)),
                *stage_counts,
            )
            .select_from(SalesOnboarding)
            .join(SalesCustomer, SalesCustomer.id == SalesOnboarding.customer_id)
            .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
            .where(*conds)
        )
    ).one()
    return {
        "total": int(row[0]), "open": int(row[1]), "unassigned": int(row[2]), "mine": int(row[3]),
        "stages": {stage: int(n) for stage, n in zip(ONBOARDING_STAGES, row[4:])},
    }


# ---- who can be given an onboarding -------------------------------------------------------------------------

def _holds(user_col, permission: str):
    """EXISTS: the user holds `permission` through an ACTIVE grant of an ACTIVE role (a revoked grant or a retired role
    stops it exactly as it stops their access)."""
    return exists(
        select(1)
        .select_from(StaffUserRole)
        .join(StaffRole, and_(StaffRole.id == StaffUserRole.role_id, StaffRole.is_active.is_(True)))
        .join(StaffRolePermission, and_(StaffRolePermission.role_id == StaffRole.id, StaffRolePermission.permission_key == permission))
        .where(StaffUserRole.user_id == user_col, StaffUserRole.revoked_at.is_(None))
    )


def _may_receive_onboarding(user_col):
    """Somebody who can WORK an onboarding record and will be able to SEE the one given to them: it needs the manage
    permission and the assigned scope (the scope is what makes the assignment visible to them)."""
    return and_(_holds(user_col, perms.PERM_ONBOARDING_MANAGE), _holds(user_col, perms.PERM_ONBOARDING_SCOPE_ASSIGNED))


def _usable_staff_account():
    return and_(User.role == perms.PLATFORM_ROLE_STAFF, User.status == "active", User.deleted_at.is_(None))


async def get_assignable_user(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> Optional[User]:
    """The user, if - and only if - they may be given an onboarding right now: an ACTIVE `jaz_staff` account, not
    deleted, holding onboarding.manage and the assigned onboarding scope through active roles. lock=True takes a shared
    row lock, so the account cannot be deactivated by a concurrent request between this check and the assignment
    committing."""
    stmt = select(User).where(User.id == user_id, _usable_staff_account(), _may_receive_onboarding(User.id))
    if lock:
        stmt = stmt.with_for_update(read=True, of=User)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_assignees(db: AsyncSession) -> List[Tuple[User, int]]:
    """Everyone who may be given an onboarding, with their OPEN workload (not yet activated)."""
    open_counts = (
        select(SalesOnboarding.assigned_to.label("uid"), func.count().label("n"))
        .where(SalesOnboarding.assigned_to.is_not(None), SalesOnboarding.stage != ONBOARDING_DONE)
        .group_by(SalesOnboarding.assigned_to)
        .subquery()
    )
    result = await db.execute(
        select(User, func.coalesce(open_counts.c.n, 0))
        .outerjoin(open_counts, open_counts.c.uid == User.id)
        .where(_usable_staff_account(), _may_receive_onboarding(User.id))
        .order_by(User.name, User.id)
    )
    return [(user, int(n)) for user, n in result.all()]
