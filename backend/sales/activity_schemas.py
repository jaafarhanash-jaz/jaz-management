"""Request and response models for the Phase-3 Sales API (calls, follow-ups, demos, trials).

Requests reject unknown fields outright (422, like the Phase-1/2 schemas): a client cannot smuggle `status`,
`completed_at`, `created_by`, `employee_id`, `lead_id` (on an update) ... into a create/update call. Those change only
through their own endpoints, each behind its own permission. Every timestamp must carry a timezone (a naive
"2026-09-25T10:00:00" is ambiguous, so it is a 422) and must lie between the years 2000 and 2100.

Responses are explicit shapes; anything not declared here never leaves the server.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from sales.constants import MAX_CALL_SECONDS, MAX_TIMESTAMP, MIN_TIMESTAMP
from sales.lead_schemas import UserRef, _blank_to_none, _Strict

CallResult = Literal["answered", "no_answer", "busy", "wrong_number", "interested", "not_interested", "call_back"]
FollowupStatus = Literal["pending", "completed", "cancelled"]
DemoStatus = Literal["scheduled", "completed", "cancelled", "no_show"]
TrialStatus = Literal["active", "completed", "cancelled"]

_NOTES_MAX = 2000      # a call / follow-up / demo / trial note
_ACTION_NOTE_MAX = 1000  # the optional note that goes with completing / cancelling


def _sane_time(value):
    if value is not None and not (MIN_TIMESTAMP <= value <= MAX_TIMESTAMP):
        raise ValueError("must be a time between the years 2000 and 2100")
    return value


class ActionNote(_Strict):
    """The optional note that goes with complete / cancel / no-show. It is recorded on the lead's timeline."""

    note: Optional[str] = Field(default=None, max_length=_ACTION_NOTE_MAX)
    _v_note = field_validator("note", mode="before")(_blank_to_none)


# =============================================================================
# requests - calls
# =============================================================================
class CallCreate(_Strict):
    lead_id: uuid.UUID
    result: CallResult
    called_at: Optional[AwareDatetime] = None                  # default: now
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=MAX_CALL_SECONDS)
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    _v_time = field_validator("called_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class CallUpdate(_Strict):
    """PATCH: only the fields present are changed; an explicit null (or blank) clears an optional field."""

    result: Optional[CallResult] = None
    called_at: Optional[AwareDatetime] = None
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=MAX_CALL_SECONDS)
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    _v_time = field_validator("called_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


# =============================================================================
# requests - follow-ups
# =============================================================================
class FollowupCreate(_Strict):
    lead_id: uuid.UUID
    due_at: AwareDatetime
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    # Nobody named = the caller. Naming somebody else needs sales.leads.assign (checked server-side).
    assigned_to: Optional[uuid.UUID] = None
    _v_time = field_validator("due_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class FollowupUpdate(_Strict):
    due_at: Optional[AwareDatetime] = None
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    assigned_to: Optional[uuid.UUID] = None                    # needs sales.leads.assign
    _v_time = field_validator("due_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


# =============================================================================
# requests - demos
# =============================================================================
class DemoCreate(_Strict):
    lead_id: uuid.UUID
    scheduled_at: AwareDatetime
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    assigned_to: Optional[uuid.UUID] = None
    _v_time = field_validator("scheduled_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class DemoUpdate(_Strict):
    """Notes and who has the demo. Moving it in time is its own action (reschedule)."""

    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    assigned_to: Optional[uuid.UUID] = None                    # needs sales.leads.assign
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class DemoReschedule(_Strict):
    scheduled_at: AwareDatetime
    note: Optional[str] = Field(default=None, max_length=_ACTION_NOTE_MAX)     # why (recorded on the timeline)
    _v_time = field_validator("scheduled_at", mode="after")(_sane_time)
    _v_note = field_validator("note", mode="before")(_blank_to_none)


# =============================================================================
# requests - trials
# =============================================================================
class TrialCreate(_Strict):
    lead_id: uuid.UUID
    expected_end_at: AwareDatetime
    started_at: Optional[AwareDatetime] = None                # default: now
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    _v_time = field_validator("started_at", "expected_end_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class TrialUpdate(_Strict):
    expected_end_at: Optional[AwareDatetime] = None
    notes: Optional[str] = Field(default=None, max_length=_NOTES_MAX)
    _v_time = field_validator("expected_end_at", mode="after")(_sane_time)
    _v_notes = field_validator("notes", mode="before")(_blank_to_none)


class TrialComplete(_Strict):
    actual_end_at: Optional[AwareDatetime] = None              # default: now
    note: Optional[str] = Field(default=None, max_length=_ACTION_NOTE_MAX)
    _v_time = field_validator("actual_end_at", mode="after")(_sane_time)
    _v_note = field_validator("note", mode="before")(_blank_to_none)


# =============================================================================
# responses
# =============================================================================
class LeadRefOut(BaseModel):
    id: str
    business_name: str
    pipeline_stage: str
    archived: bool


class CallCan(BaseModel):
    update: bool


class CallOut(BaseModel):
    id: str
    lead: LeadRefOut
    employee: UserRef
    called_at: datetime
    duration_seconds: Optional[int] = None
    result: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    can: CallCan


class CallList(BaseModel):
    items: List[CallOut]
    total: int
    limit: int
    offset: int


class CallResultCounts(BaseModel):
    counts: Dict[str, int]
    total: int


class FollowupCan(BaseModel):
    update: bool
    complete: bool
    cancel: bool
    assign: bool


class FollowupOut(BaseModel):
    id: str
    lead: LeadRefOut
    assigned_to: UserRef
    due_at: datetime
    notes: Optional[str] = None
    status: str
    completed_at: Optional[datetime] = None
    is_overdue: bool
    created_by: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    can: FollowupCan


class FollowupList(BaseModel):
    items: List[FollowupOut]
    total: int
    limit: int
    offset: int


class FollowupCounts(BaseModel):
    pending: int
    overdue: int
    upcoming: int
    completed: int
    cancelled: int
    mine_pending: int
    mine_overdue: int


class DemoCan(BaseModel):
    update: bool
    reschedule: bool
    complete: bool
    cancel: bool
    no_show: bool
    assign: bool


class DemoOut(BaseModel):
    id: str
    lead: LeadRefOut
    assigned_to: UserRef
    scheduled_at: datetime
    status: str
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
    is_past_due: bool
    created_by: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    can: DemoCan


class DemoList(BaseModel):
    items: List[DemoOut]
    total: int
    limit: int
    offset: int


class DemoCounts(BaseModel):
    scheduled: int
    upcoming: int
    past_due: int
    completed: int
    cancelled: int
    no_show: int
    mine_scheduled: int


class TrialCan(BaseModel):
    update: bool
    complete: bool
    cancel: bool


class TrialOut(BaseModel):
    id: str
    lead: LeadRefOut
    started_at: datetime
    expected_end_at: datetime
    actual_end_at: Optional[datetime] = None
    status: str
    notes: Optional[str] = None
    is_overdue: bool
    created_by: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    can: TrialCan


class TrialList(BaseModel):
    items: List[TrialOut]
    total: int
    limit: int
    offset: int


class TrialCounts(BaseModel):
    active: int
    ending_soon: int
    overdue: int
    completed: int
    cancelled: int
