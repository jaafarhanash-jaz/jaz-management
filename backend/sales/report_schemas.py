"""Response models of the Phase-5 dashboard and report endpoints (read-only: there are no request bodies).

Responses are explicit shapes; anything not declared here never leaves the server. A section the caller may not see is
`null` (or absent from a map), never an empty object that would pass for "zero". Every count is an integer, every
percentage one decimal or `null` (no data), every value a number - see sales/metrics.py for what each one means.
"""
from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class PeriodOut(BaseModel):
    range: str
    date_from: date
    date_to: date
    days: int


class PersonRef(BaseModel):
    id: str
    name: str


NamedRef = PersonRef        # {id, name} of anything named (a campaign)


class Header(BaseModel):
    """What every dashboard / report says about itself."""

    period: Optional[PeriodOut] = None      # null: the report is not tied to a period (pipeline, basis=all)
    scope: str                              # "team" | "personal"
    employee: Optional[PersonRef] = None    # the one employee a team caller narrowed to
    as_of: datetime                         # the moment the "now" numbers were read


# ---- shared rows --------------------------------------------------------------------------------------------------
class LeadFigures(BaseModel):
    leads: int
    won: int
    lost: int
    open: int
    conversion_rate: Optional[float] = None
    value: float
    won_value: float


class PipelineStageOut(BaseModel):
    stage: str
    leads: int
    value: float
    valued: int                             # how many of those leads carry an estimated value


class SourceRowOut(LeadFigures):
    source: str
    name_en: str
    name_ar: str


class CampaignRowOut(LeadFigures):
    campaign_id: str
    name: str
    status: str
    source: Optional[str] = None


class NoCampaignOut(BaseModel):
    leads: int
    won: int
    lost: int


class PerformanceRowOut(BaseModel):
    employee: Optional[PersonRef] = None    # null = the leads nobody owns
    leads: int
    won: int
    lost: int
    open: int
    conversion_rate: Optional[float] = None
    calls: Optional[int] = None             # null: the caller may not see that kind of work
    followups: Optional[int] = None
    demos: Optional[int] = None
    trials: Optional[int] = None
    activities: int


# ---- dashboard --------------------------------------------------------------------------------------------------------
class DashboardKpis(BaseModel):
    total_leads: Optional[int] = None
    new_leads: Optional[int] = None
    assigned_leads: Optional[int] = None
    contacted_leads: Optional[int] = None
    interested_leads: Optional[int] = None
    demos: Optional[int] = None
    negotiations: Optional[int] = None
    won: Optional[int] = None
    lost: Optional[int] = None
    conversion_rate: Optional[float] = None
    active_trials: Optional[int] = None
    upcoming_demos: Optional[int] = None
    overdue_followups: Optional[int] = None
    upcoming_followups: Optional[int] = None


class DashboardPerformance(BaseModel):
    items: List[PerformanceRowOut]
    total: int                              # employees with leads or activity in the period (items holds the busiest)


class DashboardCampaigns(BaseModel):
    items: List[CampaignRowOut]
    total: int
    no_campaign: NoCampaignOut


class ActivityTotals(BaseModel):
    calls: Optional[int] = None
    followups: Optional[int] = None
    demos: Optional[int] = None
    trials: Optional[int] = None
    total: int


class OnboardingSection(BaseModel):
    total: int                              # every onboarding record in the caller's scope
    active: int                             # ... not activated yet                        [now]
    activated_in_period: int                # ... activated during the period               [event]
    unassigned: Optional[int] = None        # only for a caller who sees every record
    by_stage: Dict[str, int]                # current stage of every record in scope       [now]


class DashboardOut(Header):
    bases: Dict[str, str]
    kpis: DashboardKpis
    pipeline: Optional[List[PipelineStageOut]] = None
    performance: Optional[DashboardPerformance] = None
    sources: Optional[List[SourceRowOut]] = None
    campaigns: Optional[DashboardCampaigns] = None
    activity: Optional[ActivityTotals] = None
    onboarding: Optional[OnboardingSection] = None


# ---- reports -----------------------------------------------------------------------------------------------------------
class LeadReportRow(BaseModel):
    id: str
    business_name: str
    city: Optional[str] = None
    pipeline_stage: str
    priority: str
    source: str
    campaign: Optional[NamedRef] = None
    assigned_to: Optional[PersonRef] = None
    estimated_value: Optional[float] = None
    created_at: datetime
    closed_at: Optional[datetime] = None


class LeadReportSummary(BaseModel):
    total: int                              # leads matching every filter EXCEPT the stage filter
    value: float
    by_stage: Dict[str, int]


class LeadReportOut(Header):
    summary: LeadReportSummary
    items: List[LeadReportRow]
    total: int                              # leads matching every filter (what the pages add up to)
    limit: int
    offset: int


class PipelineReportOut(Header):
    basis: str                              # "created" (leads created in the period) | "all" (every lead in scope)
    stages: List[PipelineStageOut]
    total_leads: int
    total_value: float


class EmployeePerformanceOut(Header):
    totals: PerformanceRowOut
    items: List[PerformanceRowOut]
    total: int
    limit: int
    offset: int


class SourceReportOut(Header):
    items: List[SourceRowOut]
    totals: LeadFigures


class CampaignReportOut(Header):
    items: List[CampaignRowOut]
    total: int
    limit: int
    offset: int
    no_campaign: Optional[NoCampaignOut] = None     # null while a campaign filter is on (it is what is being excluded)


class ConversionCohort(BaseModel):
    leads: int
    won: int
    lost: int
    open: int
    conversion_rate: Optional[float] = None


class ClosedInPeriod(BaseModel):
    won: int
    lost: int


class LostReasonRow(BaseModel):
    reason: str
    leads: int


class SeriesPoint(BaseModel):
    start: date                             # first day of the day / week
    leads: int
    won: int


class ConversionSeries(BaseModel):
    bucket: str                             # "day" | "week"
    points: List[SeriesPoint]


class ConversionReportOut(Header):
    cohort: ConversionCohort                # leads created in the period, by current stage        [cohort]
    closed_in_period: ClosedInPeriod        # leads closed in the period, whenever created          [event]
    customers: Optional[int] = None         # cohort leads that became a customer; null without sales.customers.view
    lost_reasons: List[LostReasonRow]
    series: ConversionSeries


class KindBreakdown(BaseModel):
    total: int                              # counted as activity (cancelled excluded)
    breakdown: Dict[str, int]               # by result (calls) / status (the others), cancelled included


class ActivityPersonRow(BaseModel):
    employee: PersonRef
    calls: Optional[int] = None
    followups: Optional[int] = None
    demos: Optional[int] = None
    trials: Optional[int] = None
    activities: int


class ActivityReportOut(Header):
    kinds: Dict[str, Optional[KindBreakdown]]
    activities: int
    items: List[ActivityPersonRow]
    total: int
    limit: int
    offset: int


class OnboardingSummary(BaseModel):
    total: int
    open: int
    activated: int
    unassigned: Optional[int] = None        # only for a caller who sees every record
    by_stage: Dict[str, int]


class OnboardingOwnerRow(BaseModel):
    employee: Optional[PersonRef] = None    # null = not owned yet
    total: int
    open: int
    activated: int


class OnboardingReportRow(BaseModel):
    id: str
    business_name: str
    stage: str
    assigned_to: Optional[PersonRef] = None
    started_at: datetime
    completed_at: Optional[datetime] = None


class OnboardingReportOut(Header):
    summary: OnboardingSummary              # records that STARTED in the period, by current stage [cohort]
    by_owner: List[OnboardingOwnerRow]       # the busiest onboarding employees (at most 50) ...
    by_owner_total: int                      # ... out of this many
    items: List[OnboardingReportRow]
    total: int
    limit: int
    offset: int
