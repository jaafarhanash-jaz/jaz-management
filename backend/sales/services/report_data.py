"""The computations the dashboard and the reports share: turning the repository's aggregates into the API's shapes.

Nothing here reads a raw record. The repository has already counted; this module only merges (the per-employee lead
counts with the per-employee activity counts), rounds, and shapes. Every result is a plain dict that the response model
validates.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import CALL_RESULTS, DEMO_STATUSES, FOLLOWUP_STATUSES, PIPELINE_STAGES, TRIAL_STATUSES
from sales.metrics import ACTIVITY_KINDS, Period, rate
from sales.repositories import reports as repo
from sales.repositories.reports import LeadMeasures, StageTotal
from sales.services.report_scope import ReportScope, viewable_kinds

_BREAKDOWN_KEYS = {
    "calls": CALL_RESULTS,
    "followups": FOLLOWUP_STATUSES,
    "demos": DEMO_STATUSES,
    "trials": TRIAL_STATUSES,
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def period_out(period: Optional[Period]) -> Optional[dict]:
    if period is None:
        return None
    return {"range": period.range, "date_from": period.date_from, "date_to": period.date_to, "days": period.days}


def person(user_id: Optional[uuid.UUID], name: Optional[str]) -> Optional[dict]:
    return None if user_id is None else {"id": str(user_id), "name": name or ""}


async def header(db: AsyncSession, scope: ReportScope, period: Optional[Period]) -> dict:
    """The part of every response that says what it is about: the period, the scope, the narrowed employee, the time."""
    employee = None
    if scope.subject is not None:
        names = await repo.names_for(db, [scope.subject])
        employee = person(scope.subject, names.get(scope.subject))
    return {"period": period_out(period), "scope": scope.name, "employee": employee, "as_of": now()}


# ---- lead figures ----------------------------------------------------------------------------------------------------
def lead_figures(m: LeadMeasures) -> dict:
    return {
        "leads": m.leads, "won": m.won, "lost": m.lost, "open": m.open, "conversion_rate": rate(m.won, m.leads),
        "value": round(m.value, 2), "won_value": round(m.won_value, 2),
    }


def add_measures(rows: Sequence[LeadMeasures]) -> LeadMeasures:
    return LeadMeasures(
        sum(r.leads for r in rows), sum(r.won for r in rows), sum(r.lost for r in rows),
        sum(r.value for r in rows), sum(r.won_value for r in rows),
    )


def campaign_row(c: repo.CampaignRow) -> dict:
    return {"campaign_id": str(c.id), "name": c.name, "status": c.status, "source": c.source, **lead_figures(c.measures)}


def pipeline_rows(totals: Dict[str, StageTotal]) -> List[dict]:
    return [
        {"stage": stage, "leads": totals[stage].leads, "value": round(totals[stage].value, 2), "valued": totals[stage].valued}
        for stage in PIPELINE_STAGES
    ]


# ---- activity ----------------------------------------------------------------------------------------------------------
@dataclass
class ActivityRollup:
    """The work whose date is in the period, for the kinds the caller may see."""

    viewable: Tuple[str, ...]
    kinds: Dict[str, Optional[dict]] = field(default_factory=dict)            # kind -> {"total", "breakdown"} | None
    per_person: Dict[uuid.UUID, Dict[str, int]] = field(default_factory=dict)  # person -> kind -> counted activity

    @property
    def total(self) -> int:
        return sum(k["total"] for k in self.kinds.values() if k is not None)


async def activity_rollup(db: AsyncSession, scope: ReportScope) -> ActivityRollup:
    """At most four queries (one per viewable kind), each already grouped by person and status."""
    rollup = ActivityRollup(viewable=viewable_kinds(scope.ctx))
    for kind in ACTIVITY_KINDS:
        if kind not in rollup.viewable:
            rollup.kinds[kind] = None
            continue
        breakdown = {key: 0 for key in _BREAKDOWN_KEYS[kind]}
        counted = 0
        for who, status, n in await repo.activity_by_person(db, kind, visibility=scope.visibility, period=scope.period, actor=scope.actor):
            breakdown[status] = breakdown.get(status, 0) + n
            if repo.counts_as_activity(kind, status):
                counted += n
                rollup.per_person.setdefault(who, {}).setdefault(kind, 0)
                rollup.per_person[who][kind] += n
        rollup.kinds[kind] = {"total": counted, "breakdown": breakdown}
    return rollup


def _person_activity(rollup: ActivityRollup, who: Optional[uuid.UUID]) -> Dict[str, Optional[int]]:
    """{kind: count} for one person; None for a kind the caller may not see (unknown is not zero)."""
    mine = rollup.per_person.get(who, {}) if who is not None else {}
    return {kind: (mine.get(kind, 0) if kind in rollup.viewable else None) for kind in ACTIVITY_KINDS}


def _sum_kinds(counts: Dict[str, Optional[int]]) -> int:
    return sum(v for v in counts.values() if v is not None)


async def performance_rows(
    db: AsyncSession, lead_groups: Optional[List[Tuple[Optional[uuid.UUID], Optional[str], LeadMeasures]]], rollup: ActivityRollup,
    idle: Optional[List[Tuple[uuid.UUID, str]]] = None,
) -> List[dict]:
    """One row per employee that has leads (lead_groups) or activity (the rollup) in the period - the unassigned leads as a
    row with no employee - busiest first. `lead_groups=None` (the caller may not see leads) gives activity-only rows.
    `idle` (id, name) are staff to list even with nothing to report: a manager comparing employees must see the one who did
    nothing, not have them silently missing (zeros, at the end, in name order)."""
    groups = lead_groups or []
    names: Dict[uuid.UUID, str] = {uid: name or "" for uid, name, _ in groups if uid is not None}
    names.update({uid: name for uid, name in (idle or []) if uid not in names})
    missing = [who for who in rollup.per_person if who not in names]
    names.update(await repo.names_for(db, missing))

    rows: Dict[Optional[uuid.UUID], dict] = {}

    def blank(who: Optional[uuid.UUID]) -> dict:
        activity = _person_activity(rollup, who)
        return {
            "employee": person(who, names.get(who)), "leads": 0, "won": 0, "lost": 0, "open": 0, "conversion_rate": None,
            **activity, "activities": _sum_kinds(activity),
        }

    for who, _, m in groups:
        row = blank(who)
        row.update(leads=m.leads, won=m.won, lost=m.lost, open=m.open, conversion_rate=rate(m.won, m.leads))
        rows[who] = row
    for who in rollup.per_person:
        rows.setdefault(who, blank(who))
    for who, _ in idle or []:
        rows.setdefault(who, blank(who))

    return sorted(rows.values(), key=lambda r: (-r["leads"], -r["activities"], (r["employee"] or {"name": ""})["name"].lower(), (r["employee"] or {"id": ""})["id"]))


async def idle_staff(db: AsyncSession, scope: ReportScope) -> Optional[List[Tuple[uuid.UUID, str]]]:
    """The staff to list in a performance table even when they have nothing to report - only for a TEAM caller looking at the
    whole team (not narrowed to one employee) who may use the assignment picker (sales.leads.assign), which already reveals
    exactly these people. Anybody else gets None: no extra names, no extra query."""
    if scope.team and scope.subject is None and scope.ctx.has(perms.PERM_LEADS_ASSIGN):
        return await repo.assignable_staff(db)
    return None


def performance_totals(rows: Sequence[dict], rollup: ActivityRollup) -> dict:
    """The row that adds up the rows (activity totals come from the rollup, so an employee the page cut off still counts)."""
    leads, won, lost = sum(r["leads"] for r in rows), sum(r["won"] for r in rows), sum(r["lost"] for r in rows)
    kinds = {kind: (rollup.kinds[kind]["total"] if rollup.kinds.get(kind) is not None else None) for kind in ACTIVITY_KINDS}
    return {
        "employee": None, "leads": leads, "won": won, "lost": lost, "open": leads - won - lost, "conversion_rate": rate(won, leads),
        **kinds, "activities": rollup.total,
    }
