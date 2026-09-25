"""Query-string filters of the Phase-3 list endpoints (calls, follow-ups, demos, trials).

One class per kind, shared by the kind's list endpoint and its counts endpoint (the same filters mean the same
thing in both). Each `to_filters(ctx)` turns the raw query values into the repository's filter object and turns a bad
value into a 400 in the API's usual {"field", "message"} shape. `assigned_to` / `employee_id` accept an id or `me`
(the caller).
"""
import uuid
from datetime import date
from typing import Iterable, List, Literal, Optional

from fastapi import Query

from sales.constants import (
    CALL_RESULTS,
    DEFAULT_ENDING_SOON_DAYS,
    DEMO_STATUSES,
    FOLLOWUP_STATUSES,
    MAX_ENDING_SOON_DAYS,
    TRIAL_STATUSES,
)
from sales.repositories.calls import CallFilters
from sales.repositories.demos import DemoFilters
from sales.repositories.followups import FollowupFilters
from sales.repositories.trials import TrialFilters
from sales.services.access import StaffContext
from sales.services.common import field_error


def _choices(value: Optional[str], allowed: Iterable[str], field: str) -> Optional[List[str]]:
    """'a,b' -> ['a', 'b'], every part checked against the closed set; nothing given -> None (no filter)."""
    if not value:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [part for part in parts if part not in allowed]
    if unknown:
        raise field_error(field, f"Unknown {field}: {', '.join(unknown)}")
    return parts or None


def _person(value: Optional[str], ctx: StaffContext, field: str) -> Optional[uuid.UUID]:
    """An employee id, or 'me' for the caller."""
    if not value:
        return None
    if value == "me":
        return ctx.user_id
    try:
        return uuid.UUID(value)
    except ValueError:
        raise field_error(field, "Invalid employee id")


_Q = Query(None, max_length=200, description="Search the lead's business or contact name")


class CallFilterParams:
    def __init__(
        self,
        lead_id: Optional[uuid.UUID] = Query(None, description="Only the calls of this lead"),
        employee_id: Optional[str] = Query(None, max_length=40, description="The employee who made the call: an id or 'me'"),
        result: Optional[str] = Query(None, max_length=200, description="One call result, or several separated by commas"),
        called_from: Optional[date] = Query(None, description="Called on or after (date, Baghdad time)"),
        called_to: Optional[date] = Query(None, description="Called on or before (date, Baghdad time)"),
        q: Optional[str] = _Q,
    ):
        self.lead_id, self.employee_id, self.result = lead_id, employee_id, result
        self.called_from, self.called_to, self.q = called_from, called_to, q

    def to_filters(self, ctx: StaffContext) -> CallFilters:
        return CallFilters(
            lead_id=self.lead_id, employee_id=_person(self.employee_id, ctx, "employee_id"),
            results=_choices(self.result, CALL_RESULTS, "result"), called_from=self.called_from, called_to=self.called_to, q=self.q,
        )


class FollowupFilterParams:
    def __init__(
        self,
        lead_id: Optional[uuid.UUID] = Query(None, description="Only the follow-ups of this lead"),
        assigned_to: Optional[str] = Query(None, max_length=40, description="The employee it is for: an id or 'me'"),
        status: Optional[str] = Query(None, max_length=100, description="One status, or several separated by commas"),
        due: Optional[Literal["overdue", "upcoming"]] = Query(None, description="Pending follow-ups only: overdue (due before now) or upcoming (due now or later)"),
        due_from: Optional[date] = Query(None, description="Due on or after (date, Baghdad time)"),
        due_to: Optional[date] = Query(None, description="Due on or before (date, Baghdad time)"),
        q: Optional[str] = _Q,
    ):
        self.lead_id, self.assigned_to, self.status, self.due = lead_id, assigned_to, status, due
        self.due_from, self.due_to, self.q = due_from, due_to, q

    def to_filters(self, ctx: StaffContext) -> FollowupFilters:
        return FollowupFilters(
            lead_id=self.lead_id, assigned_to=_person(self.assigned_to, ctx, "assigned_to"),
            statuses=_choices(self.status, FOLLOWUP_STATUSES, "status"), due=self.due,
            due_from=self.due_from, due_to=self.due_to, q=self.q,
        )


class DemoFilterParams:
    def __init__(
        self,
        lead_id: Optional[uuid.UUID] = Query(None, description="Only the demos of this lead"),
        assigned_to: Optional[str] = Query(None, max_length=40, description="The employee who gives it: an id or 'me'"),
        status: Optional[str] = Query(None, max_length=100, description="One status, or several separated by commas"),
        scheduled_from: Optional[date] = Query(None, description="Scheduled on or after (date, Baghdad time)"),
        scheduled_to: Optional[date] = Query(None, description="Scheduled on or before (date, Baghdad time)"),
        q: Optional[str] = _Q,
    ):
        self.lead_id, self.assigned_to, self.status = lead_id, assigned_to, status
        self.scheduled_from, self.scheduled_to, self.q = scheduled_from, scheduled_to, q

    def to_filters(self, ctx: StaffContext) -> DemoFilters:
        return DemoFilters(
            lead_id=self.lead_id, assigned_to=_person(self.assigned_to, ctx, "assigned_to"),
            statuses=_choices(self.status, DEMO_STATUSES, "status"),
            scheduled_from=self.scheduled_from, scheduled_to=self.scheduled_to, q=self.q,
        )


class TrialFilterParams:
    def __init__(
        self,
        lead_id: Optional[uuid.UUID] = Query(None, description="Only the trials of this lead"),
        status: Optional[str] = Query(None, max_length=100, description="One status, or several separated by commas"),
        ending_within_days: Optional[int] = Query(
            None, ge=1, le=MAX_ENDING_SOON_DAYS,
            description="Active trials whose expected end is at most this many days away - including ones already past it",
        ),
        ending_from: Optional[date] = Query(None, description="Expected to end on or after (date, Baghdad time)"),
        ending_to: Optional[date] = Query(None, description="Expected to end on or before (date, Baghdad time)"),
        q: Optional[str] = _Q,
    ):
        self.lead_id, self.status, self.ending_within_days = lead_id, status, ending_within_days
        self.ending_from, self.ending_to, self.q = ending_from, ending_to, q

    def to_filters(self, ctx: StaffContext) -> TrialFilters:
        return TrialFilters(
            lead_id=self.lead_id, statuses=_choices(self.status, TRIAL_STATUSES, "status"),
            ending_within_days=self.ending_within_days, ending_from=self.ending_from, ending_to=self.ending_to, q=self.q,
        )


ENDING_SOON_QUERY = Query(
    DEFAULT_ENDING_SOON_DAYS, ge=1, le=MAX_ENDING_SOON_DAYS,
    description="The window of the `ending_soon` count: active trials expected to end within this many days",
)
