"""Query-string parameters of the Phase-5 dashboard and report endpoints.

One class per shape, each turning raw query values into what the services take and a bad value into a 400 in the API's
usual {"field", "message", "code"} shape. `to_period()` is the only place a period is resolved (sales/metrics.py has the
rules). The `employee_id` filter arrives here as plain text: it is parsed AND authorized in one step by
services/report_scope.build_scope (a caller outside the team may only name themself), so no route can read it unchecked.
"""
import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import Query

from sales.constants import ONBOARDING_STAGES, PIPELINE_STAGES
from sales.metrics import Period, PeriodError, resolve_period
from sales.services.common import field_error
from sales.timezone import app_today

# Same key shape as the lead-source filter of the lead list: anything else cannot match a source, and a NUL character in
# particular must never reach the database driver.
_SOURCE_KEY_PATTERN = r"^[a-z0-9_]+$"

_RANGE = Query(None, max_length=20, description="today | this_week | this_month (default) | last_month | custom")
_FROM = Query(None, description="Custom range start (date in Baghdad time, inclusive) - needs date_to")
_TO = Query(None, description="Custom range end (date in Baghdad time, inclusive) - needs date_from")
_EMPLOYEE = Query(None, max_length=40, description="Only one employee's numbers: an id or 'me'. A caller outside the team may only ask about themself.")


def _period(range_: Optional[str], date_from: Optional[date], date_to: Optional[date]) -> Period:
    try:
        return resolve_period(range_, date_from, date_to, app_today())
    except PeriodError as exc:
        raise field_error(exc.field, exc.message, 400, code=exc.code)


class PeriodParams:
    """The period and nothing else (the dashboard's own filter besides the employee)."""

    def __init__(self, range: Optional[str] = _RANGE, date_from: Optional[date] = _FROM, date_to: Optional[date] = _TO):
        self.range, self.date_from, self.date_to = range, date_from, date_to

    def to_period(self) -> Period:
        return _period(self.range, self.date_from, self.date_to)


class DashboardParams(PeriodParams):
    def __init__(self, range: Optional[str] = _RANGE, date_from: Optional[date] = _FROM, date_to: Optional[date] = _TO, employee_id: Optional[str] = _EMPLOYEE):
        super().__init__(range, date_from, date_to)
        self.employee_id = employee_id


class LeadReportParams(DashboardParams):
    """The filters of the lead-based reports (leads, pipeline, sources, conversion, employee performance)."""

    def __init__(
        self,
        range: Optional[str] = _RANGE,
        date_from: Optional[date] = _FROM,
        date_to: Optional[date] = _TO,
        employee_id: Optional[str] = _EMPLOYEE,
        source: Optional[str] = Query(None, max_length=50, pattern=_SOURCE_KEY_PATTERN, description="A lead source key"),
        campaign_id: Optional[uuid.UUID] = Query(None, description="Only the leads of this campaign"),
    ):
        super().__init__(range, date_from, date_to, employee_id)
        self.source, self.campaign_id = source, campaign_id


class LeadListParams(LeadReportParams):
    """The lead report: the shared filters plus the ones only a list of leads needs."""

    def __init__(
        self,
        range: Optional[str] = _RANGE,
        date_from: Optional[date] = _FROM,
        date_to: Optional[date] = _TO,
        employee_id: Optional[str] = _EMPLOYEE,
        source: Optional[str] = Query(None, max_length=50, pattern=_SOURCE_KEY_PATTERN, description="A lead source key"),
        campaign_id: Optional[uuid.UUID] = Query(None, description="Only the leads of this campaign"),
        pipeline_stage: Optional[str] = Query(None, max_length=200, description="One stage, or several separated by commas"),
        priority: Optional[Literal["low", "medium", "high"]] = Query(None),
    ):
        super().__init__(range, date_from, date_to, employee_id, source, campaign_id)
        self.pipeline_stage, self.priority = pipeline_stage, priority

    def stages(self) -> Optional[list]:
        if not self.pipeline_stage:
            return None
        stages = [part.strip() for part in self.pipeline_stage.split(",") if part.strip()]
        unknown = [stage for stage in stages if stage not in PIPELINE_STAGES]
        if unknown:
            raise field_error("pipeline_stage", f"Unknown pipeline stage: {', '.join(unknown)}")
        return stages or None


class OnboardingReportParams(PeriodParams):
    def __init__(
        self,
        range: Optional[str] = _RANGE,
        date_from: Optional[date] = _FROM,
        date_to: Optional[date] = _TO,
        stage: Optional[str] = Query(None, max_length=200, description="One onboarding stage, or several separated by commas"),
        assigned_to: Optional[str] = Query(None, max_length=40, description="The onboarding employee: an id or 'me'"),
    ):
        super().__init__(range, date_from, date_to)
        self.stage, self.assigned_to = stage, assigned_to

    def stages(self) -> Optional[list]:
        if not self.stage:
            return None
        stages = [part.strip() for part in self.stage.split(",") if part.strip()]
        unknown = [stage for stage in stages if stage not in ONBOARDING_STAGES]
        if unknown:
            raise field_error("stage", f"Unknown onboarding stage: {', '.join(unknown)}")
        return stages or None
