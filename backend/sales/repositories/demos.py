"""Demo persistence (Phase 3). See repositories/work.py for the shared conventions.

A demo whose time has passed but that is still `scheduled` is "past due": it is waiting for its outcome (completed,
no-show or cancelled). That is a reading of the clock computed in SQL, once per statement, so the counts and the
`is_past_due` flag on every row agree.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import User
from sales.constants import DEMO_OPEN
from sales.models import SalesDemo, SalesLead
from sales.repositories import work
from sales.repositories.work import LeadInfo

_Assignee = aliased(User)
_Creator = aliased(User)


def _is_past_due():
    return and_(SalesDemo.status == DEMO_OPEN, SalesDemo.scheduled_at < func.now())


def _is_upcoming():
    return and_(SalesDemo.status == DEMO_OPEN, SalesDemo.scheduled_at >= func.now())


@dataclass(frozen=True)
class DemoRow:
    demo: SalesDemo
    lead: LeadInfo
    assignee_name: Optional[str]
    assignee_status: Optional[str]
    creator_name: Optional[str]
    is_past_due: bool


@dataclass
class DemoFilters:
    lead_id: Optional[uuid.UUID] = None
    assigned_to: Optional[uuid.UUID] = None
    statuses: Optional[Sequence[str]] = None
    scheduled_from: Optional[date] = None   # inclusive calendar dates, Baghdad time
    scheduled_to: Optional[date] = None
    q: Optional[str] = None                 # the lead's business or contact name


def _row_select():
    return (
        select(SalesDemo, *work.LEAD_COLUMNS, _Assignee.name, _Assignee.status, _Creator.name, _is_past_due().label("is_past_due"))
        .select_from(SalesDemo)
        .join(SalesLead, SalesLead.id == SalesDemo.lead_id)
        .outerjoin(_Assignee, _Assignee.id == SalesDemo.assigned_to)
        .outerjoin(_Creator, _Creator.id == SalesDemo.created_by)
    )


def _to_row(t) -> DemoRow:
    n = 1 + len(work.LEAD_COLUMNS)
    return DemoRow(demo=t[0], lead=work.lead_info(t), assignee_name=t[n], assignee_status=t[n + 1], creator_name=t[n + 2], is_past_due=bool(t[n + 3]))


async def get_row(db: AsyncSession, demo_id: uuid.UUID) -> Optional[DemoRow]:
    row = (await db.execute(_row_select().where(SalesDemo.id == demo_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, demo_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesDemo]:
    """The bare demo. for_update=True takes a row lock and re-reads the row (see repositories/calls.get)."""
    stmt = select(SalesDemo).where(SalesDemo.id == demo_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


def _conditions(visibility, filters: DemoFilters) -> list:
    conds = [visibility]
    if filters.lead_id is not None:
        conds.append(SalesDemo.lead_id == filters.lead_id)
    else:
        conds.append(SalesLead.archived_at.is_(None))     # a cross-lead list never shows the demos of archived leads
    if filters.assigned_to is not None:
        conds.append(SalesDemo.assigned_to == filters.assigned_to)
    if filters.statuses:
        conds.append(SalesDemo.status.in_(list(filters.statuses)))
    lower, upper = work.day_bounds(filters.scheduled_from, filters.scheduled_to)
    if lower is not None:
        conds.append(SalesDemo.scheduled_at >= lower)
    if upper is not None:
        conds.append(SalesDemo.scheduled_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\")))
    return conds


async def list_rows(
    db: AsyncSession, *, visibility, filters: DemoFilters, descending: bool, limit: int, offset: int
) -> Tuple[List[DemoRow], int]:
    conds = _conditions(visibility, filters)
    total = (
        await db.execute(select(func.count()).select_from(SalesDemo).join(SalesLead, SalesLead.id == SalesDemo.lead_id).where(*conds))
    ).scalar_one()
    ordered = SalesDemo.scheduled_at.desc() if descending else SalesDemo.scheduled_at.asc()
    stmt = _row_select().where(*conds).order_by(ordered, SalesDemo.id.desc() if descending else SalesDemo.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def counts(db: AsyncSession, *, visibility, filters: DemoFilters, me: uuid.UUID) -> dict:
    """One query: the numbers behind the tabs, within the caller's scope (and the lead / assignee / search filters;
    the status and date filters are ignored - they are what is being counted). `mine_scheduled` is what is assigned
    to `me`. `scheduled` = upcoming + past_due = every demo still waiting to happen or to be closed out."""
    base = DemoFilters(lead_id=filters.lead_id, assigned_to=filters.assigned_to, q=filters.q)
    conds = _conditions(visibility, base)
    open_ = SalesDemo.status == DEMO_OPEN
    row = (
        await db.execute(
            select(
                func.count().filter(open_),
                func.count().filter(_is_upcoming()),
                func.count().filter(_is_past_due()),
                func.count().filter(SalesDemo.status == "completed"),
                func.count().filter(SalesDemo.status == "cancelled"),
                func.count().filter(SalesDemo.status == "no_show"),
                func.count().filter(and_(open_, SalesDemo.assigned_to == me)),
            )
            .select_from(SalesDemo)
            .join(SalesLead, SalesLead.id == SalesDemo.lead_id)
            .where(*conds)
        )
    ).one()
    keys = ("scheduled", "upcoming", "past_due", "completed", "cancelled", "no_show", "mine_scheduled")
    return {key: int(value) for key, value in zip(keys, row)}
