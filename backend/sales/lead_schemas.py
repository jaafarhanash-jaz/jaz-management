"""Request and response models for the Phase-2 Sales API (leads, campaigns, sources, timeline).

Requests reject unknown fields outright (422, like the Phase-1 schemas): a client cannot smuggle `pipeline_stage`,
`assigned_to` (where not offered), `created_by`, `archived_at`, a normalized column ... into a create/update call.
Those change only through their own endpoints, each behind its own permission.

Responses are explicit shapes; anything not declared here never leaves the server (the normalized duplicate
columns, for one).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from sales.constants import DEFAULT_CAMPAIGN_STATUS, DEFAULT_PRIORITY, DEFAULT_SOURCE

PipelineStage = Literal[
    "new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won", "lost"
]
Priority = Literal["low", "medium", "high"]
LostReason = Literal[
    "too_expensive", "not_interested", "already_using_another_system", "no_response", "wrong_number",
    "business_closed", "not_suitable", "delayed_decision", "competitor", "other",
]
CampaignStatus = Literal["active", "paused", "completed"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _blank_to_none(value: Any) -> Any:
    """Surrounding whitespace is dropped; a blank string means 'no value' (how a cleared form field arrives).
    A NUL character is refused outright: PostgreSQL text and JSONB cannot store it, so it would be a 500."""
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("must not contain null characters")
        value = value.strip()
        return value or None
    return value


# =============================================================================
# requests - leads
# =============================================================================
_TEXT_FIELDS = (
    "business_name", "business_type", "description", "contact_name", "contact_position", "phone", "whatsapp",
    "email", "website", "country", "city", "address", "source", "notes",
)


class _LeadFields(_Strict):
    """The user-editable lead fields. Everything optional here; LeadCreate makes business_name required."""

    business_name: Optional[str] = Field(default=None, max_length=200)
    business_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_position: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=50)
    whatsapp: Optional[str] = Field(default=None, max_length=50)
    email: Optional[EmailStr] = None
    website: Optional[str] = Field(default=None, max_length=255)
    country: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=500)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    source: Optional[str] = Field(default=None, max_length=50)
    campaign_id: Optional[uuid.UUID] = None
    priority: Optional[Priority] = None
    estimated_value: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    notes: Optional[str] = Field(default=None, max_length=5000)

    _v_text = field_validator(*_TEXT_FIELDS, mode="before")(_blank_to_none)


class LeadCreate(_LeadFields):
    business_name: str = Field(max_length=200)
    source: str = Field(default=DEFAULT_SOURCE, max_length=50)
    priority: Priority = DEFAULT_PRIORITY
    # Choosing the owner while creating needs sales.leads.assign as well (checked server-side).
    assigned_to: Optional[uuid.UUID] = None
    # The caller has seen the duplicate report and wants the lead created anyway.
    confirm_duplicates: bool = False


class LeadUpdate(_LeadFields):
    """PATCH: only the fields present are changed; an explicit null (or blank) clears an optional field."""

    confirm_duplicates: bool = False


class DuplicateCheckRequest(_Strict):
    business_name: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=50)
    whatsapp: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=254)   # plain text: a half-typed address must not 422
    website: Optional[str] = Field(default=None, max_length=255)
    exclude_lead_id: Optional[uuid.UUID] = None                   # the lead being edited

    _v_text = field_validator("business_name", "city", "phone", "whatsapp", "email", "website", mode="before")(_blank_to_none)


class AssignRequest(_Strict):
    assigned_to: uuid.UUID


class BulkAssignRequest(_Strict):
    lead_ids: List[uuid.UUID] = Field(min_length=1, max_length=100)
    assigned_to: uuid.UUID


class StageChange(_Strict):
    stage: PipelineStage
    lost_reason: Optional[LostReason] = None
    note: Optional[str] = Field(default=None, max_length=1000)

    _v_note = field_validator("note", mode="before")(_blank_to_none)


# =============================================================================
# requests - campaigns
# =============================================================================
def _required_text(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None:
        raise ValueError("must not be blank")
    return value


class CampaignCreate(_Strict):
    name: str = Field(max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    source: Optional[str] = Field(default=None, max_length=50)
    status: CampaignStatus = DEFAULT_CAMPAIGN_STATUS
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    _v_name = field_validator("name", mode="before")(_required_text)
    _v_optional = field_validator("description", "source", mode="before")(_blank_to_none)


class CampaignUpdate(_Strict):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    source: Optional[str] = Field(default=None, max_length=50)
    status: Optional[CampaignStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    _v_text = field_validator("name", "description", "source", mode="before")(_blank_to_none)


# =============================================================================
# responses
# =============================================================================
class UserRef(BaseModel):
    id: str
    name: str
    status: Optional[str] = None


class CampaignRef(BaseModel):
    id: str
    name: Optional[str] = None
    status: Optional[str] = None


class LeadCan(BaseModel):
    """What the caller may do to this lead right now (UI hint; every endpoint enforces it anyway)."""

    update: bool
    change_stage: bool
    assign: bool
    archive: bool
    restore: bool


class LeadSummary(BaseModel):
    id: str
    business_name: str
    business_type: Optional[str] = None
    contact_name: Optional[str] = None
    contact_position: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source: str
    campaign_id: Optional[str] = None
    campaign: Optional[CampaignRef] = None
    pipeline_stage: str
    lost_reason: Optional[str] = None
    assigned_to: Optional[UserRef] = None
    assigned_at: Optional[datetime] = None
    priority: str
    estimated_value: Optional[float] = None
    closed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    created_by: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    allowed_stages: List[str]
    can: LeadCan


class LeadDetail(LeadSummary):
    description: Optional[str] = None
    notes: Optional[str] = None


class LeadList(BaseModel):
    items: List[LeadSummary]
    total: int
    limit: int
    offset: int


class LeadResult(BaseModel):
    changed: bool
    lead: LeadDetail


class BulkAssignResult(BaseModel):
    total: int
    assigned: int
    unchanged: int


class StageCounts(BaseModel):
    counts: Dict[str, int]
    total: int


class SourceOut(BaseModel):
    key: str
    name_en: str
    name_ar: str
    sort_order: int


class AssigneeOut(BaseModel):
    id: str
    name: str
    email: str
    open_leads: int


class ActivityActor(BaseModel):
    id: str
    name: Optional[str] = None


class ActivityOut(BaseModel):
    id: str
    seq: int
    event_type: str
    occurred_at: datetime
    actor: ActivityActor
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    metadata: Dict[str, Any]


class ActivityList(BaseModel):
    items: List[ActivityOut]
    total: int
    limit: int
    offset: int


class DuplicateMatchOut(BaseModel):
    field: str          # which of the candidate's fields matched
    kind: str           # exact | possible
    matched_on: str     # what it matched on the other record


class DuplicateLeadOut(BaseModel):
    id: Optional[str] = None      # only for leads the caller may open
    business_name: str
    city: Optional[str] = None
    pipeline_stage: str
    archived: bool
    assigned: bool
    assigned_to: Optional[UserRef] = None
    in_scope: bool
    severity: str
    matches: List[DuplicateMatchOut]


class DuplicateCompanyOut(BaseModel):
    id: str
    name: str
    severity: str
    matches: List[DuplicateMatchOut]


class DuplicateReportOut(BaseModel):
    has_exact: bool
    has_possible: bool
    can_override: bool
    leads: List[DuplicateLeadOut]
    companies: List[DuplicateCompanyOut]


class CampaignOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    source: Optional[str] = None
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_by: UserRef
    created_at: datetime
    updated_at: datetime
    lead_count: int


class CampaignList(BaseModel):
    items: List[CampaignOut]
    total: int
    limit: int
    offset: int


class Deleted(BaseModel):
    deleted: bool
