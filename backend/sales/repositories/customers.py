"""Customer persistence (Phase 4). See repositories/work.py for the shared conventions.

A customer row is read together with its lead, its JAZ company (and the company owner's contact details), the
person who converted it and the state of its onboarding, in ONE query - a page of customers costs one query for the
rows + one for the total, never one per customer. Lists apply the caller's LEAD scope (services/lead_access
.lead_visibility) to the joined lead: a customer is visible exactly when the lead it came from is.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import Company, User
from sales.constants import CUSTOMER_STATUSES
from sales.models import SalesCustomer, SalesLead, SalesOnboarding
from sales.repositories import work
from sales.repositories.work import LeadInfo

_Converter = aliased(User)
_Owner = aliased(User)
_OnboardingOwner = aliased(User)

SORT_FIELDS = ("converted_at", "business_name")


@dataclass(frozen=True)
class CustomerRow:
    customer: SalesCustomer
    lead: LeadInfo
    contact_name: Optional[str]              # the lead's contact details (what Sales collected)
    phone: Optional[str]
    email: Optional[str]
    city: Optional[str]
    company_name: Optional[str]
    company_subscription_status: Optional[str]
    company_deleted: bool
    owner_name: Optional[str]                # the company owner account that conversion created
    owner_email: Optional[str]
    owner_phone: Optional[str]
    converter_name: Optional[str]
    onboarding_id: Optional[uuid.UUID]
    onboarding_stage: Optional[str]
    onboarding_assigned_to: Optional[uuid.UUID]
    onboarding_assignee_name: Optional[str]
    onboarding_assignee_status: Optional[str]
    company_subscription_end_date: Optional[datetime] = None     # with the flag below: whether the period is over by now
    company_subscription_ends_exactly: bool = False


@dataclass
class CustomerFilters:
    statuses: Optional[Sequence[str]] = None
    converted_from: Optional[date] = None    # inclusive calendar dates, Baghdad time
    converted_to: Optional[date] = None
    q: Optional[str] = None                  # the lead's business or contact name, or the company's name


def _row_select():
    return (
        select(
            SalesCustomer, *work.LEAD_COLUMNS,
            SalesLead.contact_name, SalesLead.phone, SalesLead.email, SalesLead.city,
            Company.name, Company.subscription_status, Company.deleted_at.is_not(None),
            _Owner.name, _Owner.email, _Owner.phone, _Converter.name,
            SalesOnboarding.id, SalesOnboarding.stage, SalesOnboarding.assigned_to, _OnboardingOwner.name, _OnboardingOwner.status,
            Company.subscription_end_date, Company.subscription_ends_exactly,
        )
        .select_from(SalesCustomer)
        .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
        .join(Company, Company.id == SalesCustomer.company_id)
        .outerjoin(_Owner, _Owner.id == Company.owner_id)
        .outerjoin(_Converter, _Converter.id == SalesCustomer.converted_by)
        .outerjoin(SalesOnboarding, SalesOnboarding.customer_id == SalesCustomer.id)
        .outerjoin(_OnboardingOwner, _OnboardingOwner.id == SalesOnboarding.assigned_to)
    )


def _to_row(t) -> CustomerRow:
    n = 1 + len(work.LEAD_COLUMNS)
    return CustomerRow(
        customer=t[0], lead=work.lead_info(t),
        contact_name=t[n], phone=t[n + 1], email=t[n + 2], city=t[n + 3],
        company_name=t[n + 4], company_subscription_status=t[n + 5], company_deleted=bool(t[n + 6]),
        owner_name=t[n + 7], owner_email=t[n + 8], owner_phone=t[n + 9], converter_name=t[n + 10],
        onboarding_id=t[n + 11], onboarding_stage=t[n + 12], onboarding_assigned_to=t[n + 13], onboarding_assignee_name=t[n + 14],
        onboarding_assignee_status=t[n + 15],
        company_subscription_end_date=t[n + 16], company_subscription_ends_exactly=bool(t[n + 17]),
    )


async def get_row(db: AsyncSession, customer_id: uuid.UUID) -> Optional[CustomerRow]:
    row = (await db.execute(_row_select().where(SalesCustomer.id == customer_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get_row_by_lead(db: AsyncSession, lead_id: uuid.UUID) -> Optional[CustomerRow]:
    row = (await db.execute(_row_select().where(SalesCustomer.lead_id == lead_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, customer_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesCustomer]:
    """The bare customer. for_update=True takes a row lock and re-reads the row."""
    stmt = select(SalesCustomer).where(SalesCustomer.id == customer_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


async def get_by_lead(db: AsyncSession, lead_id: uuid.UUID) -> Optional[SalesCustomer]:
    return (await db.execute(select(SalesCustomer).where(SalesCustomer.lead_id == lead_id), execution_options={"populate_existing": True})).scalar_one_or_none()


def _conditions(visibility, filters: CustomerFilters) -> list:
    conds = [visibility]
    if filters.statuses:
        conds.append(SalesCustomer.status.in_(list(filters.statuses)))
    lower, upper = work.day_bounds(filters.converted_from, filters.converted_to)
    if lower is not None:
        conds.append(SalesCustomer.converted_at >= lower)
    if upper is not None:
        conds.append(SalesCustomer.converted_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(
            SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\"),
            Company.name.ilike(pattern, escape="\\"),
        ))
    return conds


def _sort_column(sort: str):
    return {"converted_at": SalesCustomer.converted_at, "business_name": func.lower(SalesLead.business_name)}[sort]


def _count_select():
    return select(func.count()).select_from(SalesCustomer).join(SalesLead, SalesLead.id == SalesCustomer.lead_id).join(Company, Company.id == SalesCustomer.company_id)


async def list_rows(
    db: AsyncSession, *, visibility, filters: CustomerFilters, sort: str, descending: bool, limit: int, offset: int
) -> Tuple[List[CustomerRow], int]:
    conds = _conditions(visibility, filters)
    total = (await db.execute(_count_select().where(*conds))).scalar_one()
    column = _sort_column(sort)
    ordered = column.desc() if descending else column.asc()
    stmt = _row_select().where(*conds).order_by(ordered, SalesCustomer.id.desc() if descending else SalesCustomer.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def status_counts(db: AsyncSession, *, visibility, filters: CustomerFilters) -> dict:
    """One query: the numbers behind the tabs, within the caller's scope (and the search / date filters; the status
    filter is ignored - it is what is being counted)."""
    conds = _conditions(visibility, CustomerFilters(converted_from=filters.converted_from, converted_to=filters.converted_to, q=filters.q))
    result = await db.execute(
        select(SalesCustomer.status, func.count())
        .select_from(SalesCustomer)
        .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
        .join(Company, Company.id == SalesCustomer.company_id)
        .where(*conds)
        .group_by(SalesCustomer.status)
    )
    counts = {status: 0 for status in CUSTOMER_STATUSES}
    counts.update({status: int(n) for status, n in result.all()})
    return {"counts": counts, "total": sum(counts.values())}


# ---- facts conversion needs ----------------------------------------------------------------------------------

async def email_registered(db: AsyncSession, email: str) -> bool:
    """A live account already uses this email. Compared case-insensitively: 'Owner@Shop.com' and 'owner@shop.com' are
    the same mailbox, and two accounts that differ only by case would be ambiguous at login."""
    return (
        await db.execute(select(func.count()).select_from(User).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None)))
    ).scalar_one() > 0


async def phone_registered(db: AsyncSession, phone: str) -> bool:
    return (await db.execute(select(func.count()).select_from(User).where(User.phone == phone, User.deleted_at.is_(None)))).scalar_one() > 0


async def company_employee_count(db: AsyncSession, company_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(User).where(User.company_id == company_id, User.role == "employee", User.deleted_at.is_(None))
        )
    ).scalar_one()
