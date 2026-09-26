"""Request and response models for the Phase-4 Sales API (conversion, customers, onboarding).

Requests reject unknown fields outright (422, like the Phase-1/2/3 schemas): a client cannot smuggle `company_id`,
`lead_id`, `status`, `converted_by`, `role` ... into a conversion, nor `assigned_to` / `completed_at` into a stage change
- those change only through their own operations, each behind its own permission. The one secret a request carries, the
initial password of the company owner, is a `SecretStr`: it prints as ********** if a model is ever logged or repr'd,
is never echoed back, and never reaches the timeline or the audit trail.

Responses are explicit shapes; anything not declared here never leaves the server.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from sales.lead_schemas import DuplicateMatchOut, UserRef, _blank_to_none, _Strict

OnboardingStage = Literal[
    "won", "assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated",
]
CustomerStatus = Literal["active", "onboarding", "activated"]

_ACTION_NOTE_MAX = 1000
_NOTES_MAX = 5000


# =============================================================================
# requests - conversion  (RETIRED: POST /leads/{id}/convert[/preflight] answer 410; ConvertLead, ConvertResult and
# ConversionPreflightOut below are kept for the record - no route uses them. ConversionValues and ConversionDuplicates are still
# used by the Customer Setup's preflight.)
# =============================================================================
class ConversionValues(_Strict):
    """What the preflight is asked about. Everything optional: what is not given is taken from the lead (its business
    name, contact name, email, phone and address), so an empty body asks "what would converting this lead as it stands
    do?"."""

    business_name: Optional[str] = Field(default=None, max_length=200)
    owner_name: Optional[str] = Field(default=None, max_length=200)
    owner_email: Optional[EmailStr] = None
    owner_phone: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=500)

    _v_text = field_validator("business_name", "owner_name", "owner_email", "owner_phone", "address", mode="before")(_blank_to_none)


def _required_text(value):
    value = _blank_to_none(value)
    if value is None:
        raise ValueError("must not be blank")
    return value


class ConvertLead(_Strict):
    """Everything the JAZ company needs - the same fields the platform's own company creation takes. The values are
    prefilled from the lead by the UI (GET the preflight first), but the request states them all: the server never
    guesses. `confirm_duplicates` is the caller's explicit "I have seen the possible duplicates and continue"."""

    business_name: str = Field(max_length=200)
    owner_name: str = Field(max_length=200)
    owner_email: EmailStr
    owner_phone: str = Field(max_length=50)
    owner_password: SecretStr = Field(min_length=1, max_length=200)   # strength enforced by the core validator
    address: Optional[str] = Field(default=None, max_length=500)
    subscription_plan_id: uuid.UUID
    confirm_duplicates: bool = False

    _v_required = field_validator("business_name", "owner_name", "owner_phone", mode="before")(_required_text)
    _v_address = field_validator("address", mode="before")(_blank_to_none)


# =============================================================================
# requests - onboarding
# =============================================================================
class OnboardingAssign(_Strict):
    assigned_to: uuid.UUID
    note: Optional[str] = Field(default=None, max_length=_ACTION_NOTE_MAX)     # recorded on the timeline
    _v_note = field_validator("note", mode="before")(_blank_to_none)


class OnboardingStageChange(_Strict):
    stage: OnboardingStage
    note: Optional[str] = Field(default=None, max_length=_ACTION_NOTE_MAX)     # recorded on the timeline
    _v_note = field_validator("note", mode="before")(_blank_to_none)


class OnboardingUpdate(_Strict):
    """PATCH: the working notes. An explicit null (or blank) clears them."""

    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


# =============================================================================
# responses - customers
# =============================================================================
class CustomerLeadOut(BaseModel):
    id: str
    business_name: str
    pipeline_stage: str
    archived: bool
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None


class CustomerCompanyOut(BaseModel):
    id: str
    name: Optional[str] = None
    subscription_status: Optional[str] = None
    deleted: bool
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None
    employee_count: Optional[int] = None       # only on the customer's own page


class CustomerOnboardingOut(BaseModel):
    id: str
    stage: str
    assigned_to: Optional[UserRef] = None


class CustomerCan(BaseModel):
    view_lead: bool
    view_onboarding: bool


class CustomerOut(BaseModel):
    id: str
    status: str
    subscription_type: Optional[str] = None     # what the customer setup started the company on (trial | paid)
    converted_at: datetime
    converted_by: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    lead: CustomerLeadOut
    company: CustomerCompanyOut
    onboarding: Optional[CustomerOnboardingOut] = None      # only for a caller who may view that onboarding record
    can: CustomerCan


class CustomerList(BaseModel):
    items: List[CustomerOut]
    total: int
    limit: int
    offset: int


class CustomerCounts(BaseModel):
    counts: Dict[str, int]
    total: int


class ConvertResult(BaseModel):
    customer: CustomerOut
    already_converted: bool        # true = nothing was created: the lead had been converted before


class ConversionStatusOut(BaseModel):
    lead_stage: str
    archived: bool
    converted: bool
    customer: Optional[CustomerOut] = None
    can_convert: bool              # ALWAYS false: the Phase-4 conversion is retired (410) - the Customer Setup (`can_setup`) replaces it
    blocker: Optional[str] = None  # `conversion_retired` until the lead has a customer
    can_setup: bool = False        # the Customer Setup may start from this lead now (won, or open and assigned)
    setup_blocker: Optional[str] = None   # lead_archived | lead_lost | lead_unassigned


class PlanOut(BaseModel):
    id: str
    name: str
    price: float
    duration_months: int
    max_employees: int


class ConversionLeadOut(BaseModel):
    id: str
    business_name: str
    pipeline_stage: str
    archived: bool


class ConversionValuesOut(BaseModel):
    business_name: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None
    address: Optional[str] = None


class ConversionBlocker(BaseModel):
    code: str
    field: Optional[str] = None
    message: str


class ConversionCompanyMatch(BaseModel):
    id: str
    name: str
    severity: str
    matches: List[DuplicateMatchOut]


class ConversionDuplicates(BaseModel):
    has_exact: bool
    has_possible: bool
    companies: List[ConversionCompanyMatch]


class ConversionPreflightOut(BaseModel):
    lead: ConversionLeadOut
    already_converted: bool
    customer: Optional[CustomerOut] = None
    values: ConversionValuesOut
    blockers: List[ConversionBlocker]
    duplicates: ConversionDuplicates
    requires_confirmation: bool    # only "possible" duplicates remain: the caller may continue with confirm_duplicates
    can_convert: bool              # no blocker: converting now would (with confirmation, if required) succeed
    plans: List[PlanOut]


# =============================================================================
# responses - onboarding
# =============================================================================
class OnboardingCustomerOut(BaseModel):
    id: str
    status: str
    business_name: str
    contact_name: Optional[str] = None
    contact_position: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    converted_at: datetime


class OnboardingCompanyRef(BaseModel):
    id: str
    name: Optional[str] = None


class OnboardingCan(BaseModel):
    update: bool
    change_stage: bool
    assign: bool
    view_customer: bool
    view_lead: bool


class OnboardingOut(BaseModel):
    id: str
    stage: str
    assigned_to: Optional[UserRef] = None
    notes: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    customer: OnboardingCustomerOut
    company: Optional[OnboardingCompanyRef] = None    # only for a caller who may view the customer
    lead_id: Optional[str] = None                     # only for a caller who may view the customer's lead
    allowed_stages: List[str]
    can: OnboardingCan


class OnboardingList(BaseModel):
    items: List[OnboardingOut]
    total: int
    limit: int
    offset: int


class OnboardingCounts(BaseModel):
    total: int
    open: int
    unassigned: int
    mine: int
    stages: Dict[str, int]


class OnboardingAssigneeOut(BaseModel):
    id: str
    name: str
    email: str
    open_onboarding: int
