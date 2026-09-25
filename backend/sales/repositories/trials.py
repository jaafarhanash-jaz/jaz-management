"""Sales-side trial persistence (Phase 3). See repositories/work.py for the shared conventions.

"Ending soon" and "overdue" are readings of the clock computed in SQL, once per statement: an ACTIVE trial is
ending soon when its expected end is at most N days away - INCLUDING one whose expected end has already passed
(which is the most urgent kind: somebody has to close it) - and overdue when the expected end is behind us.
"""
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import User
from sales.constants import DEFAULT_ENDING_SOON_DAYS, TRIAL_OPEN
from sales.models import SalesLead, SalesTrial
from sales.repositories import work
from sales.repositories.work import LeadInfo

_Creator = aliased(User)


def _is_overdue():
    return and_(SalesTrial.status == TRIAL_OPEN, SalesTrial.expected_end_at < func.now())


def _is_ending_soon(days: int):
    return and_(SalesTrial.status == TRIAL_OPEN, SalesTrial.expected_end_at <= func.now() + timedelta(days=days))


@dataclass(frozen=True)
class TrialRow:
    trial: SalesTrial
    lead: LeadInfo
    creator_name: Optional[str]
    is_overdue: bool


@dataclass
class TrialFilters:
    lead_id: Optional[uuid.UUID] = None
    statuses: Optional[Sequence[str]] = None
    ending_within_days: Optional[int] = None    # active trials whose expected end is at most this many days away
    ending_from: Optional[date] = None          # inclusive calendar dates, Baghdad time, on expected_end_at
    ending_to: Optional[date] = None
    q: Optional[str] = None                     # the lead's business or contact name


def _row_select():
    return (
        select(SalesTrial, *work.LEAD_COLUMNS, _Creator.name, _is_overdue().label("is_overdue"))
        .select_from(SalesTrial)
        .join(SalesLead, SalesLead.id == SalesTrial.lead_id)
        .outerjoin(_Creator, _Creator.id == SalesTrial.created_by)
    )


def _to_row(t) -> TrialRow:
    n = 1 + len(work.LEAD_COLUMNS)
    return TrialRow(trial=t[0], lead=work.lead_info(t), creator_name=t[n], is_overdue=bool(t[n + 1]))


async def get_row(db: AsyncSession, trial_id: uuid.UUID) -> Optional[TrialRow]:
    row = (await db.execute(_row_select().where(SalesTrial.id == trial_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, trial_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesTrial]:
    """The bare trial. for_update=True takes a row lock and re-reads the row (see repositories/calls.get)."""
    stmt = select(SalesTrial).where(SalesTrial.id == trial_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


async def get_active_for_lead(db: AsyncSession, lead_id: uuid.UUID) -> Optional[SalesTrial]:
    return (
        await db.execute(select(SalesTrial).where(SalesTrial.lead_id == lead_id, SalesTrial.status == TRIAL_OPEN))
    ).scalar_one_or_none()


def _conditions(visibility, filters: TrialFilters) -> list:
    conds = [visibility]
    if filters.lead_id is not None:
        conds.append(SalesTrial.lead_id == filters.lead_id)
    else:
        conds.append(SalesLead.archived_at.is_(None))     # a cross-lead list never shows the trials of archived leads
    if filters.statuses:
        conds.append(SalesTrial.status.in_(list(filters.statuses)))
    if filters.ending_within_days is not None:
        conds.append(_is_ending_soon(filters.ending_within_days))
    lower, upper = work.day_bounds(filters.ending_from, filters.ending_to)
    if lower is not None:
        conds.append(SalesTrial.expected_end_at >= lower)
    if upper is not None:
        conds.append(SalesTrial.expected_end_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\")))
    return conds


def _sort_column(sort: str):
    return {"expected_end_at": SalesTrial.expected_end_at, "started_at": SalesTrial.started_at, "actual_end_at": SalesTrial.actual_end_at}[sort]


SORT_FIELDS = ("expected_end_at", "started_at", "actual_end_at")


async def list_rows(
    db: AsyncSession, *, visibility, filters: TrialFilters, sort: str, descending: bool, limit: int, offset: int
) -> Tuple[List[TrialRow], int]:
    conds = _conditions(visibility, filters)
    total = (
        await db.execute(select(func.count()).select_from(SalesTrial).join(SalesLead, SalesLead.id == SalesTrial.lead_id).where(*conds))
    ).scalar_one()
    column = _sort_column(sort)
    ordered = column.desc().nulls_last() if descending else column.asc().nulls_last()
    stmt = _row_select().where(*conds).order_by(ordered, SalesTrial.id.desc() if descending else SalesTrial.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def counts(
    db: AsyncSession, *, visibility, filters: TrialFilters, ending_within_days: int = DEFAULT_ENDING_SOON_DAYS
) -> dict:
    """One query: the numbers behind the tabs, within the caller's scope (and the lead / search filters; the status
    and date filters are ignored - they are what is being counted)."""
    base = TrialFilters(lead_id=filters.lead_id, q=filters.q)
    conds = _conditions(visibility, base)
    row = (
        await db.execute(
            select(
                func.count().filter(SalesTrial.status == TRIAL_OPEN),
                func.count().filter(_is_ending_soon(ending_within_days)),
                func.count().filter(_is_overdue()),
                func.count().filter(SalesTrial.status == "completed"),
                func.count().filter(SalesTrial.status == "cancelled"),
            )
            .select_from(SalesTrial)
            .join(SalesLead, SalesLead.id == SalesTrial.lead_id)
            .where(*conds)
        )
    ).one()
    keys = ("active", "ending_soon", "overdue", "completed", "cancelled")
    return {key: int(value) for key, value in zip(keys, row)}
