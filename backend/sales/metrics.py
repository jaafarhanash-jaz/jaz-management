"""JAZ Sales (Phase 5) - what the dashboard and the reports count, and the period they count it in.

Pure Python (no database / framework imports), so the rules can be unit-tested on their own. This module is THE place
where a metric is defined; the SQL in repositories/reports.py implements these definitions and the UI only labels them.

THE PERIOD
  A period is a range of calendar dates in the application timezone (Baghdad, UTC+3 - see sales/timezone.py), both ends
  INCLUSIVE (the convention of every Sales filter since Phase 2).
  Presets are whole calendar units: `today`; `this_week` = Monday..Sunday of the current week; `this_month` = the 1st..the
  last day of the current month; `last_month` = the whole previous month. `custom` needs both dates. The default is
  `this_month`. A period is at most MAX_PERIOD_DAYS long, which keeps every aggregate bounded and practical. A day starts at
  midnight in that zone for EVERY user and every Sales date filter (stored timestamps stay UTC), so a number on the dashboard
  and the list behind it always agree.

THE THREE BASES - every number states which one it is (`bases` in the API responses)
  cohort  The things that ENTERED the period (a lead by its creation date, an onboarding record by the day the customer was
          converted), counted by their CURRENT stored stage. Because they are one population, the stage counts always add up
          to the total, and a rate over them can never exceed 100 %.
  event   Things that HAPPENED in the period, each kind by its own date: a call by when it was made, a follow-up by when it is
          due, a demo by when it is scheduled, a trial by when it started, an onboarding activation by when it completed.
  now     The state at the moment of the request, whatever the period: overdue / upcoming follow-ups, upcoming demos, active
          trials, active onboarding. (A period says nothing about what is overdue today.)

METRICS
  total_leads / new_leads / assigned_leads / contacted_leads / interested_leads / negotiations / won / lost   [cohort]
      leads created in the period, counted by their current pipeline stage (`new`, `assigned`, ... as stored). Archived
      leads are never counted (they are hidden everywhere else too).
  demos                        [cohort]  leads created in the period that are now in a demo stage (demo_scheduled or
                                         demo_completed).
  conversion_rate              [cohort]  won / total_leads * 100 over that same population, rounded to one decimal;
                                         null (never 0 %) when no lead entered the period. Leads that are still open count
                                         in the denominator - it is a rate over the leads that entered, not over closed ones.
  overdue_followups            [now]     pending follow-ups whose due time has passed.
  upcoming_followups           [now]     pending follow-ups due now or later.
  upcoming_demos               [now]     scheduled demos whose time is now or later.
  active_trials                [now]     Sales-side trials whose status is active.
  activities                   [event]   calls + follow-ups + demos + trials whose date is in the period, each row once and
                                         CANCELLED follow-ups / demos / trials excluded (cancelled work is shown separately in
                                         the breakdowns, never as activity). Attributed to whoever did / owns the work: a call
                                         to the employee who made it, a follow-up or demo to its assignee, a trial to whoever
                                         started it.
  onboarding active            [now]     onboarding records that are not activated yet.
  onboarding by stage          [now]     every onboarding record in the caller's scope by its current stage.
  onboarding activated         [event]   records whose activation (completed_at) falls in the period.
  Pipeline value               sum of the leads' ESTIMATED value - an estimate typed in by staff, with no currency, ignored
                               where it is empty. Every value is reported together with how many leads carry one.

WHO SEES WHAT
  Aggregates obey the same scope as the records they count (services/report_scope.py): lead numbers follow the lead scope,
  work-item numbers follow the lead scope AND the kind's view permission, onboarding numbers follow the onboarding scope. A
  caller who is not in the `team` (sales.leads.scope_all) gets only their own numbers: their scope for leads, and the work
  they did or own for activities.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple

from sales.constants import (
    STAGE_ASSIGNED,
    STAGE_CONTACTED,
    STAGE_DEMO_COMPLETED,
    STAGE_DEMO_SCHEDULED,
    STAGE_INTERESTED,
    STAGE_LOST,
    STAGE_NEGOTIATION,
    STAGE_NEW,
    STAGE_WON,
)
from sales.timezone import day_start

# ---- the period ------------------------------------------------------------------------------------------------
RANGE_TODAY = "today"
RANGE_THIS_WEEK = "this_week"
RANGE_THIS_MONTH = "this_month"
RANGE_LAST_MONTH = "last_month"
RANGE_CUSTOM = "custom"
RANGES: Tuple[str, ...] = (RANGE_TODAY, RANGE_THIS_WEEK, RANGE_THIS_MONTH, RANGE_LAST_MONTH, RANGE_CUSTOM)
DEFAULT_RANGE = RANGE_THIS_MONTH

MAX_PERIOD_DAYS = 366
WEEK_STARTS_ON = 0                      # Monday (date.weekday(): Monday = 0)
MIN_PERIOD_DATE = date(2000, 1, 1)      # the same sanity window as every client-supplied timestamp (constants.MIN_TIMESTAMP)
MAX_PERIOD_DATE = date(2099, 12, 31)

# The daily series of the conversion report switches to weekly buckets beyond this many days (so a chart never has more
# than ~55 points).
DAILY_SERIES_MAX_DAYS = 45


class PeriodError(ValueError):
    """A period that cannot be used. `field` names the offending query parameter, `code` is a stable identifier."""

    def __init__(self, field: str, code: str, message: str):
        super().__init__(message)
        self.field, self.code, self.message = field, code, message


@dataclass(frozen=True)
class Period:
    range: str
    date_from: date                     # inclusive
    date_to: date                       # inclusive

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    @property
    def start(self) -> datetime:
        """The first instant of the period (inclusive): midnight of the first day, in the application timezone."""
        return day_start(self.date_from)

    @property
    def end(self) -> datetime:
        """The first instant AFTER the period (exclusive): midnight after its last day, in the application timezone."""
        return day_start(self.date_to + timedelta(days=1))


def _preset(name: str, today: date) -> Tuple[date, date]:
    if name == RANGE_TODAY:
        return today, today
    if name == RANGE_THIS_WEEK:
        first = today - timedelta(days=(today.weekday() - WEEK_STARTS_ON) % 7)
        return first, first + timedelta(days=6)
    first_of_month = today.replace(day=1)
    if name == RANGE_THIS_MONTH:
        return first_of_month, _last_day_of_month(first_of_month)
    # last_month
    last_of_previous = first_of_month - timedelta(days=1)
    return last_of_previous.replace(day=1), last_of_previous


def _last_day_of_month(first: date) -> date:
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def resolve_period(range_: Optional[str], date_from: Optional[date], date_to: Optional[date], today: date) -> Period:
    """The concrete period a request asks for (see the module docstring), or a PeriodError.

    No range and no dates -> the default; dates without a range -> custom; a preset together with dates is a conflict
    rather than a silent choice between the two."""
    if range_ is not None and range_ not in RANGES:
        raise PeriodError("range", "invalid_range", f"Unknown range: {range_}")
    if range_ is None:
        range_ = RANGE_CUSTOM if (date_from is not None or date_to is not None) else DEFAULT_RANGE
    if range_ != RANGE_CUSTOM:
        if date_from is not None or date_to is not None:
            raise PeriodError("date_from", "period_conflict", "Give either a range or a custom date range, not both")
        lo, hi = _preset(range_, today)
        return Period(range_, lo, hi)

    if date_from is None or date_to is None:
        raise PeriodError("date_from" if date_from is None else "date_to", "period_incomplete", "A custom range needs both a start and an end date")
    if date_from > date_to:
        raise PeriodError("date_to", "period_reversed", "The end date cannot be before the start date")
    if date_from < MIN_PERIOD_DATE or date_to > MAX_PERIOD_DATE:
        raise PeriodError("date_from", "period_out_of_bounds", "The dates are outside the supported window")
    if (date_to - date_from).days + 1 > MAX_PERIOD_DAYS:
        raise PeriodError("date_to", "period_too_long", f"A period can span at most {MAX_PERIOD_DAYS} days")
    return Period(RANGE_CUSTOM, date_from, date_to)


def series_bucket(period: Period) -> str:
    """'day' for a short period, 'week' (Monday-based, like PostgreSQL's date_trunc) for a longer one."""
    return "day" if period.days <= DAILY_SERIES_MAX_DAYS else "week"


# ---- rates -----------------------------------------------------------------------------------------------------
def rate(part: int, whole: int) -> Optional[float]:
    """part / whole as a percentage with one decimal, or None when there is no whole to speak of (a rate over nothing is
    "no data", not 0 %)."""
    if not whole:
        return None
    return round(part * 100.0 / whole, 1)


# ---- stage groups ----------------------------------------------------------------------------------------------
DEMO_STAGES: Tuple[str, ...] = (STAGE_DEMO_SCHEDULED, STAGE_DEMO_COMPLETED)

# The KPI -> the stage(s) whose leads it counts (the cohort KPIs of the dashboard).
KPI_STAGES: Dict[str, Tuple[str, ...]] = {
    "new_leads": (STAGE_NEW,),
    "assigned_leads": (STAGE_ASSIGNED,),
    "contacted_leads": (STAGE_CONTACTED,),
    "interested_leads": (STAGE_INTERESTED,),
    "demos": DEMO_STAGES,
    "negotiations": (STAGE_NEGOTIATION,),
    "won": (STAGE_WON,),
    "lost": (STAGE_LOST,),
}

# ---- activities ------------------------------------------------------------------------------------------------
ACTIVITY_KINDS: Tuple[str, ...] = ("calls", "followups", "demos", "trials")
CANCELLED = "cancelled"     # the one status that is never counted as activity (calls have no cancelled state)

# ---- bases -----------------------------------------------------------------------------------------------------
BASIS_COHORT, BASIS_EVENT, BASIS_NOW = "cohort", "event", "now"

# Every metric the dashboard / reports return and the basis it is on. Returned as `bases` so the UI can label it, and
# checked by the tests: a metric without a basis has no definition.
METRIC_BASES: Dict[str, str] = {
    "total_leads": BASIS_COHORT, "new_leads": BASIS_COHORT, "assigned_leads": BASIS_COHORT, "contacted_leads": BASIS_COHORT,
    "interested_leads": BASIS_COHORT, "demos": BASIS_COHORT, "negotiations": BASIS_COHORT, "won": BASIS_COHORT,
    "lost": BASIS_COHORT, "conversion_rate": BASIS_COHORT,
    "active_trials": BASIS_NOW, "upcoming_demos": BASIS_NOW, "overdue_followups": BASIS_NOW, "upcoming_followups": BASIS_NOW,
    "pipeline": BASIS_COHORT, "performance": BASIS_COHORT, "sources": BASIS_COHORT, "campaigns": BASIS_COHORT,
    "activity": BASIS_EVENT,
    "onboarding_active": BASIS_NOW, "onboarding_by_stage": BASIS_NOW, "onboarding_activated": BASIS_EVENT,
}
