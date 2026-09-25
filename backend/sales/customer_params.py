"""Query-string filters of the Phase-4 list endpoints (customers, onboarding).

One class per list, shared by the list endpoint and its counts endpoint (the same filters mean the same thing in
both). Each `to_filters(ctx)` turns the raw query values into the repository's filter object and turns a bad value
into a 400 in the API's usual {"field", "message"} shape. `assigned_to` accepts an id or `me` (the caller).
"""
from datetime import date
from typing import Optional

from fastapi import Query

from sales.activity_params import _choices, _person
from sales.constants import CUSTOMER_STATUSES, ONBOARDING_STAGES
from sales.repositories.customers import CustomerFilters
from sales.repositories.onboarding import OnboardingFilters
from sales.services.access import StaffContext

_Q = Query(None, max_length=200, description="Search the business or contact name")


class CustomerFilterParams:
    def __init__(
        self,
        status: Optional[str] = Query(None, max_length=100, description="One customer status, or several separated by commas"),
        converted_from: Optional[date] = Query(None, description="Converted on or after (date, Baghdad time)"),
        converted_to: Optional[date] = Query(None, description="Converted on or before (date, Baghdad time)"),
        q: Optional[str] = Query(None, max_length=200, description="Search the business, contact or company name"),
    ):
        self.status, self.converted_from, self.converted_to, self.q = status, converted_from, converted_to, q

    def to_filters(self, ctx: StaffContext) -> CustomerFilters:
        return CustomerFilters(
            statuses=_choices(self.status, CUSTOMER_STATUSES, "status"),
            converted_from=self.converted_from, converted_to=self.converted_to, q=self.q,
        )


class OnboardingFilterParams:
    def __init__(
        self,
        stage: Optional[str] = Query(None, max_length=200, description="One onboarding stage, or several separated by commas"),
        assigned_to: Optional[str] = Query(None, max_length=40, description="The onboarding employee: an id or 'me'"),
        unassigned: bool = Query(False, description="Only the onboarding records nobody owns yet"),
        open: bool = Query(False, description="Only the records that are not yet activated"),
        started_from: Optional[date] = Query(None, description="Started on or after (date, Baghdad time)"),
        started_to: Optional[date] = Query(None, description="Started on or before (date, Baghdad time)"),
        q: Optional[str] = _Q,
    ):
        self.stage, self.assigned_to, self.unassigned, self.open = stage, assigned_to, unassigned, open
        self.started_from, self.started_to, self.q = started_from, started_to, q

    def to_filters(self, ctx: StaffContext) -> OnboardingFilters:
        return OnboardingFilters(
            stages=_choices(self.stage, ONBOARDING_STAGES, "stage"), assigned_to=_person(self.assigned_to, ctx, "assigned_to"),
            unassigned=self.unassigned, open_only=self.open,
            started_from=self.started_from, started_to=self.started_to, q=self.q,
        )
