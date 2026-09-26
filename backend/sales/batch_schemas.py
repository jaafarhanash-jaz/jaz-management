"""Response and request models of the Sales Manager's batch, search and performance views (migration f2b6d8a1c4e9).

None of these shapes is used by a Data Entry or Sales Employee route: they exist only behind sales.batches.view /
sales.performance.view (and the all-leads scope). The one request body, ReassignBatch, rejects unknown fields like every Sales
request.
"""
import uuid
from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from sales.lead_schemas import _Strict


class Person(BaseModel):
    id: str
    name: str


# =============================================================================
# statistics
# =============================================================================
class AttemptStats(BaseModel):
    """The split of one salesperson's decisions on a set of leads. Percentages are of the DECIDED leads (accepted + rejected,
    direct or after the wait list); `wait_list_rate` is the share of all leads that were put on the wait list."""

    total: int
    accepted_direct: int
    rejected_direct: int
    wait_listed: int
    wait_accepted: int
    wait_rejected: int
    wait_pending: int
    released: int
    not_started: int = 0            # a reassigned lead the new salesperson has not decided yet
    accepted: int                   # accepted_direct + wait_accepted
    rejected: int                   # rejected_direct + wait_rejected
    decided: int
    pct_accepted_direct: float
    pct_rejected_direct: float
    pct_wait_accepted: float
    pct_wait_rejected: float
    pct_accepted: float
    pct_rejected: float
    wait_list_rate: float


# =============================================================================
# overview / data batches / master batches
# =============================================================================
class OpenDataBatch(BaseModel):
    id: str
    seq: int
    lead_count: int


class BatchesOverview(BaseModel):
    data_batch_size: int
    master_batch_size: int
    open_data_batch: Optional[OpenDataBatch] = None
    full_data_batches: int
    data_batches_toward_next_master: int
    master_batches: int
    work_batches_open: int
    work_batches_closed: int
    wait_list_pending: int


class MasterRef(BaseModel):
    id: str
    seq: int


class DataBatchOut(BaseModel):
    id: str
    seq: int
    status: str
    lead_count: int
    capacity: int
    opened_at: datetime
    filled_at: Optional[datetime] = None
    master_batch: Optional[MasterRef] = None


class DataBatchList(BaseModel):
    items: List[DataBatchOut]
    total: int
    limit: int
    offset: int


class BatchLead(BaseModel):
    """A lead as a batch shows it: what it is, who entered it and when (permanent), and where it stands now."""

    id: str
    business_name: str
    business_type: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    pipeline_stage: str
    entered_by: Person
    entered_at: datetime
    assigned_to: Optional[Person] = None
    archived: bool = False


class Entrant(BaseModel):
    id: str
    name: str
    leads: int


class DataBatchDetail(DataBatchOut):
    entrants: List[Entrant]
    leads: List[BatchLead]


class MasterBatchOut(BaseModel):
    id: str
    seq: int
    formed_at: datetime
    data_batch_count: int
    lead_count: int


class MasterBatchList(BaseModel):
    items: List[MasterBatchOut]
    total: int
    limit: int
    offset: int


class MasterBatchDetail(MasterBatchOut):
    data_batches: List[DataBatchOut]


# =============================================================================
# work batches
# =============================================================================
class BatchAttemptOut(BaseModel):
    id: str
    attempt_no: int
    kind: str                       # initial | reassignment
    status: str                     # open | closed
    employee: Person                # who works (worked) this attempt
    started_by: Person              # the salesperson themself (initial) or the manager who reassigned
    started_at: datetime
    closed_at: Optional[datetime] = None
    lead_count: int
    stats: AttemptStats


class WorkBatchOut(BaseModel):
    id: str
    seq: int
    status: str                     # open | closed
    lead_count: int
    created_at: datetime
    closed_at: Optional[datetime] = None
    original_employee: Person
    current_employee: Person        # who holds the latest attempt
    attempt_count: int
    stats: AttemptStats             # the initial attempt's split
    can_reassign: bool


class WorkBatchList(BaseModel):
    items: List[WorkBatchOut]
    total: int
    limit: int
    offset: int


class LeadAttemptOut(BaseModel):
    """One salesperson's try at one lead: what they first decided, how it ended, when."""

    attempt_no: int                 # the lead's own attempt number (1, 2 ...)
    employee: Person
    recorded_by: Person
    first_outcome: str              # accepted | rejected | wait_list
    outcome_at: datetime
    wait_listed_at: Optional[datetime] = None
    result: Optional[str] = None    # accepted | rejected | released | null (still pending on the wait list)
    result_at: Optional[datetime] = None
    path: str                       # direct_accepted | direct_rejected | wait_accepted | wait_rejected | wait_pending | released
    work_batch_seq: Optional[int] = None
    batch_attempt_no: Optional[int] = None


class WorkBatchLead(BatchLead):
    ordinal: int
    attempts: List[LeadAttemptOut]


class WorkBatchDetail(WorkBatchOut):
    attempts: List[BatchAttemptOut]
    leads: List[WorkBatchLead]


class ReassignBatch(_Strict):
    employee_id: uuid.UUID


# =============================================================================
# search / trace
# =============================================================================
class BatchRef(BaseModel):
    id: str
    seq: int
    status: Optional[str] = None


class TraceLead(BatchLead):
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    data_batch: Optional[BatchRef] = None
    master_batch: Optional[MasterRef] = None
    work_batches: List[BatchRef]
    attempts: List[LeadAttemptOut]


class SearchResults(BaseModel):
    items: List[TraceLead]
    total: int
    limit: int


class TimelineEvent(BaseModel):
    id: str
    seq: int
    event_type: str
    occurred_at: datetime
    actor: Person
    before: Optional[dict] = None
    after: Optional[dict] = None
    note: Optional[str] = None
    metadata: Optional[dict] = None


class LeadTrace(TraceLead):
    """The full trail of one lead: Lead -> Data Batch -> Master Batch -> Work Batch attempts -> results, with its timeline."""

    timeline: List[TimelineEvent]
    timeline_total: int


# =============================================================================
# performance
# =============================================================================
class DataEntryPeriod(BaseModel):
    leads_entered: int
    work_hours: float


class SalesPeriod(BaseModel):
    stats: AttemptStats
    completed_batches: int
    work_hours: float


class PeriodMap(BaseModel):
    today: Optional[DataEntryPeriod] = None
    month: Optional[DataEntryPeriod] = None
    year: Optional[DataEntryPeriod] = None
    lifetime: Optional[DataEntryPeriod] = None


class SalesPeriodMap(BaseModel):
    today: Optional[SalesPeriod] = None
    month: Optional[SalesPeriod] = None
    year: Optional[SalesPeriod] = None
    lifetime: Optional[SalesPeriod] = None


class DataEntryPerformance(BaseModel):
    user: Person
    status: Optional[str] = None
    periods: PeriodMap


class SalesPerformance(BaseModel):
    user: Person
    status: Optional[str] = None
    periods: SalesPeriodMap


class DataEntryPerformanceList(BaseModel):
    period_starts: Dict[str, Optional[datetime]]
    items: List[DataEntryPerformance]
    total: int
    limit: int
    offset: int


class SalesPerformanceList(BaseModel):
    period_starts: Dict[str, Optional[datetime]]
    items: List[SalesPerformance]
    total: int
    limit: int
    offset: int


class EmployeePerformance(BaseModel):
    user: Person
    status: Optional[str] = None
    period_starts: Dict[str, Optional[datetime]]
    data_entry: Optional[PeriodMap] = None
    sales: Optional[SalesPeriodMap] = None


class HistoryDay(BaseModel):
    date: date
    leads_entered: int = 0
    worked: int = 0
    accepted_direct: int = 0
    rejected_direct: int = 0
    wait_listed: int = 0
    wait_accepted: int = 0
    wait_rejected: int = 0
    work_hours: float = 0.0


class WorkSessionOut(BaseModel):
    started_at: datetime
    ended_at: datetime
    actions: int
    minutes: float


class EmployeeHistory(BaseModel):
    user: Person
    days: List[HistoryDay]           # newest first; a day with no recorded work is left out
    sessions: List[WorkSessionOut]   # the most recent work sessions, newest first
