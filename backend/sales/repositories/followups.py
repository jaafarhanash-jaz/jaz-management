"""Follow-up persistence (Phase 3). See repositories/work.py for the shared conventions.

"Overdue" is a reading of the clock, not a stored status: pending AND due_at < now(). It is computed in SQL, once per
statement, so the `due=overdue` filter, the counts and the `is_overdue` flag on every row always agree.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import User
from sales.constants import FOLLOWUP_OPEN
from sales.models import SalesFollowup, SalesLead
from sales.repositories import work
from sales.repositories.work import LeadInfo

_Assignee = aliased(User)
_Creator = aliased(User)

SORT_FIELDS = ("due_at", "completed_at", "created_at")


def _is_overdue():
    return and_(SalesFollowup.status == FOLLOWUP_OPEN, SalesFollowup.due_at < func.now())


def _is_upcoming():
    return and_(SalesFollowup.status == FOLLOWUP_OPEN, SalesFollowup.due_at >= func.now())


@dataclass(frozen=True)
class FollowupRow:
    followup: SalesFollowup
    lead: LeadInfo
    assignee_name: Optional[str]
    assignee_status: Optional[str]
    creator_name: Optional[str]
    is_overdue: bool


@dataclass
class FollowupFilters:
    lead_id: Optional[uuid.UUID] = None
    assigned_to: Optional[uuid.UUID] = None
    statuses: Optional[Sequence[str]] = None
    due: Optional[str] = None               # 'overdue' | 'upcoming' - both mean "pending, and ..."
    due_from: Optional[date] = None         # inclusive calendar dates, Baghdad time
    due_to: Optional[date] = None
    q: Optional[str] = None                 # the lead's business or contact name


def _row_select():
    return (
        select(SalesFollowup, *work.LEAD_COLUMNS, _Assignee.name, _Assignee.status, _Creator.name, _is_overdue().label("is_overdue"))
        .select_from(SalesFollowup)
        .join(SalesLead, SalesLead.id == SalesFollowup.lead_id)
        .outerjoin(_Assignee, _Assignee.id == SalesFollowup.assigned_to)
        .outerjoin(_Creator, _Creator.id == SalesFollowup.created_by)
    )


def _to_row(t) -> FollowupRow:
    n = 1 + len(work.LEAD_COLUMNS)
    return FollowupRow(followup=t[0], lead=work.lead_info(t), assignee_name=t[n], assignee_status=t[n + 1], creator_name=t[n + 2], is_overdue=bool(t[n + 3]))


async def get_row(db: AsyncSession, followup_id: uuid.UUID) -> Optional[FollowupRow]:
    row = (await db.execute(_row_select().where(SalesFollowup.id == followup_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, followup_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesFollowup]:
    """The bare follow-up. for_update=True takes a row lock and re-reads the row (see repositories/calls.get)."""
    stmt = select(SalesFollowup).where(SalesFollowup.id == followup_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


def _conditions(visibility, filters: FollowupFilters) -> list:
    conds = [visibility]
    if filters.lead_id is not None:
        conds.append(SalesFollowup.lead_id == filters.lead_id)
    else:
        conds.append(SalesLead.archived_at.is_(None))     # a cross-lead list never shows the follow-ups of archived leads
    if filters.assigned_to is not None:
        conds.append(SalesFollowup.assigned_to == filters.assigned_to)
    if filters.statuses:
        conds.append(SalesFollowup.status.in_(list(filters.statuses)))
    if filters.due == "overdue":
        conds.append(_is_overdue())
    elif filters.due == "upcoming":
        conds.append(_is_upcoming())
    lower, upper = work.day_bounds(filters.due_from, filters.due_to)
    if lower is not None:
        conds.append(SalesFollowup.due_at >= lower)
    if upper is not None:
        conds.append(SalesFollowup.due_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\")))
    return conds


def _sort_column(sort: str):
    return {"due_at": SalesFollowup.due_at, "completed_at": SalesFollowup.completed_at, "created_at": SalesFollowup.created_at}[sort]


async def list_rows(
    db: AsyncSession, *, visibility, filters: FollowupFilters, sort: str, descending: bool, limit: int, offset: int
) -> Tuple[List[FollowupRow], int]:
    conds = _conditions(visibility, filters)
    total = (
        await db.execute(select(func.count()).select_from(SalesFollowup).join(SalesLead, SalesLead.id == SalesFollowup.lead_id).where(*conds))
    ).scalar_one()
    column = _sort_column(sort)
    ordered = column.desc().nulls_last() if descending else column.asc().nulls_last()
    stmt = _row_select().where(*conds).order_by(ordered, SalesFollowup.id.desc() if descending else SalesFollowup.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def counts(db: AsyncSession, *, visibility, filters: FollowupFilters, me: uuid.UUID) -> dict:
    """One query: the numbers behind the tabs, within the caller's scope (and the lead / assignee / search filters;
    the status and due filters are ignored - they are what is being counted). `mine_*` is what is assigned to `me`."""
    base = FollowupFilters(lead_id=filters.lead_id, assigned_to=filters.assigned_to, q=filters.q)
    conds = _conditions(visibility, base)
    pending = SalesFollowup.status == FOLLOWUP_OPEN
    mine = SalesFollowup.assigned_to == me
    row = (
        await db.execute(
            select(
                func.count().filter(pending),
                func.count().filter(_is_overdue()),
                func.count().filter(_is_upcoming()),
                func.count().filter(SalesFollowup.status == "completed"),
                func.count().filter(SalesFollowup.status == "cancelled"),
                func.count().filter(and_(pending, mine)),
                func.count().filter(and_(_is_overdue(), mine)),
            )
            .select_from(SalesFollowup)
            .join(SalesLead, SalesLead.id == SalesFollowup.lead_id)
            .where(*conds)
        )
    ).one()
    keys = ("pending", "overdue", "upcoming", "completed", "cancelled", "mine_pending", "mine_overdue")
    return {key: int(value) for key, value in zip(keys, row)}
