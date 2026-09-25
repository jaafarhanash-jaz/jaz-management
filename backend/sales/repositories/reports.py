"""Dashboard / report aggregates (Phase 5). Every number is computed by the database: COUNT / SUM / GROUP BY over the
caller's scope - never by loading leads or activities into Python. See sales/metrics.py for what each metric means.

Conventions
  * Every function takes the caller's scope as SQL predicates (lead_access.lead_visibility / onboarding_access
    .onboarding_visibility) and ANDs them into the query, so an aggregate can never count a row its caller could not open.
  * One statement per question, whatever the amount of data: a dashboard costs a fixed handful of queries and a report
    one to four, never one per lead / employee / campaign (tests/test_sales_reports_db.py counts them).
  * Grouped queries are capped at MAX_GROUPS rows - a runaway guard, not a feature: the groups are staff accounts (x a handful
    of statuses), so the cap sits far above any real team, and a result can never silently lose rows to it.
  * Employee names come with the aggregate (a join) or from ONE `id IN (...)` lookup for the whole result (names_for).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.constants import FOLLOWUP_OPEN, ONBOARDING_DONE, ONBOARDING_STAGES, PIPELINE_STAGES, STAGE_LOST, STAGE_WON, TRIAL_OPEN
from sales.metrics import CANCELLED, Period
from sales.models import (
    SalesCall,
    SalesCampaign,
    SalesCustomer,
    SalesDemo,
    SalesFollowup,
    SalesLead,
    SalesLeadSource,
    SalesOnboarding,
    SalesTrial,
)
from sales.repositories.leads import (
    LeadFilters,
    _conditions as _lead_conditions,
    _is_staff_account_usable,
    _may_receive_leads,
)
from sales.repositories.onboarding import OnboardingFilters, _conditions as _onboarding_conditions
from sales.timezone import APP_UTC_OFFSET_HOURS

MAX_GROUPS = 5000


# ---- the lead population -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LeadScope:
    """WHICH leads an aggregate counts: the caller's visibility, not archived, created in `period` (None = any time),
    and the optional narrowing filters."""

    visibility: Any                                  # lead_access.lead_visibility(ctx)
    period: Optional[Period] = None
    assignee: Optional[uuid.UUID] = None             # only leads owned by this employee (a team caller's filter)
    source: Optional[str] = None
    campaign_id: Optional[uuid.UUID] = None
    priority: Optional[str] = None
    stages: Optional[Sequence[str]] = None

    def without_period(self) -> "LeadScope":
        return LeadScope(self.visibility, None, self.assignee, self.source, self.campaign_id, self.priority, self.stages)

    def without_stages(self) -> "LeadScope":
        return LeadScope(self.visibility, self.period, self.assignee, self.source, self.campaign_id, self.priority, None)

    def filters(self) -> LeadFilters:
        return LeadFilters(
            stages=self.stages, assigned_to=self.assignee, source=self.source, campaign_id=self.campaign_id,
            priority=self.priority, created_from=self.period.date_from if self.period else None,
            created_to=self.period.date_to if self.period else None, archived="exclude",
        )


def lead_conditions(scope: LeadScope) -> list:
    """The WHERE terms of the lead population. The same predicate the lead list uses, so a report can never count a
    lead the list would not show."""
    return _lead_conditions(scope.visibility, scope.filters())


def _lead_measures():
    won = SalesLead.pipeline_stage == STAGE_WON
    lost = SalesLead.pipeline_stage == STAGE_LOST
    return (
        func.count().label("leads"),
        func.count().filter(won).label("won"),
        func.count().filter(lost).label("lost"),
        func.coalesce(func.sum(SalesLead.estimated_value), 0).label("value"),
        func.coalesce(func.sum(SalesLead.estimated_value).filter(won), 0).label("won_value"),
    )


@dataclass(frozen=True)
class LeadMeasures:
    leads: int
    won: int
    lost: int
    value: float
    won_value: float

    @property
    def open(self) -> int:
        return self.leads - self.won - self.lost


def _measures(row) -> LeadMeasures:
    return LeadMeasures(int(row.leads), int(row.won), int(row.lost), float(row.value), float(row.won_value))


# ---- pipeline ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StageTotal:
    leads: int = 0
    value: float = 0.0
    valued: int = 0          # how many of the leads carry an estimated value


async def stage_totals(db: AsyncSession, scope: LeadScope) -> Dict[str, StageTotal]:
    """Leads (and their estimated value) per CURRENT pipeline stage; every stage is present, empty ones as zero."""
    result = await db.execute(
        select(
            SalesLead.pipeline_stage, func.count(), func.coalesce(func.sum(SalesLead.estimated_value), 0), func.count(SalesLead.estimated_value)
        )
        .where(*lead_conditions(scope))
        .group_by(SalesLead.pipeline_stage)
    )
    totals = {stage: StageTotal() for stage in PIPELINE_STAGES}
    for stage, leads, value, valued in result.all():
        totals[stage] = StageTotal(int(leads), float(value), int(valued))
    return totals


# ---- leads grouped by owner / source / campaign ---------------------------------------------------------------------------
async def leads_by_assignee(db: AsyncSession, scope: LeadScope) -> List[Tuple[Optional[uuid.UUID], Optional[str], LeadMeasures]]:
    """(employee id or None for unassigned, name, measures), the busiest first."""
    result = await db.execute(
        select(SalesLead.assigned_to, User.name, *_lead_measures())
        .select_from(SalesLead)
        .outerjoin(User, User.id == SalesLead.assigned_to)
        .where(*lead_conditions(scope))
        .group_by(SalesLead.assigned_to, User.name)
        .order_by(func.count().desc(), User.name, SalesLead.assigned_to)
        .limit(MAX_GROUPS)
    )
    return [(row.assigned_to, row.name, _measures(row)) for row in result.all()]


@dataclass(frozen=True)
class SourceRow:
    key: str
    name_en: str
    name_ar: str
    measures: LeadMeasures


async def leads_by_source(db: AsyncSession, scope: LeadScope) -> List[SourceRow]:
    result = await db.execute(
        select(SalesLead.source, SalesLeadSource.name_en, SalesLeadSource.name_ar, *_lead_measures())
        .select_from(SalesLead)
        .join(SalesLeadSource, SalesLeadSource.key == SalesLead.source)
        .where(*lead_conditions(scope))
        .group_by(SalesLead.source, SalesLeadSource.name_en, SalesLeadSource.name_ar, SalesLeadSource.sort_order)
        .order_by(func.count().desc(), SalesLeadSource.sort_order)
    )
    return [SourceRow(row.source, row.name_en, row.name_ar, _measures(row)) for row in result.all()]


@dataclass(frozen=True)
class CampaignRow:
    id: uuid.UUID
    name: str
    status: str
    source: Optional[str]
    measures: LeadMeasures


async def leads_by_campaign(
    db: AsyncSession, scope: LeadScope, *, campaign_status: Optional[str] = None, limit: int, offset: int
) -> Tuple[List[CampaignRow], int]:
    """The campaigns that received a lead in the scope, the busiest first (a campaign with none is not listed), one page
    of them, and how many campaigns there are in all."""
    grouped = (
        select(SalesLead.campaign_id.label("cid"), SalesCampaign.name, SalesCampaign.status, SalesCampaign.source, *_lead_measures())
        .select_from(SalesLead)
        .join(SalesCampaign, SalesCampaign.id == SalesLead.campaign_id)
        .where(*lead_conditions(scope))
        .group_by(SalesLead.campaign_id, SalesCampaign.name, SalesCampaign.status, SalesCampaign.source)
    )
    if campaign_status:
        grouped = grouped.where(SalesCampaign.status == campaign_status)
    total = (await db.execute(select(func.count()).select_from(grouped.order_by(None).subquery()))).scalar_one()
    result = await db.execute(grouped.order_by(func.count().desc(), SalesCampaign.name, SalesLead.campaign_id).limit(limit).offset(offset))
    return [CampaignRow(row.cid, row.name, row.status, row.source, _measures(row)) for row in result.all()], int(total)


async def leads_without_campaign(db: AsyncSession, scope: LeadScope) -> LeadMeasures:
    result = await db.execute(select(*_lead_measures()).where(*lead_conditions(scope), SalesLead.campaign_id.is_(None)))
    return _measures(result.one())


# ---- conversion ----------------------------------------------------------------------------------------------------
async def closed_in_period(db: AsyncSession, scope: LeadScope, period: Period) -> Tuple[int, int]:
    """(won, lost) among the leads that were CLOSED during the period, whenever they were created (the scope's own period
    is not used: this is the event view of the same two stages)."""
    result = await db.execute(
        select(
            func.count().filter(SalesLead.pipeline_stage == STAGE_WON),
            func.count().filter(SalesLead.pipeline_stage == STAGE_LOST),
        ).where(*lead_conditions(scope.without_period()), SalesLead.closed_at >= period.start, SalesLead.closed_at < period.end)
    )
    won, lost = result.one()
    return int(won), int(lost)


async def converted_customers(db: AsyncSession, scope: LeadScope) -> int:
    """Leads of the population that have become a customer."""
    return int(
        (
            await db.execute(
                select(func.count(SalesCustomer.id))
                .select_from(SalesCustomer)
                .join(SalesLead, SalesLead.id == SalesCustomer.lead_id)
                .where(*lead_conditions(scope))
            )
        ).scalar_one()
    )


async def lost_reasons(db: AsyncSession, scope: LeadScope) -> List[Tuple[str, int]]:
    result = await db.execute(
        select(SalesLead.lost_reason, func.count())
        .where(*lead_conditions(scope.without_stages()), SalesLead.pipeline_stage == STAGE_LOST)
        .group_by(SalesLead.lost_reason)
        .order_by(func.count().desc(), SalesLead.lost_reason)
    )
    return [(reason, int(n)) for reason, n in result.all()]


async def conversion_series(db: AsyncSession, scope: LeadScope, bucket: str) -> List[Tuple[datetime, int, int]]:
    """Leads created (and how many of them are now won) per day / week of the period, the days being days of the application
    timezone (a lead created at 22:30 UTC is on the NEXT day in Baghdad). `bucket` is 'day' or 'week' - a literal in the SQL,
    never a bound value, so the SELECT and GROUP BY expressions are identical to the server. The offset is a literal too (an
    integer constant from sales/timezone.py)."""
    if bucket not in ("day", "week"):
        raise ValueError(bucket)
    local = func.timezone(literal_column("'UTC'"), SalesLead.created_at) + literal_column(f"interval '{APP_UTC_OFFSET_HOURS} hours'")
    start = func.date_trunc(literal_column(f"'{bucket}'"), local).label("bucket")
    result = await db.execute(
        select(start, func.count(), func.count().filter(SalesLead.pipeline_stage == STAGE_WON))
        .where(*lead_conditions(scope))
        .group_by(start)
        .order_by(start)
    )
    return [(row[0], int(row[1]), int(row[2])) for row in result.all()]


# ---- activities (calls, follow-ups, demos, trials) ---------------------------------------------------------------------
# kind -> (model, the column naming who did / owns it, its status column, the column that puts it in a period)
_ACTIVITY = {
    "calls": (SalesCall, SalesCall.employee_id, SalesCall.result, SalesCall.called_at),
    "followups": (SalesFollowup, SalesFollowup.assigned_to, SalesFollowup.status, SalesFollowup.due_at),
    "demos": (SalesDemo, SalesDemo.assigned_to, SalesDemo.status, SalesDemo.scheduled_at),
    "trials": (SalesTrial, SalesTrial.created_by, SalesTrial.status, SalesTrial.started_at),
}


async def activity_by_person(
    db: AsyncSession, kind: str, *, visibility, period: Period, actor: Optional[uuid.UUID]
) -> List[Tuple[uuid.UUID, str, int]]:
    """(person, status / result, count) of one kind of work whose date is in the period, on non-archived leads the caller
    may see, optionally only the work of `actor`. Every status is returned - the caller decides what counts as activity."""
    model, person, status, when = _ACTIVITY[kind]
    stmt = (
        select(person, status, func.count())
        .select_from(model)
        .join(SalesLead, SalesLead.id == model.lead_id)
        .where(visibility, SalesLead.archived_at.is_(None), when >= period.start, when < period.end)
        .group_by(person, status)
        .limit(MAX_GROUPS)
    )
    if actor is not None:
        stmt = stmt.where(person == actor)
    return [(row[0], row[1], int(row[2])) for row in (await db.execute(stmt)).all()]


def counts_as_activity(kind: str, status: str) -> bool:
    """Cancelled follow-ups / demos / trials are not work that was done (calls have no cancelled state)."""
    return not (kind != "calls" and status == CANCELLED)


# ---- "now" readings --------------------------------------------------------------------------------------------------
async def followups_now(db: AsyncSession, *, visibility, actor: Optional[uuid.UUID]) -> Tuple[int, int]:
    """(overdue, upcoming) pending follow-ups: due before / at-or-after this moment."""
    stmt = (
        select(func.count().filter(SalesFollowup.due_at < func.now()), func.count().filter(SalesFollowup.due_at >= func.now()))
        .select_from(SalesFollowup)
        .join(SalesLead, SalesLead.id == SalesFollowup.lead_id)
        .where(SalesFollowup.status == FOLLOWUP_OPEN, visibility, SalesLead.archived_at.is_(None))
    )
    if actor is not None:
        stmt = stmt.where(SalesFollowup.assigned_to == actor)
    overdue, upcoming = (await db.execute(stmt)).one()
    return int(overdue), int(upcoming)


async def upcoming_demos(db: AsyncSession, *, visibility, actor: Optional[uuid.UUID]) -> int:
    stmt = (
        select(func.count())
        .select_from(SalesDemo)
        .join(SalesLead, SalesLead.id == SalesDemo.lead_id)
        .where(SalesDemo.status == "scheduled", SalesDemo.scheduled_at >= func.now(), visibility, SalesLead.archived_at.is_(None))
    )
    if actor is not None:
        stmt = stmt.where(SalesDemo.assigned_to == actor)
    return int((await db.execute(stmt)).scalar_one())


async def active_trials(db: AsyncSession, *, visibility, lead_owner: Optional[uuid.UUID]) -> int:
    """Active Sales-side trials on the leads the caller may see (optionally only the leads one employee owns): a trial
    belongs to its lead, so it follows the lead's owner, not whoever happened to start it."""
    stmt = (
        select(func.count())
        .select_from(SalesTrial)
        .join(SalesLead, SalesLead.id == SalesTrial.lead_id)
        .where(SalesTrial.status == TRIAL_OPEN, visibility, SalesLead.archived_at.is_(None))
    )
    if lead_owner is not None:
        stmt = stmt.where(SalesLead.assigned_to == lead_owner)
    return int((await db.execute(stmt)).scalar_one())


# ---- onboarding ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class OnboardingStageRow:
    total: int
    activated_in_period: int
    unassigned: int


async def onboarding_by_stage(db: AsyncSession, *, visibility, period: Optional[Period]) -> Dict[str, OnboardingStageRow]:
    """Every onboarding record in the caller's onboarding scope by its CURRENT stage (all eight stages present), with how
    many were activated during the period and how many nobody owns yet."""
    if period is not None:
        activated = func.count().filter(and_(SalesOnboarding.completed_at >= period.start, SalesOnboarding.completed_at < period.end))
    else:
        activated = func.count().filter(SalesOnboarding.completed_at.is_not(None))
    result = await db.execute(
        select(SalesOnboarding.stage, func.count(), activated, func.count().filter(SalesOnboarding.assigned_to.is_(None)))
        .where(visibility)
        .group_by(SalesOnboarding.stage)
    )
    rows = {stage: OnboardingStageRow(0, 0, 0) for stage in ONBOARDING_STAGES}
    for stage, total, done, unassigned in result.all():
        rows[stage] = OnboardingStageRow(int(total), int(done), int(unassigned))
    return rows


async def onboarding_cohort_by_stage(db: AsyncSession, *, visibility, filters: OnboardingFilters) -> Dict[str, int]:
    """The onboarding records that STARTED in the period (and match the filters), by current stage."""
    result = await db.execute(
        select(SalesOnboarding.stage, func.count()).where(*_onboarding_conditions(visibility, filters)).group_by(SalesOnboarding.stage)
    )
    counts = {stage: 0 for stage in ONBOARDING_STAGES}
    counts.update({stage: int(n) for stage, n in result.all()})
    return counts


ONBOARDING_OWNER_ROWS = 50      # the "by onboarding employee" table shows the busiest this many; the total says how many there are


async def onboarding_cohort_by_owner(
    db: AsyncSession, *, visibility, filters: OnboardingFilters, limit: int = ONBOARDING_OWNER_ROWS
) -> Tuple[List[Tuple[Optional[uuid.UUID], Optional[str], int, int, int]], int]:
    """The busiest `limit` onboarding employees of the same population - (employee or None for unowned, name, records, still
    open, activated) - and how many there are in all (one more small query; it is what lets the UI say "50 of 340")."""
    grouped = (
        select(
            SalesOnboarding.assigned_to, User.name, func.count().label("n"),
            func.count().filter(SalesOnboarding.stage != ONBOARDING_DONE), func.count().filter(SalesOnboarding.stage == ONBOARDING_DONE),
        )
        .select_from(SalesOnboarding)
        .outerjoin(User, User.id == SalesOnboarding.assigned_to)
        .where(*_onboarding_conditions(visibility, filters))
        .group_by(SalesOnboarding.assigned_to, User.name)
    )
    total = (await db.execute(select(func.count()).select_from(grouped.subquery()))).scalar_one()
    result = await db.execute(grouped.order_by(func.count().desc(), User.name, SalesOnboarding.assigned_to).limit(limit))
    return [(row[0], row[1], int(row[2]), int(row[3]), int(row[4])) for row in result.all()], int(total)


# ---- idle employees --------------------------------------------------------------------------------------------------
async def assignable_staff(db: AsyncSession) -> List[Tuple[uuid.UUID, str]]:
    """(id, name) of every ACTIVE staff member who may receive leads - the people a Sales Manager manages - so a performance
    table can show the ones with nothing to report instead of silently leaving them out. It is the same set, by the same
    predicates, as the assignment picker lists (leads.list_assignees); a caller only gets it when they may use that picker
    (sales.leads.assign). Two columns only: nothing else of a user account is read."""
    result = await db.execute(
        select(User.id, User.name).where(_is_staff_account_usable(), _may_receive_leads(User.id)).order_by(User.name, User.id).limit(MAX_GROUPS)
    )
    return [(uid, name or "") for uid, name in result.all()]


# ---- names ---------------------------------------------------------------------------------------------------------------
async def names_for(db: AsyncSession, ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, str]:
    """{user id: name} for a whole result at once (one query, however many people)."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    result = await db.execute(select(User.id, User.name).where(User.id.in_(wanted)))
    return {uid: name or "" for uid, name in result.all()}
