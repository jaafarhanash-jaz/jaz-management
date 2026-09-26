"""Batch, search and performance READS (migration f2b6d8a1c4e9) - the Sales Manager's views.

Nothing here writes (services/batches.py and services/work_sessions.py do), and nothing here is reachable except through the
Sales Manager's routes (sales.batches.view / sales.performance.view AND sales.leads.scope_all). Every listing is paginated or
bounded; the per-row extras (attempt statistics, names) are fetched with ONE query for the whole page, never one per row.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Date, DateTime, and_, cast, exists, func, literal, literal_column, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import User
from sales import permissions as perms
from sales.constants import DATA_BATCH_SIZE, MASTER_BATCH_SIZE, SESSION_TAIL_CREDIT
from sales.models import (
    SalesBatchAttempt,
    SalesBatchAttemptLead,
    SalesCustomer,
    SalesDataBatch,
    SalesLead,
    SalesLeadAttempt,
    SalesMasterBatch,
    SalesWorkBatch,
    SalesWorkSession,
    StaffRole,
    StaffRolePermission,
    StaffUserRole,
)
from sales.repositories.leads import _PHONE_LOOKING, _escape_like
from sales.services.normalize import digits_only
from sales.timezone import APP_UTC_OFFSET_HOURS

_Creator = aliased(User)
_Assignee = aliased(User)


# =============================================================================
# names
# =============================================================================

async def user_names(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, str]:
    ids = list({i for i in ids if i is not None})
    if not ids:
        return {}
    return {u: n for u, n in (await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()}


# =============================================================================
# overview
# =============================================================================

async def overview(db: AsyncSession) -> dict:
    open_batch = (
        await db.execute(select(SalesDataBatch.id, SalesDataBatch.seq, SalesDataBatch.lead_count).where(SalesDataBatch.status == "open"))
    ).first()
    data_full = (await db.execute(select(func.count()).select_from(SalesDataBatch).where(SalesDataBatch.status == "full"))).scalar_one()
    unmastered = (
        await db.execute(
            select(func.count()).select_from(SalesDataBatch)
            .where(SalesDataBatch.status == "full", SalesDataBatch.master_batch_id.is_(None))
        )
    ).scalar_one()
    masters = (await db.execute(select(func.count()).select_from(SalesMasterBatch))).scalar_one()
    work = dict((await db.execute(select(SalesWorkBatch.status, func.count()).group_by(SalesWorkBatch.status))).all())
    pending = (
        await db.execute(select(func.count()).select_from(SalesLeadAttempt).where(SalesLeadAttempt.result.is_(None)))
    ).scalar_one()
    return {
        "data_batch_size": DATA_BATCH_SIZE, "master_batch_size": MASTER_BATCH_SIZE,
        "open_data_batch": {"id": open_batch.id, "seq": open_batch.seq, "lead_count": open_batch.lead_count} if open_batch else None,
        "full_data_batches": data_full, "data_batches_toward_next_master": unmastered % MASTER_BATCH_SIZE if unmastered else 0,
        "master_batches": masters,
        "work_batches_open": work.get("open", 0), "work_batches_closed": work.get("closed", 0),
        "wait_list_pending": pending,
    }


# =============================================================================
# data batches / master batches
# =============================================================================

async def list_data_batches(
    db: AsyncSession, *, status: Optional[str], master_batch_id: Optional[uuid.UUID], limit: int, offset: int
) -> Tuple[List[tuple], int]:
    conds = []
    if status:
        conds.append(SalesDataBatch.status == status)
    if master_batch_id is not None:
        conds.append(SalesDataBatch.master_batch_id == master_batch_id)
    total = (await db.execute(select(func.count()).select_from(SalesDataBatch).where(*conds))).scalar_one()
    rows = (
        await db.execute(
            select(SalesDataBatch, SalesMasterBatch.seq)
            .outerjoin(SalesMasterBatch, SalesMasterBatch.id == SalesDataBatch.master_batch_id)
            .where(*conds).order_by(SalesDataBatch.seq.desc()).limit(limit).offset(offset)
        )
    ).all()
    return [tuple(r) for r in rows], total


async def get_data_batch(db: AsyncSession, batch_id: uuid.UUID) -> Optional[tuple]:
    row = (
        await db.execute(
            select(SalesDataBatch, SalesMasterBatch.seq)
            .outerjoin(SalesMasterBatch, SalesMasterBatch.id == SalesDataBatch.master_batch_id)
            .where(SalesDataBatch.id == batch_id)
        )
    ).first()
    return tuple(row) if row else None


async def data_batch_leads(db: AsyncSession, batch_id: uuid.UUID) -> List[tuple]:
    """(lead, who entered it, its current owner's name) for the batch's leads, in the order they were entered."""
    rows = (
        await db.execute(
            select(SalesLead, _Creator.name, _Assignee.name)
            .join(_Creator, _Creator.id == SalesLead.created_by)
            .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
            .where(SalesLead.data_batch_id == batch_id).order_by(SalesLead.created_at, SalesLead.id)
        )
    ).all()
    return [tuple(r) for r in rows]


async def data_batch_entrants(db: AsyncSession, batch_id: uuid.UUID) -> List[tuple]:
    """(user id, name, leads they entered into the batch), the most first."""
    rows = (
        await db.execute(
            select(SalesLead.created_by, _Creator.name, func.count().label("n"))
            .join(_Creator, _Creator.id == SalesLead.created_by)
            .where(SalesLead.data_batch_id == batch_id)
            .group_by(SalesLead.created_by, _Creator.name).order_by(func.count().desc(), _Creator.name)
        )
    ).all()
    return [tuple(r) for r in rows]


async def list_master_batches(db: AsyncSession, *, limit: int, offset: int) -> Tuple[List[tuple], int]:
    total = (await db.execute(select(func.count()).select_from(SalesMasterBatch))).scalar_one()
    rows = (
        await db.execute(
            select(SalesMasterBatch, func.count(SalesDataBatch.id), func.coalesce(func.sum(SalesDataBatch.lead_count), 0))
            .outerjoin(SalesDataBatch, SalesDataBatch.master_batch_id == SalesMasterBatch.id)
            .group_by(SalesMasterBatch.id).order_by(SalesMasterBatch.seq.desc()).limit(limit).offset(offset)
        )
    ).all()
    return [tuple(r) for r in rows], total


async def get_master_batch(db: AsyncSession, batch_id: uuid.UUID) -> Optional[tuple]:
    row = (
        await db.execute(
            select(SalesMasterBatch, func.count(SalesDataBatch.id), func.coalesce(func.sum(SalesDataBatch.lead_count), 0))
            .outerjoin(SalesDataBatch, SalesDataBatch.master_batch_id == SalesMasterBatch.id)
            .where(SalesMasterBatch.id == batch_id).group_by(SalesMasterBatch.id)
        )
    ).first()
    return tuple(row) if row else None


# =============================================================================
# attempt statistics
# =============================================================================

def _stat_columns(a, group_col):
    """The count columns of a statistics row over attempts (`a`: sales_lead_attempts' columns, or a subquery's; grouped by `group_col`)."""
    wait = a.first_outcome == "wait_list"
    return [
        group_col,
        func.count(a.id).label("total"),
        func.count(a.id).filter(a.first_outcome == "accepted").label("accepted_direct"),
        func.count(a.id).filter(a.first_outcome == "rejected").label("rejected_direct"),
        func.count(a.id).filter(wait).label("wait_listed"),
        func.count(a.id).filter(wait, a.result == "accepted").label("wait_accepted"),
        func.count(a.id).filter(wait, a.result == "rejected").label("wait_rejected"),
        func.count(a.id).filter(a.result.is_(None)).label("wait_pending"),
        func.count(a.id).filter(a.result == "released").label("released"),
    ]


_STAT_KEYS = ("total", "accepted_direct", "rejected_direct", "wait_listed", "wait_accepted", "wait_rejected", "wait_pending", "released")


def _stat_dict(row) -> dict:
    return {key: int(getattr(row, key)) for key in _STAT_KEYS}


async def batch_attempt_stats(db: AsyncSession, batch_attempt_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, dict]:
    """The split of each batch attempt over its LEADS: a lead counts once, by its latest attempt in that batch attempt (a lead decided,
    reopened and decided again before its batch formed shows its final result, not both)."""
    if not batch_attempt_ids:
        return {}
    a = SalesLeadAttempt.__table__.c
    ranked = (
        select(
            a.id, a.batch_attempt_id, a.first_outcome, a.result,
            func.row_number().over(partition_by=(a.batch_attempt_id, a.lead_id), order_by=a.attempt_no.desc()).label("rn"),
        )
        .where(a.batch_attempt_id.in_(list(batch_attempt_ids)))
        .subquery()
    )
    rows = (
        await db.execute(select(*_stat_columns(ranked.c, ranked.c.batch_attempt_id)).where(ranked.c.rn == 1).group_by(ranked.c.batch_attempt_id))
    ).all()
    return {row.batch_attempt_id: _stat_dict(row) for row in rows}


async def movable_counts(db: AsyncSession, work_batch_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, int]:
    """How many of each Work Batch's leads a reassignment could still move: not archived, not won, not converted."""
    if not work_batch_ids:
        return {}
    rows = (
        await db.execute(
            select(SalesBatchAttempt.work_batch_id, func.count())
            .select_from(SalesBatchAttemptLead)
            .join(SalesBatchAttempt, and_(SalesBatchAttempt.id == SalesBatchAttemptLead.batch_attempt_id, SalesBatchAttempt.attempt_no == 1))
            .join(SalesLead, SalesLead.id == SalesBatchAttemptLead.lead_id)
            .where(
                SalesBatchAttempt.work_batch_id.in_(list(work_batch_ids)), SalesLead.archived_at.is_(None), SalesLead.pipeline_stage != "won",
                not_(exists().where(SalesCustomer.lead_id == SalesLead.id)),
            )
            .group_by(SalesBatchAttempt.work_batch_id)
        )
    ).all()
    return {batch_id: int(n) for batch_id, n in rows}


# =============================================================================
# work batches
# =============================================================================

async def list_work_batches(
    db: AsyncSession, *, status: Optional[str], employee_id: Optional[uuid.UUID], limit: int, offset: int
) -> Tuple[List[SalesWorkBatch], int]:
    conds = []
    if status:
        conds.append(SalesWorkBatch.status == status)
    if employee_id is not None:      # the batch was ever worked by this person (formed from their leads, or reassigned to them)
        conds.append(exists().where(SalesBatchAttempt.work_batch_id == SalesWorkBatch.id, SalesBatchAttempt.employee_id == employee_id))
    total = (await db.execute(select(func.count()).select_from(SalesWorkBatch).where(*conds))).scalar_one()
    batches = (
        await db.execute(select(SalesWorkBatch).where(*conds).order_by(SalesWorkBatch.seq.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return list(batches), total


async def get_work_batch(db: AsyncSession, batch_id: uuid.UUID) -> Optional[SalesWorkBatch]:
    return (await db.execute(select(SalesWorkBatch).where(SalesWorkBatch.id == batch_id))).scalar_one_or_none()


async def batch_attempts(db: AsyncSession, work_batch_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, List[SalesBatchAttempt]]:
    if not work_batch_ids:
        return {}
    rows = (
        await db.execute(
            select(SalesBatchAttempt).where(SalesBatchAttempt.work_batch_id.in_(list(work_batch_ids)))
            .order_by(SalesBatchAttempt.work_batch_id, SalesBatchAttempt.attempt_no)
        )
    ).scalars().all()
    out: Dict[uuid.UUID, List[SalesBatchAttempt]] = {}
    for row in rows:
        out.setdefault(row.work_batch_id, []).append(row)
    return out


async def work_batch_leads(db: AsyncSession, work_batch_id: uuid.UUID) -> List[tuple]:
    """(ordinal, lead, entered-by name, current owner's name) for every lead of the batch (its initial attempt's members)."""
    rows = (
        await db.execute(
            select(SalesBatchAttemptLead.ordinal, SalesLead, _Creator.name, _Assignee.name)
            .join(SalesBatchAttempt, SalesBatchAttempt.id == SalesBatchAttemptLead.batch_attempt_id)
            .join(SalesLead, SalesLead.id == SalesBatchAttemptLead.lead_id)
            .join(_Creator, _Creator.id == SalesLead.created_by)
            .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
            .where(SalesBatchAttempt.work_batch_id == work_batch_id, SalesBatchAttempt.attempt_no == 1)
            .order_by(SalesBatchAttemptLead.ordinal)
        )
    ).all()
    return [tuple(r) for r in rows]


async def lead_attempts(db: AsyncSession, lead_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, List[tuple]]:
    """Every attempt on each lead: (attempt, salesperson's name, recorded-by name, work batch seq, batch attempt no), oldest first."""
    if not lead_ids:
        return {}
    recorder = aliased(User)
    rows = (
        await db.execute(
            select(SalesLeadAttempt, User.name, recorder.name, SalesWorkBatch.seq, SalesBatchAttempt.attempt_no, SalesWorkBatch.id)
            .join(User, User.id == SalesLeadAttempt.employee_id)
            .join(recorder, recorder.id == SalesLeadAttempt.recorded_by)
            .outerjoin(SalesBatchAttempt, SalesBatchAttempt.id == SalesLeadAttempt.batch_attempt_id)
            .outerjoin(SalesWorkBatch, SalesWorkBatch.id == SalesBatchAttempt.work_batch_id)
            .where(SalesLeadAttempt.lead_id.in_(list(lead_ids))).order_by(SalesLeadAttempt.lead_id, SalesLeadAttempt.attempt_no)
        )
    ).all()
    out: Dict[uuid.UUID, List[tuple]] = {}
    for row in rows:
        out.setdefault(row[0].lead_id, []).append(tuple(row))
    return out


async def lead_memberships(db: AsyncSession, lead_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, List[tuple]]:
    """The Work Batches each lead belongs to: (work batch id, seq, status) once per batch, oldest first."""
    if not lead_ids:
        return {}
    rows = (
        await db.execute(
            select(SalesBatchAttemptLead.lead_id, SalesWorkBatch.id, SalesWorkBatch.seq, SalesWorkBatch.status)
            .join(SalesBatchAttempt, SalesBatchAttempt.id == SalesBatchAttemptLead.batch_attempt_id)
            .join(SalesWorkBatch, SalesWorkBatch.id == SalesBatchAttempt.work_batch_id)
            .where(SalesBatchAttemptLead.lead_id.in_(list(lead_ids)), SalesBatchAttempt.attempt_no == 1)
            .order_by(SalesWorkBatch.seq)
        )
    ).all()
    out: Dict[uuid.UUID, List[tuple]] = {}
    for row in rows:
        out.setdefault(row.lead_id, []).append((row.id, row.seq, row.status))
    return out


# =============================================================================
# global search
# =============================================================================

async def search_leads(db: AsyncSession, *, q: str, field: str, limit: int) -> Tuple[List[tuple], int]:
    """Leads whose business name, person (contact) name, phone or activity type (business_type) contains `q`. Each row is
    (lead, entered-by name, owner's name, data batch seq, data batch status, master batch id, master batch seq)."""
    needle = q.replace("\x00", "").strip()
    pattern = f"%{_escape_like(needle)}%"
    like = lambda column: column.ilike(pattern, escape="\\")          # noqa: E731
    by_field = {
        "business_name": [like(SalesLead.business_name)],
        "contact_name": [like(SalesLead.contact_name)],
        "business_type": [like(SalesLead.business_type)],
        "phone": [like(SalesLead.phone), like(SalesLead.whatsapp)],
    }
    digits = digits_only(needle).lstrip("0") if _PHONE_LOOKING.fullmatch(needle) else ""
    if len(digits) >= 3:                              # another spacing / the national form of a number still finds it
        by_field["phone"] += [SalesLead.phone_norm.like(f"%{digits}%"), SalesLead.whatsapp_norm.like(f"%{digits}%")]
    clauses = [c for cs in by_field.values() for c in cs] if field == "any" else by_field[field]
    cond = or_(*clauses)

    total = (await db.execute(select(func.count()).select_from(SalesLead).where(cond))).scalar_one()
    rows = (
        await db.execute(
            select(
                SalesLead, _Creator.name, _Assignee.name, SalesDataBatch.seq, SalesDataBatch.status,
                SalesMasterBatch.id, SalesMasterBatch.seq,
            )
            .join(_Creator, _Creator.id == SalesLead.created_by)
            .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
            .outerjoin(SalesDataBatch, SalesDataBatch.id == SalesLead.data_batch_id)
            .outerjoin(SalesMasterBatch, SalesMasterBatch.id == SalesDataBatch.master_batch_id)
            .where(cond).order_by(SalesLead.created_at.desc(), SalesLead.id).limit(limit)
        )
    ).all()
    return [tuple(r) for r in rows], total


async def trace_lead(db: AsyncSession, lead_id: uuid.UUID) -> Optional[tuple]:
    row = (
        await db.execute(
            select(
                SalesLead, _Creator.name, _Assignee.name, SalesDataBatch.seq, SalesDataBatch.status,
                SalesMasterBatch.id, SalesMasterBatch.seq,
            )
            .join(_Creator, _Creator.id == SalesLead.created_by)
            .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
            .outerjoin(SalesDataBatch, SalesDataBatch.id == SalesLead.data_batch_id)
            .outerjoin(SalesMasterBatch, SalesMasterBatch.id == SalesDataBatch.master_batch_id)
            .where(SalesLead.id == lead_id)
        )
    ).first()
    return tuple(row) if row else None


# =============================================================================
# performance
# =============================================================================

@dataclass(frozen=True)
class Period:
    key: str
    start: Optional[datetime]      # inclusive; None = from the beginning
    end: Optional[datetime]        # exclusive; None = until now and beyond


def _between(column, period: Period):
    conds = []
    if period.start is not None:
        conds.append(column >= period.start)
    if period.end is not None:
        conds.append(column < period.end)
    return and_(*conds) if conds else true()


def _holds(user_col, permission: str):
    """EXISTS: the user holds `permission` through an ACTIVE grant of an ACTIVE role."""
    return exists(
        select(1).select_from(StaffUserRole)
        .join(StaffRole, and_(StaffRole.id == StaffUserRole.role_id, StaffRole.is_active.is_(True)))
        .join(StaffRolePermission, and_(StaffRolePermission.role_id == StaffRole.id, StaffRolePermission.permission_key == permission))
        .where(StaffUserRole.user_id == user_col, StaffUserRole.revoked_at.is_(None))
    )


def _staff():
    return and_(User.role == perms.PLATFORM_ROLE_STAFF, User.deleted_at.is_(None))


def _is_data_entry():
    """Data Entry staff: everyone who works the intake scope (without the all-leads scope) now, or ever entered a lead into a Data
    Batch - a person who left keeps their history."""
    entered = exists().where(SalesLead.created_by == User.id, SalesLead.data_batch_id.is_not(None))
    now_intake = and_(_holds(User.id, perms.PERM_LEADS_SCOPE_INTAKE), not_(_holds(User.id, perms.PERM_LEADS_SCOPE_ALL)))
    return and_(_staff(), or_(now_intake, entered))


def _is_sales():
    """Sales staff: everyone who works assigned leads now, or ever made a decision on a lead."""
    worked = exists().where(SalesLeadAttempt.employee_id == User.id)
    return and_(_staff(), or_(_holds(User.id, perms.PERM_LEADS_SCOPE_ASSIGNED), worked))


async def _people(db: AsyncSession, cond, *, q: Optional[str], limit: int, offset: int) -> Tuple[List[User], int]:
    conds = [cond]
    needle = (q or "").replace("\x00", "").strip()
    if needle:
        conds.append(User.name.ilike(f"%{_escape_like(needle)}%", escape="\\"))
    total = (await db.execute(select(func.count()).select_from(User).where(*conds))).scalar_one()
    rows = (await db.execute(select(User).where(*conds).order_by(User.name, User.id).limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def data_entry_people(db: AsyncSession, *, q: Optional[str] = None, limit: int = 25, offset: int = 0) -> Tuple[List[User], int]:
    return await _people(db, _is_data_entry(), q=q, limit=limit, offset=offset)


async def sales_people(db: AsyncSession, *, q: Optional[str] = None, limit: int = 25, offset: int = 0) -> Tuple[List[User], int]:
    return await _people(db, _is_sales(), q=q, limit=limit, offset=offset)


async def is_data_entry_person(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return (await db.execute(select(User.id).where(User.id == user_id, _is_data_entry()))).first() is not None


async def is_sales_person(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return (await db.execute(select(User.id).where(User.id == user_id, _is_sales()))).first() is not None


async def get_staff_user(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    return (
        await db.execute(select(User).where(User.id == user_id, User.role == perms.PLATFORM_ROLE_STAFF, User.deleted_at.is_(None)))
    ).scalar_one_or_none()


async def leads_entered(db: AsyncSession, user_ids: Sequence[uuid.UUID], periods: Sequence[Period]) -> Dict[uuid.UUID, Dict[str, int]]:
    """Leads each person entered, per period (created_by / created_at are immutable, so this is the permanent record)."""
    if not user_ids:
        return {}
    cols = [func.count(SalesLead.id).filter(_between(SalesLead.created_at, p)).label(p.key) for p in periods]
    rows = (await db.execute(select(SalesLead.created_by, *cols).where(SalesLead.created_by.in_(list(user_ids))).group_by(SalesLead.created_by))).all()
    return {row.created_by: {p.key: int(getattr(row, p.key)) for p in periods} for row in rows}


async def sales_stats(db: AsyncSession, user_ids: Sequence[uuid.UUID], periods: Sequence[Period]) -> Dict[uuid.UUID, Dict[str, dict]]:
    """Each salesperson's decisions, per period. `worked` = attempts started in the period; the two wait-list RESULTS are counted
    in the period they happened (result_at), everything else in the period of the decision (outcome_at)."""
    if not user_ids:
        return {}
    a = SalesLeadAttempt
    cols = []
    for p in periods:
        wait = a.first_outcome == "wait_list"
        started = _between(a.outcome_at, p)
        cols += [
            func.count(a.id).filter(started).label(f"{p.key}__worked"),
            func.count(a.id).filter(started, a.first_outcome == "accepted").label(f"{p.key}__accepted_direct"),
            func.count(a.id).filter(started, a.first_outcome == "rejected").label(f"{p.key}__rejected_direct"),
            func.count(a.id).filter(started, wait).label(f"{p.key}__wait_listed"),
            func.count(a.id).filter(wait, a.result == "accepted", _between(a.result_at, p)).label(f"{p.key}__wait_accepted"),
            func.count(a.id).filter(wait, a.result == "rejected", _between(a.result_at, p)).label(f"{p.key}__wait_rejected"),
            func.count(a.id).filter(started, a.result.is_(None)).label(f"{p.key}__wait_pending"),
        ]
    rows = (await db.execute(select(a.employee_id, *cols).where(a.employee_id.in_(list(user_ids))).group_by(a.employee_id))).all()
    keys = ("worked", "accepted_direct", "rejected_direct", "wait_listed", "wait_accepted", "wait_rejected", "wait_pending")
    return {
        row.employee_id: {p.key: {k: int(getattr(row, f"{p.key}__{k}")) for k in keys} for p in periods} for row in rows
    }


async def completed_batches(db: AsyncSession, user_ids: Sequence[uuid.UUID], periods: Sequence[Period]) -> Dict[uuid.UUID, Dict[str, int]]:
    """Sales Work Batch attempts each person completed (closed), per period of their closing."""
    if not user_ids:
        return {}
    b = SalesBatchAttempt
    cols = [func.count(b.id).filter(b.status == "closed", _between(b.closed_at, p)).label(p.key) for p in periods]
    rows = (await db.execute(select(b.employee_id, *cols).where(b.employee_id.in_(list(user_ids))).group_by(b.employee_id))).all()
    return {row.employee_id: {p.key: int(getattr(row, p.key)) for p in periods} for row in rows}


async def work_seconds(db: AsyncSession, user_ids: Sequence[uuid.UUID], periods: Sequence[Period]) -> Dict[uuid.UUID, Dict[str, int]]:
    """Seconds of recorded work per person and period: each session's [started_at, ended_at + tail credit], clipped to the period."""
    if not user_ids:
        return {}
    s = SalesWorkSession
    end_credited = s.ended_at + SESSION_TAIL_CREDIT
    cols = []
    for p in periods:
        # explicit timestamptz binds: a bare aware datetime would be typed `timestamp without time zone` by the driver
        lo = func.greatest(s.started_at, literal(p.start, DateTime(timezone=True))) if p.start is not None else s.started_at
        hi = func.least(end_credited, literal(p.end, DateTime(timezone=True))) if p.end is not None else end_credited
        overlap = and_(
            s.started_at < p.end if p.end is not None else true(),
            end_credited > p.start if p.start is not None else true(),
        )
        cols.append(func.coalesce(func.sum(func.extract("epoch", hi - lo)).filter(overlap), 0).label(p.key))
    rows = (await db.execute(select(s.user_id, *cols).where(s.user_id.in_(list(user_ids))).group_by(s.user_id))).all()
    return {row.user_id: {p.key: max(0, int(getattr(row, p.key))) for p in periods} for row in rows}


def _local_day(column):
    """The calendar day (application timezone) of a timestamptz column."""
    local = func.timezone(literal_column("'UTC'"), column) + literal_column(f"interval '{APP_UTC_OFFSET_HOURS} hours'")
    return cast(local, Date)


async def daily_history(db: AsyncSession, user_id: uuid.UUID, since: datetime) -> Dict[date, Dict[str, int]]:
    """The person's permanent record, per calendar day (application timezone) since `since`: leads entered, decisions, hours.
    A work session belongs to the day it started."""
    days: Dict[date, Dict[str, int]] = {}

    def put(day, **values):
        days.setdefault(day, {}).update(values)

    day = _local_day(SalesLead.created_at)
    for d, n in (
        await db.execute(select(day, func.count()).where(SalesLead.created_by == user_id, SalesLead.created_at >= since).group_by(day))
    ).all():
        put(d, leads_entered=int(n))

    a = SalesLeadAttempt
    day = _local_day(a.outcome_at)
    for row in (
        await db.execute(
            select(
                day.label("d"), func.count(a.id).label("worked"),
                func.count(a.id).filter(a.first_outcome == "accepted").label("accepted_direct"),
                func.count(a.id).filter(a.first_outcome == "rejected").label("rejected_direct"),
                func.count(a.id).filter(a.first_outcome == "wait_list").label("wait_listed"),
            ).where(a.employee_id == user_id, a.outcome_at >= since).group_by(day)
        )
    ).all():
        put(row.d, worked=int(row.worked), accepted_direct=int(row.accepted_direct), rejected_direct=int(row.rejected_direct), wait_listed=int(row.wait_listed))

    day = _local_day(a.result_at)
    for row in (
        await db.execute(
            select(
                day.label("d"),
                func.count(a.id).filter(a.first_outcome == "wait_list", a.result == "accepted").label("wait_accepted"),
                func.count(a.id).filter(a.first_outcome == "wait_list", a.result == "rejected").label("wait_rejected"),
            ).where(a.employee_id == user_id, a.result_at >= since).group_by(day)
        )
    ).all():
        put(row.d, wait_accepted=int(row.wait_accepted), wait_rejected=int(row.wait_rejected))

    s = SalesWorkSession
    day = _local_day(s.started_at)
    seconds = func.extract("epoch", s.ended_at + SESSION_TAIL_CREDIT - s.started_at)
    for d, secs in (
        await db.execute(select(day, func.coalesce(func.sum(seconds), 0)).where(s.user_id == user_id, s.started_at >= since).group_by(day))
    ).all():
        put(d, work_seconds=int(secs))
    return days


async def recent_sessions(db: AsyncSession, user_id: uuid.UUID, limit: int) -> List[SalesWorkSession]:
    return list(
        (
            await db.execute(select(SalesWorkSession).where(SalesWorkSession.user_id == user_id).order_by(SalesWorkSession.ended_at.desc()).limit(limit))
        ).scalars().all()
    )
