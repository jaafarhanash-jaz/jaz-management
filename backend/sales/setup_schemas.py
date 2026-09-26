"""Request and response models for the simplified Sales workflow: the Sales Manager's distribution setting, the Sales
Employee's work queue and the Customer Setup.

Requests reject unknown fields outright (422, like every other Sales schema): a client cannot smuggle a `company_id`,
`status`, `role`, `assigned_to` ... into a setup. The secrets a setup carries - the initial passwords of the company owner
and of the employees added now - are `SecretStr`: they print as ********** if a model is ever logged or repr'd, are unwrapped
in exactly one place (services/customer_setup.py), and never reach a response, the timeline or the audit trail.
"""
import uuid
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from sales.constants import MAX_SETUP_EMPLOYEES, MAX_SETUP_TASKS
from sales.customer_schemas import (
    ConversionDuplicates,
    ConversionValues,
    CustomerOut,
    PlanOut,
    _required_text,
)
from sales.lead_schemas import LeadDetail, _blank_to_none, _Strict

SubscriptionType = Literal["trial", "paid"]
TaskPriority = Literal["low", "medium", "high", "critical"]


# =============================================================================
# distribution setting
# =============================================================================
class DistributionUpdate(_Strict):
    auto_distribution_enabled: bool
    distribution_mode: Literal["equal"] = "equal"


class RotationMember(BaseModel):
    id: str
    name: str
    email: str
    open_leads: int


class PersonRef(BaseModel):
    id: str
    name: str


class DistributionOut(BaseModel):
    auto_distribution_enabled: bool
    distribution_mode: str
    updated_at: Optional[datetime] = None
    updated_by: Optional[PersonRef] = None
    rotation: List[RotationMember]              # who receives new leads, in turn order
    next_assignee: Optional[PersonRef] = None   # whose turn it is (when the switch is on)


# =============================================================================
# the Sales Employee's work queue
# =============================================================================
class QueueOut(BaseModel):
    """One lead of the caller's queue (their open assigned leads, the longest-waiting first) and where it stands in it."""

    lead: Optional[LeadDetail] = None
    position: int                 # 0-based index of `lead` in the queue
    total: int                    # open leads waiting for the caller


# =============================================================================
# customer setup - requests
# =============================================================================
class SetupPreflightValues(ConversionValues):
    """What the preflight is asked about: the company / owner values (each optional - the lead's value otherwise)."""


class SetupEmployee(_Strict):
    """An employee account created in the new company right away (optional - the customer can add employees later)."""

    name: str = Field(max_length=200)
    email: EmailStr
    phone: str = Field(max_length=50)
    password: SecretStr = Field(min_length=1, max_length=200)     # strength enforced by the core validator
    position: Optional[str] = Field(default=None, max_length=200)
    department: Optional[str] = Field(default=None, max_length=200)

    _v_required = field_validator("name", "phone", mode="before")(_required_text)
    _v_optional = field_validator("position", "department", mode="before")(_blank_to_none)


class SetupTask(_Strict):
    """A task created in the new company right away (optional). Assigned to one of THIS request's employees, by index."""

    title: str = Field(max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    priority: TaskPriority = "medium"
    assignee: int = Field(ge=0, lt=MAX_SETUP_EMPLOYEES)
    due_date: date

    _v_title = field_validator("title", mode="before")(_required_text)
    _v_description = field_validator("description", mode="before")(_blank_to_none)


class CustomerSetup(_Strict):
    """Everything the new JAZ company needs (the same fields as the platform's own company creation), the subscription it
    starts on, and - optionally - its first employees and tasks. `confirm_duplicates` is the caller's explicit "I have seen
    the possible duplicates and continue"."""

    business_name: str = Field(max_length=200)
    owner_name: str = Field(max_length=200)
    owner_email: EmailStr
    owner_phone: str = Field(max_length=50)
    owner_password: SecretStr = Field(min_length=1, max_length=200)
    address: Optional[str] = Field(default=None, max_length=500)
    subscription_plan_id: uuid.UUID
    subscription_type: SubscriptionType
    employees: List[SetupEmployee] = Field(default_factory=list, max_length=MAX_SETUP_EMPLOYEES)
    tasks: List[SetupTask] = Field(default_factory=list, max_length=MAX_SETUP_TASKS)
    confirm_duplicates: bool = False

    _v_required = field_validator("business_name", "owner_name", "owner_phone", mode="before")(_required_text)
    _v_address = field_validator("address", mode="before")(_blank_to_none)


# =============================================================================
# customer setup - responses
# =============================================================================
class SetupLeadOut(BaseModel):
    id: str
    business_name: str
    pipeline_stage: str
    archived: bool
    assigned_to: Optional[PersonRef] = None


class SetupValuesOut(BaseModel):
    business_name: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None
    address: Optional[str] = None


class SetupBlocker(BaseModel):
    code: str
    field: Optional[str] = None
    message: str


class SubscriptionOption(BaseModel):
    type: SubscriptionType
    days: Optional[int] = None       # trial: its fixed length in days; paid: follows the plan (30 days per plan month)
    hours: Optional[int] = None      # trial: exactly this many hours from the moment it is created (7 x 24)
    exact: bool = False              # the end is an exact instant (trial) rather than a calendar date (paid)


class SetupPreflightOut(BaseModel):
    lead: SetupLeadOut
    already_converted: bool
    customer: Optional[CustomerOut] = None
    values: SetupValuesOut
    blockers: List[SetupBlocker]
    duplicates: ConversionDuplicates
    requires_confirmation: bool      # only "possible" duplicates remain: continue with confirm_duplicates
    can_setup: bool                  # no blocker: completing the setup now would (with confirmation, if required) succeed
    marks_won: bool                  # completing it also moves the lead to `won` (it is not won yet)
    plans: List[PlanOut]
    subscription_options: List[SubscriptionOption]
    today: date                      # the date a PAID subscription would start on (UTC - the platform's expiry clock)
    now: datetime                    # the server's clock: a TRIAL starts at the moment the setup completes
    local_today: date                # the application's date (Baghdad): the earliest due date a task may have


class SetupSubscriptionOut(BaseModel):
    type: SubscriptionType
    plan_id: str
    plan_name: str
    start_date: date
    end_date: date
    starts_at: datetime              # UTC instants: a trial runs exactly 7 x 24 h (starts_at -> ends_at)
    ends_at: datetime
    ends_exactly: bool               # true = ends AT ends_at (trial); false = the end date is included (paid)


class SetupResult(BaseModel):
    customer: CustomerOut
    already_converted: bool          # true = nothing was created: the lead had been converted before
    subscription: Optional[SetupSubscriptionOut] = None
    employees_added: int = 0
    tasks_created: int = 0
