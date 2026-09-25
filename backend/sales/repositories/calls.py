"""Call persistence (Phase 3). See repositories/work.py for the shared conventions."""
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import User
from sales.constants import CALL_RESULTS
from sales.models import SalesCall, SalesLead
from sales.repositories import work
from sales.repositories.work import LeadInfo

_Employee = aliased(User)


@dataclass(frozen=True)
class CallRow:
    call: SalesCall
    lead: LeadInfo
    employee_name: Optional[str]
    employee_status: Optional[str]


@dataclass
class CallFilters:
    lead_id: Optional[uuid.UUID] = None
    employee_id: Optional[uuid.UUID] = None
    results: Optional[Sequence[str]] = None
    called_from: Optional[date] = None      # inclusive calendar dates, Baghdad time
    called_to: Optional[date] = None
    q: Optional[str] = None                 # the lead's business or contact name


def _row_select():
    return (
        select(SalesCall, *work.LEAD_COLUMNS, _Employee.name, _Employee.status)
        .select_from(SalesCall)
        .join(SalesLead, SalesLead.id == SalesCall.lead_id)
        .outerjoin(_Employee, _Employee.id == SalesCall.employee_id)
    )


def _to_row(t) -> CallRow:
    n = 1 + len(work.LEAD_COLUMNS)
    return CallRow(call=t[0], lead=work.lead_info(t), employee_name=t[n], employee_status=t[n + 1])


async def get_row(db: AsyncSession, call_id: uuid.UUID) -> Optional[CallRow]:
    # populate_existing: the call may already be in the session (a mutation just flushed it); re-read what the
    # database now holds (server-side updated_at) instead of trusting the in-memory copy.
    row = (await db.execute(_row_select().where(SalesCall.id == call_id), execution_options={"populate_existing": True})).first()
    return _to_row(row) if row else None


async def get(db: AsyncSession, call_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesCall]:
    """The bare call. for_update=True takes a row lock and re-reads the row, so a state check made after it sees what
    the database really holds even if a concurrent change committed while this request waited for the lock."""
    stmt = select(SalesCall).where(SalesCall.id == call_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


def _conditions(visibility, filters: CallFilters, *, with_result: bool = True) -> list:
    conds = [visibility]
    if filters.lead_id is not None:
        conds.append(SalesCall.lead_id == filters.lead_id)
    else:
        conds.append(SalesLead.archived_at.is_(None))     # a cross-lead list never shows the calls of archived leads
    if filters.employee_id is not None:
        conds.append(SalesCall.employee_id == filters.employee_id)
    if with_result and filters.results:
        conds.append(SalesCall.result.in_(list(filters.results)))
    lower, upper = work.day_bounds(filters.called_from, filters.called_to)
    if lower is not None:
        conds.append(SalesCall.called_at >= lower)
    if upper is not None:
        conds.append(SalesCall.called_at < upper)
    pattern = work.search_pattern(filters.q)
    if pattern:
        conds.append(or_(SalesLead.business_name.ilike(pattern, escape="\\"), SalesLead.contact_name.ilike(pattern, escape="\\")))
    return conds


async def list_rows(
    db: AsyncSession, *, visibility, filters: CallFilters, descending: bool, limit: int, offset: int
) -> Tuple[List[CallRow], int]:
    conds = _conditions(visibility, filters)
    total = (
        await db.execute(select(func.count()).select_from(SalesCall).join(SalesLead, SalesLead.id == SalesCall.lead_id).where(*conds))
    ).scalar_one()
    ordered = SalesCall.called_at.desc() if descending else SalesCall.called_at.asc()
    stmt = _row_select().where(*conds).order_by(ordered, SalesCall.id.desc() if descending else SalesCall.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def result_counts(db: AsyncSession, *, visibility, filters: CallFilters) -> Dict[str, int]:
    """Calls per result (the result filter itself is ignored), within the caller's scope."""
    conds = _conditions(visibility, filters, with_result=False)
    result = await db.execute(
        select(SalesCall.result, func.count())
        .select_from(SalesCall)
        .join(SalesLead, SalesLead.id == SalesCall.lead_id)
        .where(*conds)
        .group_by(SalesCall.result)
    )
    counts = {r: 0 for r in CALL_RESULTS}
    counts.update({r: n for r, n in result.all()})
    return counts
