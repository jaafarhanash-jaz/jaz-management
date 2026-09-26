"""The Sales Manager's batch, search and performance views (migration f2b6d8a1c4e9) - shaping only.

The queries live in repositories/batches.py; this module turns their rows into the API shapes (batch_schemas.py), computes the
statistics and percentages, and resolves names in bulk. Every function is reached only from routes guarded by
sales.batches.view / sales.performance.view AND sales.leads.scope_all - the Data Entry and Sales Employee APIs never call it,
and no shape here carries a batch id into a lead, queue or timeline response.

Unknown, malformed and out-of-scope ids answer 404 (the Sales convention); a well-formed id of a batch that exists is shown to
whoever holds the view permission - there is one Sales Manager view of everything, no per-row scope below it.
"""
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import DATA_BATCH_SIZE, SESSION_TAIL_CREDIT
from sales.repositories import batches as repo
from sales.repositories.batches import Period
from sales.services import activity as activity_service
from sales.services import batches as batches_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.timezone import app_today, day_start
from services.admin import parse_uuid

PERIOD_KEYS = ("today", "month", "year", "lifetime")
MAX_HISTORY_DAYS = 366


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _person(user_id, names: Dict[uuid.UUID, str]) -> dict:
    return {"id": str(user_id), "name": names.get(user_id, "")}


def _uuid(value: str, what: str) -> uuid.UUID:
    parsed = parse_uuid(value)
    if parsed is None:
        raise _not_found(what)
    return parsed


# =============================================================================
# statistics
# =============================================================================

def stats_out(counts: Optional[dict], *, lead_count: Optional[int] = None) -> dict:
    """The statistics shape from the raw counts of repositories/batches (a missing row = all zero). `lead_count` (a batch attempt's
    size) adds the leads nobody has decided yet on a reassignment."""
    c = {k: int((counts or {}).get(k, 0)) for k in (
        "total", "accepted_direct", "rejected_direct", "wait_listed", "wait_accepted", "wait_rejected", "wait_pending", "released",
    )}
    accepted = c["accepted_direct"] + c["wait_accepted"]
    rejected = c["rejected_direct"] + c["wait_rejected"]
    decided = accepted + rejected
    return {
        **c,
        "not_started": max(0, lead_count - c["total"]) if lead_count is not None else 0,
        "accepted": accepted, "rejected": rejected, "decided": decided,
        "pct_accepted_direct": _pct(c["accepted_direct"], decided), "pct_rejected_direct": _pct(c["rejected_direct"], decided),
        "pct_wait_accepted": _pct(c["wait_accepted"], decided), "pct_wait_rejected": _pct(c["wait_rejected"], decided),
        "pct_accepted": _pct(accepted, decided), "pct_rejected": _pct(rejected, decided),
        "wait_list_rate": _pct(c["wait_listed"], c["total"]),
    }


def _performance_stats(counts: Optional[dict]) -> dict:
    """The same shape for a person's period (their decisions - repositories/batches.sales_stats keys: `worked` is the total)."""
    c = dict(counts or {})
    c["total"] = c.pop("worked", 0)
    c.setdefault("released", 0)
    return stats_out(c)


def attempt_path(first_outcome: str, result: Optional[str]) -> str:
    if first_outcome == "accepted":
        return "direct_accepted"
    if first_outcome == "rejected":
        return "direct_rejected"
    return {"accepted": "wait_accepted", "rejected": "wait_rejected", "released": "released", None: "wait_pending"}[result]


def _hours(seconds: Optional[int]) -> float:
    return round((seconds or 0) / 3600.0, 2)


# =============================================================================
# overview / data batches / master batches
# =============================================================================

async def overview(db: AsyncSession) -> dict:
    # the safety net for a Work Batch whose formation failed after its decision committed (services/batches.settle)
    await batches_service.form_all_due_work_batches(db)
    data = await repo.overview(db)
    if data["open_data_batch"]:
        data["open_data_batch"]["id"] = str(data["open_data_batch"]["id"])
    return data


def _data_batch_out(batch, master_seq: Optional[int]) -> dict:
    return {
        "id": str(batch.id), "seq": batch.seq, "status": batch.status, "lead_count": batch.lead_count, "capacity": DATA_BATCH_SIZE,
        "opened_at": batch.opened_at, "filled_at": batch.filled_at,
        "master_batch": {"id": str(batch.master_batch_id), "seq": master_seq} if batch.master_batch_id else None,
    }


def _batch_lead(lead, creator: str, assignee: Optional[str]) -> dict:
    return {
        "id": str(lead.id), "business_name": lead.business_name, "business_type": lead.business_type, "contact_name": lead.contact_name,
        "phone": lead.phone, "city": lead.city, "pipeline_stage": lead.pipeline_stage,
        "entered_by": {"id": str(lead.created_by), "name": creator or ""}, "entered_at": lead.created_at,
        "assigned_to": {"id": str(lead.assigned_to), "name": assignee or ""} if lead.assigned_to else None,
        "archived": lead.archived_at is not None,
    }


async def list_data_batches(
    db: AsyncSession, *, status: Optional[str], master_batch_id: Optional[str], limit: int, offset: int
) -> dict:
    master = _uuid(master_batch_id, "Master batch") if master_batch_id else None
    rows, total = await repo.list_data_batches(db, status=status, master_batch_id=master, limit=limit, offset=offset)
    return {"items": [_data_batch_out(b, seq) for b, seq in rows], "total": total, "limit": limit, "offset": offset}


async def get_data_batch(db: AsyncSession, batch_id: str) -> dict:
    row = await repo.get_data_batch(db, _uuid(batch_id, "Data batch"))
    if row is None:
        raise _not_found("Data batch")
    batch, master_seq = row
    leads = await repo.data_batch_leads(db, batch.id)
    entrants = await repo.data_batch_entrants(db, batch.id)
    return {
        **_data_batch_out(batch, master_seq),
        "entrants": [{"id": str(uid), "name": name, "leads": int(n)} for uid, name, n in entrants],
        "leads": [_batch_lead(lead, creator, assignee) for lead, creator, assignee in leads],
    }


def _master_out(row) -> dict:
    master, batch_count, lead_count = row
    return {"id": str(master.id), "seq": master.seq, "formed_at": master.formed_at, "data_batch_count": int(batch_count), "lead_count": int(lead_count)}


async def list_master_batches(db: AsyncSession, *, limit: int, offset: int) -> dict:
    rows, total = await repo.list_master_batches(db, limit=limit, offset=offset)
    return {"items": [_master_out(r) for r in rows], "total": total, "limit": limit, "offset": offset}


async def get_master_batch(db: AsyncSession, batch_id: str) -> dict:
    parsed = _uuid(batch_id, "Master batch")
    row = await repo.get_master_batch(db, parsed)
    if row is None:
        raise _not_found("Master batch")
    rows, _ = await repo.list_data_batches(db, status=None, master_batch_id=parsed, limit=100, offset=0)
    return {**_master_out(row), "data_batches": [_data_batch_out(b, seq) for b, seq in reversed(rows)]}


# =============================================================================
# work batches
# =============================================================================

def _attempt_out(attempt, names: Dict[uuid.UUID, str], stats: Optional[dict]) -> dict:
    return {
        "id": str(attempt.id), "attempt_no": attempt.attempt_no, "kind": attempt.kind, "status": attempt.status,
        "employee": _person(attempt.employee_id, names), "started_by": _person(attempt.started_by, names),
        "started_at": attempt.started_at, "closed_at": attempt.closed_at, "lead_count": attempt.lead_count,
        "stats": stats_out(stats, lead_count=attempt.lead_count),
    }


def _work_batch_out(
    ctx: StaffContext, batch, attempts: Sequence, stats: Dict[uuid.UUID, dict], names: Dict[uuid.UUID, str], movable: Dict[uuid.UUID, int],
) -> dict:
    first, latest = attempts[0], attempts[-1]
    return {
        "id": str(batch.id), "seq": batch.seq, "status": batch.status, "lead_count": batch.lead_count,
        "created_at": batch.created_at, "closed_at": batch.closed_at,
        "original_employee": _person(first.employee_id, names), "current_employee": _person(latest.employee_id, names),
        "attempt_count": len(attempts), "stats": stats_out(stats.get(first.id), lead_count=first.lead_count),
        # completed, the caller may hand it on, and a lead is left to hand on (not every one won, converted or archived)
        "can_reassign": batch.status == "closed" and ctx.has(perms.PERM_BATCHES_REASSIGN) and movable.get(batch.id, 0) > 0,
    }


async def _batches_context(db: AsyncSession, batches: Sequence):
    by_batch = await repo.batch_attempts(db, [b.id for b in batches])
    all_attempts = [a for attempts in by_batch.values() for a in attempts]
    stats = await repo.batch_attempt_stats(db, [a.id for a in all_attempts])
    names = await repo.user_names(db, [a.employee_id for a in all_attempts] + [a.started_by for a in all_attempts])
    movable = await repo.movable_counts(db, [b.id for b in batches])
    return by_batch, stats, names, movable


async def list_work_batches(
    db: AsyncSession, ctx: StaffContext, *, status: Optional[str], employee_id: Optional[str], limit: int, offset: int
) -> dict:
    employee = _uuid(employee_id, "Employee") if employee_id else None
    batches, total = await repo.list_work_batches(db, status=status, employee_id=employee, limit=limit, offset=offset)
    by_batch, stats, names, movable = await _batches_context(db, batches)
    return {
        "items": [_work_batch_out(ctx, b, by_batch[b.id], stats, names, movable) for b in batches], "total": total, "limit": limit, "offset": offset,
    }


def _lead_attempt_out(row) -> dict:
    attempt, employee, recorder, batch_seq, batch_attempt_no, _batch_id = row
    return {
        "attempt_no": attempt.attempt_no, "employee": {"id": str(attempt.employee_id), "name": employee},
        "recorded_by": {"id": str(attempt.recorded_by), "name": recorder}, "first_outcome": attempt.first_outcome,
        "outcome_at": attempt.outcome_at, "wait_listed_at": attempt.wait_listed_at, "result": attempt.result,
        "result_at": attempt.result_at, "path": attempt_path(attempt.first_outcome, attempt.result),
        "work_batch_seq": batch_seq, "batch_attempt_no": batch_attempt_no,
    }


async def get_work_batch(db: AsyncSession, ctx: StaffContext, batch_id: str) -> dict:
    batch = await repo.get_work_batch(db, _uuid(batch_id, "Work batch"))
    if batch is None:
        raise _not_found("Work batch")
    by_batch, stats, names, movable = await _batches_context(db, [batch])
    attempts = by_batch[batch.id]
    leads = await repo.work_batch_leads(db, batch.id)
    history = await repo.lead_attempts(db, [lead.id for _, lead, _, _ in leads])
    return {
        **_work_batch_out(ctx, batch, attempts, stats, names, movable),
        "attempts": [_attempt_out(a, names, stats.get(a.id)) for a in attempts],
        "leads": [
            {**_batch_lead(lead, creator, assignee), "ordinal": ordinal, "attempts": [_lead_attempt_out(r) for r in history.get(lead.id, [])]}
            for ordinal, lead, creator, assignee in leads
        ],
    }


async def reassign_work_batch(
    db: AsyncSession, ctx: StaffContext, audit: AuditContext, batch_id: str, employee_id: uuid.UUID
) -> dict:
    """Hand a completed batch to another salesperson (services/batches.reassign_work_batch), then show the batch as it now stands."""
    batch = await batches_service.reassign_work_batch(db, ctx, audit, batch_id, employee_id)
    return await get_work_batch(db, ctx, str(batch))


# =============================================================================
# search and trace
# =============================================================================

def _trace_lead(row, attempts: Sequence, memberships: Sequence) -> dict:
    lead, creator, assignee, data_seq, data_status, master_id, master_seq = row
    return {
        **_batch_lead(lead, creator, assignee), "email": lead.email, "whatsapp": lead.whatsapp,
        "data_batch": {"id": str(lead.data_batch_id), "seq": data_seq, "status": data_status} if lead.data_batch_id else None,
        "master_batch": {"id": str(master_id), "seq": master_seq} if master_id else None,
        "work_batches": [{"id": str(bid), "seq": seq, "status": status} for bid, seq, status in memberships],
        "attempts": [_lead_attempt_out(a) for a in attempts],
    }


async def search(db: AsyncSession, *, q: str, field: str, limit: int) -> dict:
    """Leads matching the text (business name, person name, phone or activity type), each with its trace: Data Batch, Master Batch,
    Work Batches and every attempt with its result."""
    rows, total = await repo.search_leads(db, q=q, field=field, limit=limit)
    ids = [r[0].id for r in rows]
    attempts = await repo.lead_attempts(db, ids)
    memberships = await repo.lead_memberships(db, ids)
    return {"items": [_trace_lead(r, attempts.get(r[0].id, []), memberships.get(r[0].id, [])) for r in rows], "total": total, "limit": limit}


async def trace(db: AsyncSession, ctx: StaffContext, lead_id: str, *, timeline_limit: int = 100) -> dict:
    parsed = _uuid(lead_id, "Lead")
    row = await repo.trace_lead(db, parsed)
    if row is None:
        raise _not_found("Lead")
    attempts = await repo.lead_attempts(db, [parsed])
    memberships = await repo.lead_memberships(db, [parsed])
    events = await activity_service.list_events(db, parsed, timeline_limit, 0, activity_service.visible_event_types(ctx))
    return {
        **_trace_lead(row, attempts.get(parsed, []), memberships.get(parsed, [])),
        "timeline": events["items"], "timeline_total": events["total"],
    }


# =============================================================================
# performance
# =============================================================================

def periods(now: Optional[datetime] = None) -> List[Period]:
    """today / this month / this year in the application timezone (sales/timezone.py), and lifetime (unbounded)."""
    today = app_today(now)
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return [
        Period("today", day_start(today), day_start(today + timedelta(days=1))),
        Period("month", day_start(month_start), day_start(next_month)),
        Period("year", day_start(today.replace(month=1, day=1)), day_start(date(today.year + 1, 1, 1))),
        Period("lifetime", None, None),
    ]


def _period_starts(ps: Sequence[Period]) -> Dict[str, Optional[datetime]]:
    return {p.key: p.start for p in ps}


def _data_entry_block(user_id, entered, seconds, ps: Sequence[Period]) -> dict:
    return {
        p.key: {"leads_entered": (entered.get(user_id) or {}).get(p.key, 0), "work_hours": _hours((seconds.get(user_id) or {}).get(p.key))}
        for p in ps
    }


def _sales_block(user_id, stats, batches, seconds, ps: Sequence[Period]) -> dict:
    return {
        p.key: {
            "stats": _performance_stats(((stats.get(user_id) or {}).get(p.key))),
            "completed_batches": (batches.get(user_id) or {}).get(p.key, 0),
            "work_hours": _hours((seconds.get(user_id) or {}).get(p.key)),
        }
        for p in ps
    }


async def data_entry_performance(db: AsyncSession, *, q: Optional[str], limit: int, offset: int) -> dict:
    ps = periods()
    people, total = await repo.data_entry_people(db, q=q, limit=limit, offset=offset)
    ids = [u.id for u in people]
    entered, seconds = await repo.leads_entered(db, ids, ps), await repo.work_seconds(db, ids, ps)
    return {
        "period_starts": _period_starts(ps),
        "items": [
            {"user": {"id": str(u.id), "name": u.name}, "status": u.status, "periods": _data_entry_block(u.id, entered, seconds, ps)}
            for u in people
        ],
        "total": total, "limit": limit, "offset": offset,
    }


async def sales_performance(db: AsyncSession, *, q: Optional[str], limit: int, offset: int) -> dict:
    ps = periods()
    people, total = await repo.sales_people(db, q=q, limit=limit, offset=offset)
    ids = [u.id for u in people]
    stats, batches, seconds = await repo.sales_stats(db, ids, ps), await repo.completed_batches(db, ids, ps), await repo.work_seconds(db, ids, ps)
    return {
        "period_starts": _period_starts(ps),
        "items": [
            {"user": {"id": str(u.id), "name": u.name}, "status": u.status, "periods": _sales_block(u.id, stats, batches, seconds, ps)}
            for u in people
        ],
        "total": total, "limit": limit, "offset": offset,
    }


async def _staff_user(db: AsyncSession, user_id: str):
    """A Data Entry or Sales employee (now, or by their recorded history). Anybody else - a peer Sales Manager, a staff account with
    no Sales role, a customer - is not somebody this view reports on: 404, like an unknown id."""
    user = await repo.get_staff_user(db, _uuid(user_id, "Employee"))
    if user is None or not (await repo.is_data_entry_person(db, user.id) or await repo.is_sales_person(db, user.id)):
        raise _not_found("Employee")
    return user


async def employee_performance(db: AsyncSession, user_id: str) -> dict:
    user = await _staff_user(db, user_id)
    ps = periods()
    ids = [user.id]
    is_data_entry = await repo.is_data_entry_person(db, user.id)
    is_sales = await repo.is_sales_person(db, user.id)
    seconds = await repo.work_seconds(db, ids, ps)
    out = {"user": {"id": str(user.id), "name": user.name}, "status": user.status, "period_starts": _period_starts(ps), "data_entry": None, "sales": None}
    if is_data_entry:
        out["data_entry"] = _data_entry_block(user.id, await repo.leads_entered(db, ids, ps), seconds, ps)
    if is_sales:
        out["sales"] = _sales_block(
            user.id, await repo.sales_stats(db, ids, ps), await repo.completed_batches(db, ids, ps), seconds, ps
        )
    return out


async def employee_history(db: AsyncSession, user_id: str, *, days: int, sessions: int) -> dict:
    """The person's permanent record day by day (newest first) and their latest work sessions."""
    user = await _staff_user(db, user_id)
    days = max(1, min(days, MAX_HISTORY_DAYS))
    since = day_start(app_today() - timedelta(days=days - 1))
    per_day = await repo.daily_history(db, user.id, since)
    rows = []
    for day in sorted(per_day, reverse=True):
        values = dict(per_day[day])
        values["work_hours"] = _hours(values.pop("work_seconds", 0))
        rows.append({"date": day, **values})
    recent = await repo.recent_sessions(db, user.id, max(1, min(sessions, 200)))
    return {
        "user": {"id": str(user.id), "name": user.name},
        "days": rows,
        "sessions": [
            {
                "started_at": s.started_at, "ended_at": s.ended_at, "actions": s.actions,
                "minutes": round(((s.ended_at - s.started_at) + SESSION_TAIL_CREDIT).total_seconds() / 60.0, 1),
            }
            for s in recent
        ],
    }

