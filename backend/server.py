from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio
import json
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import List, Literal, Optional, Dict, Any
from datetime import datetime, timezone
import qrcode
from io import BytesIO
import base64
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine, get_db, SessionLocal
import repositories.companies as companies_repo
import repositories.users as users_repo
import services.admin as admin_service
import services.announcements as announcements_service
import services.attendance as attendance_service
import services.auth as auth_service
import services.calendar as calendar_service
import services.dashboard as dashboard_service
import services.departments as departments_service
import services.devices as devices_service
import services.employees as employees_service
import services.heartbeat as heartbeat_service
import services.messages as messages_service
import services.notifications as notifications_service
import services.realtime as realtime_service
import services.reports as reports_service
import services.schedule_resolution as schedule_resolution
import services.seed as seed_service
import services.subscriptions as subscriptions_service
import services.tasks as tasks_service
import services.work_schedules as work_schedules_service
from services.admin import parse_uuid

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')

# Security
security = HTTPBearer()

# Create the main app
# Swagger/ReDoc/OpenAPI schema expose every route, model, and field name in
# the app (including admin-only endpoints) to anyone who requests them,
# unauthenticated - reconnaissance value with no offsetting benefit once
# this is a real deployment rather than a team iterating against local docs.
_docs_enabled = ENVIRONMENT != "production"
app = FastAPI(
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Rate limiting - keyed by client IP, applied selectively to
# brute-force-sensitive endpoints (login) rather than globally.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Global request body size cap - nothing enforced this anywhere, so an
# unauthenticated caller could send an arbitrarily large body to any
# endpoint (even /auth/login) and force the server to buffer it entirely
# in memory before validation ever runs, a DoS affecting every tenant.
# 20MB comfortably covers the largest legitimate payload today (a 10MB
# attachment, base64-inflated to ~13.3MB, plus JSON overhead).
MAX_REQUEST_BODY_BYTES = 20 * 1024 * 1024

@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_REQUEST_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    return await call_next(request)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    # HSTS only takes effect over a real HTTPS connection - browsers ignore
    # it over plain HTTP, so it's harmless to always send.
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Relevant to /docs, /redoc, and any future HTML this app ever serves -
    # a pure-JSON response can't itself be clickjacked, but this is
    # zero-cost defense in depth.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

api_router = APIRouter(prefix="/api")

# ============ Models ============

class UserRole:
    SUPER_ADMIN = "super_admin"
    COMPANY_OWNER = "company_owner"
    EMPLOYEE = "employee"

class RecurrenceType:
    # Only DAILY is implemented now; the schema supports the others so
    # recurring schedules can be extended later without a migration.
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SELECTED_DAYS = "selected_days"

class MessagePriority:
    NORMAL = "normal"
    IMPORTANT = "important"
    URGENT = "urgent"

class MessageConfidentiality:
    # Stored/displayed only in this phase - no access-control logic keys off
    # this yet, per the explicit "for future permission expansion" scope.
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    HIGHLY_CONFIDENTIAL = "highly_confidential"

class MessageRecipientType:
    EMPLOYEE = "employee"
    DEPARTMENT = "department"
    # COMPANY (future broadcast) intentionally not implemented - resolving
    # "department" at send time into individual recipient rows already proves
    # the fan-out shape scales to "every employee" without a redesign.

class MessageRecipientStatus:
    DELIVERED = "delivered"
    SEEN = "seen"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    ARCHIVED = "archived"

class EventCategory:
    MEETING = "meeting"
    COMPANY_HOLIDAY = "company_holiday"
    TRAINING = "training"
    TASK_DEADLINE = "task_deadline"
    MAINTENANCE = "maintenance"
    BUSINESS_TRIP = "business_trip"
    REMINDER = "reminder"
    ANNOUNCEMENT = "announcement"
    PERSONAL = "personal"
    OTHER = "other"
    # New categories are just new string values - nothing else in the schema
    # is keyed off a fixed enum, so adding one later needs no migration.

EVENT_CATEGORY_DEFAULT_COLORS = {
    EventCategory.MEETING: "#0033A0",
    EventCategory.COMPANY_HOLIDAY: "#00A36C",
    EventCategory.TRAINING: "#7C3AED",
    EventCategory.TASK_DEADLINE: "#E11D48",
    EventCategory.MAINTENANCE: "#6B7280",
    EventCategory.BUSINESS_TRIP: "#0EA5E9",
    EventCategory.REMINDER: "#FFB800",
    EventCategory.ANNOUNCEMENT: "#EC4899",
    EventCategory.PERSONAL: "#A855F7",
    EventCategory.OTHER: "#A1A1AA",
}

class EventPriority:
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

class EventVisibility:
    PRIVATE = "private"
    DEPARTMENT = "department"
    COMPANY = "company"
    OWNER_ONLY = "owner_only"

class EventRecipientType:
    OWNER = "owner"
    EMPLOYEE = "employee"
    DEPARTMENT = "department"
    OWNER_PLUS_EMPLOYEES = "owner_plus_employees"
    COMPANY = "company"  # every employee - used now for Company Holidays,
    # generally available since the fan-out code path is identical to
    # "department" with no department filter.


class RecurrenceType:
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    CUSTOM = "custom"

class RecurrenceEndType:
    NEVER = "never"
    AFTER_COUNT = "after_count"
    ON_DATE = "on_date"

class EventEditScope:
    THIS_EVENT_ONLY = "this_event_only"
    THIS_AND_FUTURE = "this_and_future"
    ENTIRE_SERIES = "entire_series"

# ============ Request/Response Models ============

class LoginRequest(BaseModel):
    email_or_phone: str
    password: str

class LoginResponse(BaseModel):
    token: str
    refresh_token: str
    user: Dict[str, Any]
    role: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class CvUploadRequest(BaseModel):
    filename: str
    mime_type: str
    data: str  # base64, same client-side-encode contract as message/calendar attachments

class PhotoUploadRequest(BaseModel):
    filename: str
    mime_type: str
    data: str  # base64, same client-side-encode contract as CV/message/calendar attachments

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UserCreate(BaseModel):
    email: EmailStr
    phone: str
    password: str
    name: str
    role: str
    company_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    # Employee CV (Part 1) - optional, uploaded in the same request as
    # employee creation when the owner attaches one right away.
    cv: Optional[CvUploadRequest] = None

class UserResponse(BaseModel):
    id: str
    email: str
    phone: str
    name: str
    role: str
    company_id: Optional[str] = None
    avatar: Optional[str] = None
    status: str = "active"
    department: Optional[str] = None
    position: Optional[str] = None
    schedule_id: Optional[str] = None
    schedule_name: Optional[str] = None
    cv: Optional[Dict[str, Any]] = None
    photo: Optional[Dict[str, Any]] = None

class CompanyCreate(BaseModel):
    name: str
    owner_email: EmailStr
    owner_name: str
    owner_password: str
    owner_phone: str
    address: Optional[str] = None
    subscription_plan_id: str

class CompanyResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    qr_code: str
    subscription_status: str
    subscription_plan_id: Optional[str] = None
    subscription_start_date: Optional[str] = None
    subscription_end_date: Optional[str] = None
    subscription_price: float = 0
    max_employees: Optional[int] = None
    subscription_duration_months: Optional[int] = None
    subscription_features: Optional[List[str]] = None
    address: Optional[str] = None
    created_at: str
    employee_count: int = 0
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None
    owner_online: Optional[bool] = None
    employees_online: Optional[int] = None
    company_online: Optional[bool] = None
    last_seen: Optional[str] = None

class SubscriptionActivate(BaseModel):
    subscription_start_date: str
    subscription_end_date: str

class SubscriptionRenew(BaseModel):
    subscription_plan_id: Optional[str] = None

class CompanyOwnerUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    subscription_plan_id: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[EmailStr] = None
    owner_phone: Optional[str] = None
    owner_password: Optional[str] = None
    owner_password_confirm: Optional[str] = None

class SubscriptionPlanCreate(BaseModel):
    name: str
    max_employees: int = Field(gt=0)
    price: float = Field(ge=0)
    duration_months: int = Field(gt=0)
    features: List[str] = []

class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    max_employees: Optional[int] = Field(default=None, gt=0)
    price: Optional[float] = Field(default=None, ge=0)
    duration_months: Optional[int] = Field(default=None, gt=0)
    features: Optional[List[str]] = None
    is_active: Optional[bool] = None

class SubscriptionPlanResponse(BaseModel):
    id: str
    name: str
    max_employees: int
    price: float
    duration_months: int
    features: List[str]
    is_active: bool = True

class TaskCreate(BaseModel):
    title: str
    description: str
    priority: str
    assigned_to: str
    due_date: str
    # Optional - the plain Task dialog doesn't collect these, the Urgent
    # Task dialog does. Additive, mirrors UrgentTaskCreate's due_time and the
    # calendar/message attachment shape (filename/mime_type/base64 data).
    due_time: Optional[str] = None
    attachments: Optional[List[Dict[str, str]]] = None
    requires_proof: bool = False
    # Scheduled Task (Part 3) - optional future activation. Omitted/blank
    # scheduled_date means "activate immediately", exactly today's behavior.
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None

class TaskWorkflowCreate(BaseModel):
    """Sequential Workflow (Part 4) - same shape as TaskCreate but
    `employee_order` replaces the single `assigned_to`: one task row per
    employee, activated one at a time in this exact order."""
    title: str
    description: str
    priority: str
    employee_order: List[str]
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    requires_proof: bool = False
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    requires_proof: Optional[bool] = None

class TaskResponse(BaseModel):
    id: str
    company_id: str
    assigned_to: str
    assigned_to_name: Optional[str] = None
    title: str
    description: str
    priority: str
    status: str
    # Optional because urgent tasks may now be created without a due date
    # ("Set Due Date" toggle off). Legacy tasks and daily-task instances
    # always populate a real value, so this widening doesn't affect them.
    due_date: Optional[str] = None
    requires_proof: bool
    proof_files: List[str] = []
    # Owner-provided attachments at creation time - distinct from proof_files
    # (employee-uploaded, after completion). Same base64-inline shape as
    # calendar/message attachments, stored directly on the task document.
    attachments: List[Dict[str, Any]] = []
    created_by: str
    created_by_name: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    # New, optional fields for daily/urgent tasks - always absent/None on
    # existing legacy tasks, which remain fully unaffected.
    task_category: Optional[str] = None
    # Critical Task Alert timeline - additive, mirrors the existing
    # field-on-document timeline convention (seen_at/started_at/completed_at)
    # rather than a new activity-log collection.
    alert_delivered_at: Optional[str] = None
    received_at: Optional[str] = None
    daily_task_id: Optional[str] = None
    occurrence_date: Optional[str] = None
    execution_date: Optional[str] = None
    execution_time: Optional[str] = None
    due_time: Optional[str] = None
    started_at: Optional[str] = None
    seen_at: Optional[str] = None
    completed_by: Optional[str] = None
    completed_by_name: Optional[str] = None
    batch_id: Optional[str] = None
    scheduled_activation_at: Optional[str] = None
    sequence_order: Optional[int] = None

class TaskWorkflowCreateResponse(BaseModel):
    batch_id: str
    steps: List[TaskResponse]

class TaskWorkflowStepProgress(BaseModel):
    task_id: str
    employee_id: str
    employee_name: Optional[str] = None
    sequence_order: int
    status: str

class TaskWorkflowProgressResponse(BaseModel):
    batch_id: str
    title: str
    steps: List[TaskWorkflowStepProgress]

class AnnouncementCreate(BaseModel):
    title: str
    message: str

class AnnouncementResponse(BaseModel):
    id: str
    company_id: str
    created_by: str
    created_by_name: str
    title: str
    message: str
    created_at: str

class DailyTaskCreate(BaseModel):
    title: str
    description: str
    assigned_to: List[str]
    execution_time: Optional[str] = None
    requires_proof: bool = False

class DailyTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[List[str]] = None
    execution_time: Optional[str] = None
    requires_proof: Optional[bool] = None

class DailyTaskResponse(BaseModel):
    id: str
    company_id: str
    title: str
    description: str
    assigned_to: List[str]
    assigned_to_names: List[str] = []
    execution_time: Optional[str] = None
    requires_proof: bool = False
    is_active: bool = True
    recurrence_type: str = RecurrenceType.DAILY
    recurrence_config: Dict[str, Any] = {}
    created_by: str
    created_at: str

class UrgentTaskCreate(BaseModel):
    assigned_to: List[str]
    title: str
    description: str
    # Optional: if left empty the task is Immediate (created and delivered
    # right away, same as always). If provided, stored and displayed as
    # informational metadata only - scheduling/delayed delivery is a
    # separate, not-yet-implemented feature.
    execution_date: Optional[str] = None
    execution_time: Optional[str] = None
    # Optional: if left empty ("Set Due Date" toggle off), the task has no
    # deadline.
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    requires_proof: bool = False

class TaskProofUpload(BaseModel):
    """Layer 1 of the proof-upload hardening (defense in depth - see
    the incident report): a malformed body (missing/null/empty/
    whitespace-only file_url) is now rejected here, before it ever
    reaches the service or repository layers. Previously this endpoint
    accepted a raw untyped dict, and a None file_url flowed all the
    way to `array_append(Task.proof_files, NULL)`, corrupting the
    column and breaking TaskResponse serialization for that task."""
    file_url: str

    @field_validator("file_url")
    @classmethod
    def file_url_must_be_meaningful(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("file_url must not be empty or whitespace-only")
        return value

class AttendanceCheckIn(BaseModel):
    qr_code: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    device_info: Optional[str] = None

class AttendanceCheckOut(BaseModel):
    # qr_code is Optional so legacy payloads still parse, but the endpoint
    # enforces it server-side - scanning the company QR is now required for
    # check-out exactly like check-in.
    qr_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    device_info: Optional[str] = None

class AttendanceResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: Optional[str] = None
    employee_department: Optional[str] = None
    employee_position: Optional[str] = None
    company_id: str
    date: str
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    check_in_location: Optional[Dict[str, float]] = None
    check_out_location: Optional[Dict[str, float]] = None
    distance_from_company_meters: Optional[float] = None
    check_out_distance_meters: Optional[float] = None
    working_duration_minutes: Optional[float] = None
    required_minutes: Optional[float] = None
    scheduled_break_minutes: Optional[float] = None
    net_minutes: Optional[float] = None
    overtime_minutes: Optional[float] = None
    missing_minutes: Optional[float] = None
    late_minutes: Optional[float] = None
    early_arrival_minutes: Optional[float] = None
    early_leave_minutes: Optional[float] = None
    device_info: Optional[str] = None
    created_at: Optional[str] = None
    status: str
    # Display-only annotation - Reports shows "Official Company Holiday" or
    # "Weekly Holiday" instead of the normal status label on non-working
    # dates. Never changes the underlying stored status.
    is_holiday: Optional[bool] = None
    holiday_type: Optional[str] = None  # "company_holiday" | "weekly_holiday"
    holiday_title: Optional[str] = None

class AttendanceSettingsUpdate(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[float] = Field(default=None, gt=0)
    qr_enabled: Optional[bool] = None

class AttendanceManualEdit(BaseModel):
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    status: Optional[str] = None

# ---- Work Schedules ----

class WorkScheduleCreate(BaseModel):
    name: str
    start_time: str
    end_time: str
    break_start_time: Optional[str] = None
    break_end_time: Optional[str] = None
    required_hours: float
    working_days: List[int]
    is_default: bool = False

class WorkScheduleUpdate(WorkScheduleCreate):
    pass

class WorkScheduleResponse(BaseModel):
    id: str
    name: str
    start_time: str
    end_time: str
    break_start_time: Optional[str] = None
    break_end_time: Optional[str] = None
    required_hours: float
    working_days: List[int]
    is_default: bool
    is_active: bool

class ReportCreate(BaseModel):
    title: str
    description: str
    files: List[str] = []
    images: List[str] = []

class ReportResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: Optional[str] = None
    company_id: str
    title: str
    description: str
    files: List[str]
    images: List[str]
    status: str
    created_at: str

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    company_id: Optional[str] = None
    type: str
    category: str
    title: str
    message: str
    read_status: bool
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    action_url: Optional[str] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    created_at: str

class UnreadCountResponse(BaseModel):
    count: int

class DashboardWidgetLayout(BaseModel):
    key: str
    visible: bool
    order: int
    size: Literal["sm", "md", "lg"] = "lg"
    # Granular sub-element visibility within this widget (title, action
    # button, individual charts, etc.) - keys are widget-specific, defined
    # by the frontend's WIDGET_SECTIONS registry. Absent/missing key means
    # visible (backward compatible with layouts saved before this field
    # existed - no migration needed since `layout` is schemaless JSONB).
    sections: Optional[Dict[str, bool]] = None

class DashboardLayoutRequest(BaseModel):
    layout: List[DashboardWidgetLayout]

class DashboardLayoutResponse(BaseModel):
    layout: List[DashboardWidgetLayout]

class DeviceTokenRegister(BaseModel):
    token: str
    platform: Literal["ios", "android", "web"]
    app_version: Optional[str] = None

class DeviceTokenResponse(BaseModel):
    id: str
    user_id: str
    platform: str
    app_version: Optional[str] = None
    last_seen_at: str
    created_at: str

class DepartmentCreate(BaseModel):
    name: str
    head_id: Optional[str] = None

class DepartmentResponse(BaseModel):
    id: str
    company_id: str
    name: str
    head_id: Optional[str] = None
    head_name: Optional[str] = None
    created_at: str

# ---- Work Messaging ----

class MessageCreate(BaseModel):
    subject: str
    body: str
    priority: str = MessagePriority.NORMAL
    confidentiality: str = MessageConfidentiality.INTERNAL
    tags: List[str] = []
    recipient_type: str  # "employee" | "department"
    recipient_ids: List[str] = []  # employee ids, or a single department name when recipient_type="department"
    requires_acknowledgement: bool = False
    completion_required: bool = False
    expires_at: Optional[str] = None
    is_draft: bool = False

class MessageUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    priority: Optional[str] = None
    confidentiality: Optional[str] = None
    tags: Optional[List[str]] = None
    requires_acknowledgement: Optional[bool] = None
    completion_required: Optional[bool] = None
    expires_at: Optional[str] = None

class MessageReply(BaseModel):
    body: str

class AttachmentUpload(BaseModel):
    filename: str
    mime_type: str
    data: str  # base64, same client-side-encode contract as task proof_files

class ReminderCreate(BaseModel):
    preset: Optional[str] = None  # "30m" | "1h" | "tomorrow"
    remind_at: Optional[str] = None  # explicit ISO datetime, used when preset is absent

class MessageResponse(BaseModel):
    id: str
    reference_number: str
    company_id: str
    thread_id: str
    parent_message_id: Optional[str] = None
    sender_id: str
    sender_name: Optional[str] = None
    subject: str
    body: str
    priority: str
    confidentiality: str
    tags: List[str] = []
    requires_acknowledgement: bool = False
    completion_required: bool = False
    expires_at: Optional[str] = None
    is_expired: Optional[bool] = None
    recipient_type: str
    recipient_department: Optional[str] = None
    is_draft: bool = False
    is_forward: bool = False
    forwarded_from_id: Optional[str] = None
    is_pinned: bool = False
    closed_at: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    # Populated on list/detail views only, never trusted as stored truth.
    recipient_names: Optional[List[str]] = None
    attachment_count: Optional[int] = None
    delivery_progress: Optional[Dict[str, Any]] = None
    my_status: Optional[str] = None
    my_is_unread: Optional[bool] = None
    my_is_starred: Optional[bool] = None
    reply_count: Optional[int] = None

# ---- Calendar ----

class WorkingHoursUpdate(BaseModel):
    working_days: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # 0=Sunday..6=Saturday
    start_time: str = "08:00"
    end_time: str = "17:00"

class CalendarEventCreate(BaseModel):
    title: str
    description: str = ""
    category: str
    custom_color: Optional[str] = None
    priority: str = EventPriority.NORMAL
    start_date: str  # YYYY-MM-DD
    start_time: Optional[str] = None  # HH:MM, absent when all_day
    end_date: str
    end_time: Optional[str] = None
    all_day: bool = False
    location_type: Optional[str] = None
    location: Optional[str] = None
    online_link: Optional[str] = None
    visibility: str = EventVisibility.COMPANY
    recipient_type: str = EventRecipientType.OWNER
    recipient_ids: List[str] = []  # employee ids, or department name(s) when recipient_type="department"
    recurrence_type: str = RecurrenceType.NONE
    recurrence_interval: int = 1
    recurrence_end_type: str = RecurrenceEndType.NEVER
    recurrence_end_value: Optional[str] = None  # ISO date if on_date, integer-as-string if after_count
    override_conflicts: bool = False

class CalendarEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    custom_color: Optional[str] = None
    priority: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    all_day: Optional[bool] = None
    location_type: Optional[str] = None
    location: Optional[str] = None
    online_link: Optional[str] = None
    visibility: Optional[str] = None
    override_conflicts: bool = False

class EventResponseUpdate(BaseModel):
    status: str  # accepted | declined | tentative

class EventReminderCreate(BaseModel):
    preset: Optional[str] = None
    remind_at: Optional[str] = None

class ConflictCheckRequest(BaseModel):
    recipient_type: str
    recipient_ids: List[str]
    start_date: str
    start_time: Optional[str] = None
    end_date: str
    end_time: Optional[str] = None
    all_day: bool = False
    exclude_event_id: Optional[str] = None

class ParticipantIdsBody(BaseModel):
    participant_ids: List[str]

class FinalAttendanceUpdate(BaseModel):
    status: str  # "attended" | "absent" - post-meeting, distinct from the pre-meeting RSVP

class MeetingNotesUpdate(BaseModel):
    summary: str = ""
    decisions: str = ""
    action_items: str = ""

class WeeklyHolidayPatternCreate(BaseModel):
    # 0=Sunday..6=Saturday, same convention as working_hours.working_days
    weekdays: List[int]
    title: str = "عطلة أسبوعية"
    description: str = ""

# ============ Helper Functions ============

def generate_qr_code(data: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

# Subscription-status resolution, presence computation and plan-config
# mapping moved to services/auth.py + services/admin.py (Postgres-backed).

# ---- Smart QR Attendance helpers ----

# Per-company overrides live in companies.attendance_settings, stored as a
# nested object (not flat company columns) so a future `branches` collection
# can carry this same shape per-branch without a schema redesign.
# Smart QR Attendance helpers moved to services/attendance.py (Postgres-backed).

# validate_qr_attendance moved to services/attendance.py (Postgres-backed).

# ---- Work Messaging helpers ----
# next_reference_number (MSG-###### counter) moved to
# repositories/counters.py + services/messages.py (Postgres-backed).

# Work Messaging helpers (resolve_recipients, write_message_activity,
# compute_delivery_progress, get_accessible_message, deliver_message,
# notify_reply_participants, enrich_messages) moved to services/messages.py
# (Postgres-backed) and repositories/{messages,message_recipients,
# message_attachments,counters}.py.

# ---- Calendar helpers ----
# All moved to services/calendar.py, services/calendar_occurrences.py, and
# services/holidays.py (Postgres-backed) - see Module 9's completion report.
# The last remaining Mongo-backed callers (owner/employee dashboard and
# analytics) migrated to services/dashboard.py; nothing here calls into
# Mongo anymore.

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    pg: AsyncSession = Depends(get_db),
):
    # Postgres-backed (Auth module, migrated). See services/auth.py.
    return await auth_service.get_current_user(pg, credentials.credentials)

# ============ Health ============

@api_router.get("/health")
async def health_check(pg: AsyncSession = Depends(get_db)):
    # Public and unauthenticated on purpose - this is what Docker's
    # HEALTHCHECK and any external uptime monitor hit. It used to be
    # `/api/auth/me`, which always returns 401/403 without a token, so the
    # container health check could only ever prove curl got *some* response,
    # not that the app or its database connection actually work. A cheap
    # `SELECT 1` here proves the request pipeline, the DB engine, and the
    # Supabase/PgBouncer connection are all genuinely alive; a DB outage now
    # correctly flips this to unhealthy instead of silently staying "healthy".
    try:
        await pg.execute(text("SELECT 1"))
    except Exception:
        logging.exception("Health check DB probe failed")
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
    return {"status": "healthy"}

# ============ Auth Routes ============

@api_router.post("/auth/login", response_model=LoginResponse)
@limiter.limit("60/minute")
async def login(request: Request, body: LoginRequest, pg: AsyncSession = Depends(get_db)):
    # Postgres-backed (Auth module, migrated). See services/auth.py.
    # `request: Request` (not the login body) is required here - slowapi's
    # @limiter.limit locates the request object by parameter name "request",
    # not by type, so the two must not collide.
    result = await auth_service.login(pg, body.email_or_phone, body.password)
    return LoginResponse(**result)

@api_router.post("/auth/refresh", response_model=LoginResponse)
@limiter.limit("60/minute")
async def refresh_access_token(request: Request, body: RefreshTokenRequest, pg: AsyncSession = Depends(get_db)):
    # No auth dependency - the refresh token itself is the credential here,
    # and the whole point is to work when the access token has expired.
    result = await auth_service.rotate_refresh_token(pg, body.refresh_token)
    return LoginResponse(**result)

@api_router.post("/auth/logout")
async def logout(body: LogoutRequest, pg: AsyncSession = Depends(get_db)):
    # No auth dependency either - logout must succeed even if the access
    # token already expired; the refresh token being revoked is the point.
    await auth_service.revoke_refresh_token(pg, body.refresh_token)
    return {"message": "Logged out"}

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    data = {k: v for k, v in current_user.items() if k != "password"}
    # schedule_name is display-only and resolved fresh on every call (not
    # cached on the JWT-derived current_user dict) so an owner reassigning
    # someone's schedule is reflected immediately on their next profile
    # view, without needing a fresh login.
    schedule = await schedule_resolution.get_effective_schedule_for_employee(
        pg,
        schedule_id=parse_uuid(current_user.get("schedule_id")),
        company_id=parse_uuid(current_user.get("company_id")),
    )
    data["schedule_name"] = schedule.name if schedule else None
    return UserResponse(**data)

@api_router.post("/heartbeat")
async def heartbeat(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Lightweight presence signal sent periodically by logged-in owner/employee
    sessions. No new infrastructure - a company's online status is derived by
    checking how recently its users' last_seen_at falls within
    PRESENCE_TIMEOUT_SECONDS, computed on read (see services/admin.py)."""
    # Postgres-backed. See services/heartbeat.py for the Critical Task Alert
    # fallback detection that piggybacks on this endpoint for employees.
    await users_repo.update_last_seen(pg, current_user["id"], datetime.now(timezone.utc))

    response = {"message": "ok"}
    if current_user["role"] == UserRole.EMPLOYEE:
        response["pending_critical_tasks"] = await heartbeat_service.pending_critical_tasks(pg, current_user)
    await pg.commit()
    return response

# ============ Super Admin Routes ============
# Postgres-backed (Super Admin module, migrated). Business logic lives in
# services/admin.py; these handlers only check the role and delegate.

def require_super_admin(current_user: dict):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

@api_router.get("/admin/statistics")
async def get_admin_statistics(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.get_statistics(pg)

@api_router.get("/admin/companies", response_model=List[CompanyResponse])
async def get_all_companies(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.list_companies(pg)

@api_router.post("/admin/companies", response_model=CompanyResponse)
async def create_company(company: CompanyCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.create_company(pg, company)

@api_router.put("/admin/companies/{company_id}")
async def update_company(company_id: str, updates: CompanyOwnerUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.update_company(pg, company_id, updates)

@api_router.post("/admin/companies/{company_id}/activate")
async def activate_subscription(company_id: str, data: SubscriptionActivate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.activate_subscription(pg, company_id, data)

@api_router.post("/admin/companies/{company_id}/suspend")
async def suspend_subscription(company_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.suspend_subscription(pg, company_id)

@api_router.post("/admin/companies/{company_id}/reactivate")
async def reactivate_subscription(company_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.reactivate_subscription(pg, company_id)

@api_router.post("/admin/companies/{company_id}/renew")
async def renew_subscription(company_id: str, data: SubscriptionRenew, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.renew_subscription(pg, company_id, data)

@api_router.delete("/admin/companies/{company_id}")
async def delete_company(company_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.delete_company(pg, company_id)

@api_router.get("/admin/subscription-plans", response_model=List[SubscriptionPlanResponse])
async def get_subscription_plans(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.list_plans(pg)

@api_router.post("/admin/subscription-plans", response_model=SubscriptionPlanResponse)
async def create_subscription_plan(plan: SubscriptionPlanCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.create_plan(pg, plan)

@api_router.put("/admin/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, updates: SubscriptionPlanUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.update_plan(pg, plan_id, updates)

@api_router.delete("/admin/subscription-plans/{plan_id}")
async def delete_subscription_plan(plan_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    require_super_admin(current_user)
    return await admin_service.delete_plan(pg, plan_id)

@api_router.get("/admin/users/{user_id}/devices", response_model=List[DeviceTokenResponse])
async def list_user_devices(user_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Support/debugging view - lets a super_admin confirm whether a
    given user has any live push registrations."""
    require_super_admin(current_user)
    return await devices_service.list_devices_for_user(pg, user_id)

# ============ Company Owner Routes ============

@api_router.get("/owner/dashboard")
async def get_owner_dashboard(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_owner_dashboard(pg, current_user)

@api_router.get("/owner/analytics")
async def get_owner_analytics(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Employee performance analytics, computed entirely from existing task
    and attendance data - no AI, no new data sources. Reuses the same
    completion_rate/attendance_rate calculation already used by
    /employee/performance, extended with more factors and aggregated across
    the whole company for the owner's view."""
    return await dashboard_service.get_owner_analytics(pg, current_user)

@api_router.get("/owner/employees", response_model=List[UserResponse])
async def get_employees(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: Optional[int] = None, page_size: Optional[int] = None,
):
    # page/page_size are opt-in - omitting them preserves the pre-existing
    # "return everything" behavior existing clients rely on. Passing them
    # slices the same array response (no shape change), for callers ready
    # to page through a large company's employee list.
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    limit = page_size if page is not None and page_size is not None else None
    offset = (page - 1) * page_size if limit is not None else None
    return await employees_service.list_employees(pg, current_user["company_id"], limit=limit, offset=offset)

@api_router.post("/owner/employees", response_model=UserResponse)
async def create_employee(employee: UserCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.create_employee(pg, current_user["company_id"], current_user["id"], employee)

@api_router.put("/owner/employees/{employee_id}")
async def update_employee(employee_id: str, updates: dict, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.update_employee(pg, current_user["company_id"], employee_id, updates)

@api_router.delete("/owner/employees/{employee_id}")
async def delete_employee(employee_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.delete_employee(pg, current_user["company_id"], employee_id)

@api_router.post("/owner/employees/{employee_id}/cv")
async def upload_employee_cv(employee_id: str, cv: CvUploadRequest, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Upload or Replace (Edit Employee) - same endpoint covers both, the
    previous file is deleted from storage automatically when one exists."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.upload_employee_cv(pg, current_user["company_id"], employee_id, current_user["id"], cv)

@api_router.get("/owner/employees/{employee_id}/cv")
async def get_employee_cv(employee_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Preview/Download - returns the file inline as base64, same contract
    as message/calendar attachments."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.get_employee_cv(pg, current_user["company_id"], employee_id)

@api_router.delete("/owner/employees/{employee_id}/cv")
async def delete_employee_cv(employee_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.delete_employee_cv(pg, current_user["company_id"], employee_id)

@api_router.get("/owner/tasks", response_model=List[TaskResponse])
async def get_tasks(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: Optional[int] = None, page_size: Optional[int] = None,
):
    # page/page_size are opt-in - see get_employees above for the rationale.
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    limit = page_size if page is not None and page_size is not None else None
    offset = (page - 1) * page_size if limit is not None else None
    return await tasks_service.list_tasks_for_owner(pg, current_user["company_id"], limit=limit, offset=offset)

@api_router.get("/owner/tasks/history", response_model=List[TaskResponse])
async def get_task_history(
    current_user: dict = Depends(get_current_user),
    pg: AsyncSession = Depends(get_db),
    search: Optional[str] = None,
    employee_id: Optional[str] = None,
    created_by: Optional[str] = None,
    priority: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    completed_from: Optional[str] = None,
    completed_to: Optional[str] = None,
    sort: Optional[str] = None,
):
    """Task History: the permanent completed-tasks archive, read straight
    off the same tasks table (no new table, no copy) - see
    services.tasks.list_completed_tasks_for_owner."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.list_completed_tasks_for_owner(
        pg, current_user["company_id"],
        search=search, employee_id=employee_id, created_by=created_by, priority=priority,
        created_from=created_from, created_to=created_to,
        completed_from=completed_from, completed_to=completed_to, sort=sort,
    )

@api_router.post("/owner/tasks", response_model=TaskResponse)
async def create_task(task: TaskCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.create_task(pg, current_user["company_id"], current_user["id"], task)

@api_router.put("/owner/tasks/{task_id}")
async def update_task(task_id: str, updates: TaskUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.update_task(pg, current_user["company_id"], task_id, updates)

@api_router.delete("/owner/tasks/{task_id}")
async def delete_task(task_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.delete_task(pg, current_user["company_id"], task_id)

@api_router.post("/owner/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.cancel_task(pg, current_user["company_id"], task_id)

@api_router.post("/owner/tasks/workflow", response_model=TaskWorkflowCreateResponse)
async def create_task_workflow(workflow: TaskWorkflowCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.create_task_workflow(pg, current_user["company_id"], current_user["id"], workflow)

@api_router.get("/owner/tasks/workflows", response_model=List[TaskWorkflowProgressResponse])
async def get_task_workflows(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.get_workflow_progress(pg, parse_uuid(current_user["company_id"]))

# ---- Daily Recurring Tasks (templates) ----

@api_router.get("/owner/daily-tasks", response_model=List[DailyTaskResponse])
async def get_daily_tasks(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.list_daily_tasks(pg, current_user["company_id"])

@api_router.post("/owner/daily-tasks", response_model=DailyTaskResponse)
async def create_daily_task(template: DailyTaskCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.create_daily_task(pg, current_user["company_id"], current_user["id"], template)

@api_router.put("/owner/daily-tasks/{template_id}")
async def update_daily_task(template_id: str, updates: DailyTaskUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.update_daily_task(pg, current_user["company_id"], template_id, updates)

@api_router.post("/owner/daily-tasks/{template_id}/toggle")
async def toggle_daily_task(template_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.toggle_daily_task(pg, current_user["company_id"], template_id)

@api_router.delete("/owner/daily-tasks/{template_id}")
async def delete_daily_task(template_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.delete_daily_task(pg, current_user["company_id"], template_id)

# ---- Urgent Tasks ----

@api_router.post("/owner/urgent-tasks")
async def create_urgent_task(task: UrgentTaskCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.create_urgent_task(pg, current_user["company_id"], current_user["id"], task)

@api_router.get("/owner/attendance", response_model=List[AttendanceResponse])
async def get_attendance(
    current_user: dict = Depends(get_current_user),
    pg: AsyncSession = Depends(get_db),
    date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    employee_id: Optional[str] = None,
    department: Optional[str] = None,
    position: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    schedule_id: Optional[str] = None,
    late: Optional[bool] = None,
    overtime: Optional[bool] = None,
    missing_hours: Optional[bool] = None,
    range: Optional[str] = None,
):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.list_attendance_for_owner(
        pg, current_user["company_id"], date=date, date_from=date_from, date_to=date_to,
        employee_id=employee_id, department=department, position=position, status=status,
        search=search, schedule_id=schedule_id, late=late, overtime=overtime,
        missing_hours=missing_hours, range_=range,
    )

@api_router.get("/owner/attendance/settings")
async def get_attendance_settings(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    company = await companies_repo.get_by_id(pg, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return await attendance_service.get_attendance_settings(pg, company)

@api_router.put("/owner/attendance/settings")
async def update_attendance_settings(updates: AttendanceSettingsUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.update_attendance_settings(pg, current_user, updates)

@api_router.post("/owner/attendance/qr/regenerate")
async def regenerate_attendance_qr(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.regenerate_qr_token(pg, current_user)

@api_router.get("/owner/attendance/analytics")
async def get_attendance_analytics(
    current_user: dict = Depends(get_current_user),
    pg: AsyncSession = Depends(get_db),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.get_attendance_analytics(pg, current_user["company_id"], date_from, date_to)

@api_router.get("/owner/attendance/employees/{employee_id}/stats")
async def get_employee_attendance_stats(
    employee_id: str,
    range: str = "today",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    pg: AsyncSession = Depends(get_db),
):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.get_employee_attendance_stats(
        pg, current_user["company_id"], employee_id, range, date_from, date_to,
    )

@api_router.patch("/owner/attendance/{attendance_id}")
async def edit_attendance(attendance_id: str, updates: AttendanceManualEdit, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.edit_attendance(pg, current_user, attendance_id, updates)

@api_router.get("/owner/attendance/audit-log")
async def get_attendance_audit_log(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Read-only view of the unified audit_logs table, filtered to
    entity_type='attendance'. No update/delete route exists for this
    collection anywhere - history is immutable by construction."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.get_attendance_audit_log(pg, parse_uuid(current_user["company_id"]))

@api_router.get("/owner/attendance/{attendance_id}/events")
async def get_attendance_events(attendance_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Attendance Events audit trail (every QR-scan attempt, success and
    failure) for one Attendance record - for owner troubleshooting, never
    a replacement for the Attendance record itself."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.get_attendance_events(pg, current_user["company_id"], attendance_id)

# ---- Work Schedules ----

@api_router.get("/owner/work-schedules", response_model=List[WorkScheduleResponse])
async def list_work_schedules(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.list_schedules(pg, current_user)

@api_router.post("/owner/work-schedules", response_model=WorkScheduleResponse)
async def create_work_schedule(data: WorkScheduleCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.create_schedule(pg, current_user, data)

@api_router.put("/owner/work-schedules/{schedule_id}", response_model=WorkScheduleResponse)
async def update_work_schedule(schedule_id: str, data: WorkScheduleUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.update_schedule(pg, current_user, schedule_id, data)

@api_router.post("/owner/work-schedules/{schedule_id}/deactivate")
async def deactivate_work_schedule(schedule_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.deactivate_schedule(pg, current_user, schedule_id)

@api_router.post("/owner/work-schedules/{schedule_id}/reactivate")
async def reactivate_work_schedule(schedule_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.reactivate_schedule(pg, current_user, schedule_id)

@api_router.post("/owner/work-schedules/{schedule_id}/set-default")
async def set_default_work_schedule(schedule_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await work_schedules_service.set_default_schedule(pg, current_user, schedule_id)

@api_router.get("/owner/reports", response_model=List[ReportResponse])
async def get_reports(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: Optional[int] = None, page_size: Optional[int] = None,
):
    # page/page_size are opt-in - see get_employees above for the rationale.
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    limit = page_size if page is not None and page_size is not None else None
    offset = (page - 1) * page_size if limit is not None else None
    return await reports_service.list_reports_for_owner(pg, current_user["company_id"], limit=limit, offset=offset)

@api_router.get("/owner/departments", response_model=List[DepartmentResponse])
async def get_departments(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: Optional[int] = None, page_size: Optional[int] = None,
):
    # page/page_size are opt-in - see get_employees above for the rationale.
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    limit = page_size if page is not None and page_size is not None else None
    offset = (page - 1) * page_size if limit is not None else None
    return await departments_service.list_departments(pg, current_user["company_id"], limit=limit, offset=offset)

@api_router.post("/owner/departments", response_model=DepartmentResponse)
async def create_department(department: DepartmentCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await departments_service.create_department(pg, current_user["company_id"], department)

# ============ Employee Routes ============

@api_router.get("/employee/dashboard")
async def get_employee_dashboard(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_employee_dashboard(pg, current_user)

@api_router.get("/employee/tasks", response_model=List[TaskResponse])
async def get_employee_tasks(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.list_tasks_for_employee(pg, current_user)

@api_router.put("/employee/tasks/{task_id}/status")
async def update_task_status(task_id: str, status_data: dict, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.update_task_status(pg, current_user, current_user["company_id"], task_id, status_data)

@api_router.post("/employee/tasks/{task_id}/receive")
async def receive_critical_task(task_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Critical Task Alert 'Receive Task' action - acknowledges the
    full-screen alert without starting work yet. Dedicated endpoint
    (rather than the generic status one) because it's specific to the
    critical-task workflow and has its own validation/notification."""
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.receive_critical_task(pg, current_user, current_user["company_id"], task_id)

@api_router.post("/employee/tasks/{task_id}/proof")
async def upload_task_proof(task_id: str, proof: TaskProofUpload, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.upload_task_proof(pg, current_user["id"], task_id, proof)

@api_router.post("/employee/tasks/{task_id}/start")
async def start_task(task_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.start_task(pg, current_user["id"], task_id)

@api_router.post("/employee/tasks/{task_id}/complete")
async def complete_task(task_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.complete_task(pg, current_user["id"], task_id)

@api_router.post("/employee/attendance/check-in")
async def check_in(data: AttendanceCheckIn, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await attendance_service.check_in(pg, current_user, data)

@api_router.post("/employee/attendance/check-out")
async def check_out(data: AttendanceCheckOut, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await attendance_service.check_out(pg, current_user, data)

@api_router.get("/employee/attendance/history", response_model=List[AttendanceResponse])
async def get_attendance_history(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.get_attendance_history(pg, current_user)

@api_router.get("/employee/performance")
async def get_employee_performance(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_employee_performance(pg, current_user)

@api_router.post("/employee/reports", response_model=ReportResponse)
async def create_report(report: ReportCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await reports_service.create_report(pg, current_user, report)

@api_router.put("/employee/profile")
async def update_profile(updates: dict, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    # NOTE: no role check here, same as the pre-migration implementation -
    # this is a pre-existing gap (any authenticated user, not just
    # employees, can call this), already flagged in the migration plan as
    # not-DB-related and intentionally left unchanged. See Module 3's
    # progress report.
    return await employees_service.update_own_profile(pg, current_user["id"], updates)

# ============ User Profile & Account Settings (any authenticated role) ============
# Deliberately under a role-neutral /profile prefix (unlike the legacy
# /employee/profile route above, which despite its name already works for
# every role) - self-service, current_user-scoped only, matching the same
# "no role check needed" shape since every user may only ever act on their
# own account here.

@api_router.post("/profile/photo", response_model=Dict[str, Any])
async def upload_own_photo(photo: PhotoUploadRequest, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Upload or Replace - same endpoint covers both, the previous photo is
    deleted from storage automatically when one exists."""
    return await auth_service.upload_own_photo(pg, current_user["id"], photo)

@api_router.get("/profile/photo", response_model=Dict[str, Any])
async def get_own_photo(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Preview - returns the photo inline as base64, same contract as
    every other attachment type in this app."""
    return await auth_service.get_own_photo(pg, current_user["id"])

@api_router.delete("/profile/photo")
async def delete_own_photo(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await auth_service.delete_own_photo(pg, current_user["id"])

@api_router.put("/profile/password")
@limiter.limit("10/minute")
async def change_own_password(request: Request, body: ChangePasswordRequest, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    # Without this, a stolen/leaked access token gives an attacker an
    # unlimited-attempt oracle on the account's current password (the
    # 400 "Current password is incorrect" response confirms/denies each
    # guess) for as long as that token stays valid.
    return await auth_service.change_password(pg, current_user["id"], body.current_password, body.new_password)

# ============ Common Routes ============

@api_router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.get_notifications(pg, current_user)

@api_router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_notification_count(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.get_unread_count(pg, current_user)

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.mark_notification_read(pg, current_user, notification_id)

@api_router.put("/notifications/read-all")
async def mark_all_notifications_read(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.mark_all_notifications_read(pg, current_user)

@api_router.get("/notifications/stream")
async def stream_notifications(token: str):
    """Server-Sent Events push channel - the primary real-time delivery
    path for the Notification Center (see services/realtime.py). Auth is a
    query param, not the usual Authorization header, because the browser
    EventSource API cannot set custom headers. Deliberately does NOT use
    the shared `get_db` request dependency: a StreamingResponse's body can
    outlive the dependency teardown that happens once this function
    returns, so the DB session used for the one-time auth check is opened
    and closed here explicitly, before entering the generator - nothing in
    the streaming loop below touches the database again."""
    async with SessionLocal() as session:
        current_user = await auth_service.get_current_user(session, token)
    user_id = current_user["id"]

    async def event_stream():
        queue = realtime_service.subscribe(user_id)
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            realtime_service.unsubscribe(user_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )

@api_router.get("/dashboard/layout", response_model=DashboardLayoutResponse)
async def get_dashboard_layout(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_dashboard_layout(pg, current_user)

@api_router.put("/dashboard/layout", response_model=DashboardLayoutResponse)
async def save_dashboard_layout(body: DashboardLayoutRequest, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await dashboard_service.save_dashboard_layout(pg, current_user, [w.model_dump() for w in body.layout])

@api_router.post("/devices", response_model=DeviceTokenResponse)
async def register_device(body: DeviceTokenRegister, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Register or refresh a push-notification device token for the
    authenticated user. Idempotent - calling this again with the same
    token (e.g. on every app foreground, or when FCM/APNs rotates the
    token) is exactly how a client keeps its registration alive; there is
    no separate "refresh" endpoint. `user_id` always comes from the
    session, never from the body - a token can only ever be registered
    against the account that presents it."""
    return await devices_service.register_device(pg, current_user, body)

@api_router.get("/devices", response_model=List[DeviceTokenResponse])
async def list_own_devices(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await devices_service.list_own_devices(pg, current_user)

@api_router.delete("/devices")
async def unregister_device(token: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Called on logout so a signed-out device stops receiving push for
    the account it just left. Scoped to the caller's own tokens - see
    services/devices.py."""
    return await devices_service.unregister_device(pg, current_user, token)

# ============ Announcements ============

@api_router.post("/owner/announcements", response_model=AnnouncementResponse)
async def create_announcement(body: AnnouncementCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await announcements_service.create_announcement(pg, current_user, body)

@api_router.get("/owner/announcements", response_model=List[AnnouncementResponse])
async def list_owner_announcements(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await announcements_service.list_announcements(pg, current_user)

@api_router.get("/employee/announcements", response_model=List[AnnouncementResponse])
async def list_employee_announcements(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    return await announcements_service.list_announcements(pg, current_user)

@api_router.get("/company/{company_id}/qr")
async def get_company_qr(company_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    # Previously unauthenticated. The QR now encodes the attendance token, so
    # it must not be publicly harvestable - only the company's own owner (or
    # the platform super admin) may fetch it.
    return await attendance_service.get_company_qr(pg, current_user, company_id)

# ============ Work Messaging Routes ============
# Shared between Company Owner and Employee (both are valid senders/
# recipients of internal work messages); Super Admin has no route in here at
# all, so "must not read company messages" holds by simple omission.

@api_router.post("/messages", response_model=MessageResponse)
async def create_message(data: MessageCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    result = await messages_service.create_message(pg, current_user, data)
    return MessageResponse(**result)

@api_router.patch("/messages/{message_id}", response_model=MessageResponse)
async def update_message(message_id: str, updates: MessageUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    result = await messages_service.update_message(pg, current_user, message_id, updates)
    return MessageResponse(**result)

@api_router.post("/messages/{message_id}/send", response_model=MessageResponse)
async def send_draft(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    result = await messages_service.send_draft(pg, current_user, message_id)
    return MessageResponse(**result)

@api_router.post("/messages/{message_id}/reply", response_model=MessageResponse)
async def reply_to_message(message_id: str, data: MessageReply, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    result = await messages_service.reply_to_message(pg, current_user, message_id, data)
    return MessageResponse(**result)

@api_router.post("/messages/{message_id}/forward", response_model=MessageResponse)
async def forward_message(message_id: str, data: MessageCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    result = await messages_service.forward_message(pg, current_user, message_id, data)
    return MessageResponse(**result)

@api_router.get("/messages/recipients")
async def get_message_recipients(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Company-scoped directory for the Work Messages recipient picker,
    accessible to BOTH owners and employees."""
    return await messages_service.get_message_recipients(pg, current_user)

@api_router.get("/messages/inbox")
async def get_inbox(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: int = 1, page_size: int = 20,
    subject: Optional[str] = None, priority: Optional[str] = None, tags: Optional[str] = None,
    status: Optional[str] = None, sender: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None, unread_only: bool = False,
):
    """One row per thread, never per reply. unread_only powers the sidebar
    badge count (?unread_only=true&page_size=1, reading just `total`) and
    deliberately drops the role="recipient" filter, since a new reply on a
    thread you SENT also flips your own (role="sender") row back to unread."""
    return await messages_service.get_inbox(
        pg, current_user, page=page, page_size=page_size, subject=subject, priority=priority, tags=tags,
        status=status, sender=sender, date_from=date_from, date_to=date_to, unread_only=unread_only,
    )

@api_router.get("/messages/sent")
async def get_sent(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: int = 1, page_size: int = 20,
    subject: Optional[str] = None, priority: Optional[str] = None, tags: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    return await messages_service.get_sent(
        pg, current_user, page=page, page_size=page_size, subject=subject, priority=priority, tags=tags,
        date_from=date_from, date_to=date_to,
    )

@api_router.get("/messages/drafts")
async def get_drafts(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db), page: int = 1, page_size: int = 20):
    return await messages_service.get_drafts(pg, current_user, page=page, page_size=page_size)

@api_router.get("/messages/starred")
async def get_starred(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db), page: int = 1, page_size: int = 20):
    return await messages_service.get_starred(pg, current_user, page=page, page_size=page_size)

@api_router.get("/messages/archived")
async def get_archived(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db), page: int = 1, page_size: int = 20):
    return await messages_service.get_archived(pg, current_user, page=page, page_size=page_size)

@api_router.get("/messages/{message_id}")
async def get_message_thread(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Read-only: does NOT mark as seen. The frontend calls POST .../open
    right after fetching - opening is a distinct, auditable action, not a
    side effect of a GET, matching this codebase's dedicated-action-endpoint
    convention for anything workflow-meaningful."""
    return await messages_service.get_message_thread(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/open")
async def open_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.open_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/accept")
async def accept_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.accept_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/complete")
async def complete_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.complete_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/archive")
async def archive_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.archive_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/unread")
async def mark_message_unread(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.mark_message_unread(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/star")
async def toggle_star_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.toggle_star_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/close")
async def close_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.close_message(pg, current_user, message_id)

@api_router.post("/owner/messages/{message_id}/pin")
async def toggle_pin_message(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.toggle_pin_message(pg, current_user, message_id)

@api_router.post("/messages/{message_id}/attachments")
async def upload_message_attachment(message_id: str, data: AttachmentUpload, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.upload_message_attachment(pg, current_user, message_id, data)

@api_router.get("/message-attachments/{attachment_id}")
async def get_message_attachment(attachment_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.get_message_attachment(pg, current_user, attachment_id)

@api_router.post("/messages/{message_id}/reminder")
async def create_reminder(message_id: str, data: ReminderCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.create_reminder(pg, current_user, message_id, data)

@api_router.get("/owner/messages/communication-center")
async def get_communication_center(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: int = 1, page_size: int = 20,
    reference_number: Optional[str] = None, subject: Optional[str] = None, sender: Optional[str] = None,
    recipient: Optional[str] = None, department: Optional[str] = None, tags: Optional[str] = None,
    priority: Optional[str] = None, status: Optional[str] = None, attachment_type: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    """Full company-wide visibility for the Owner only - every filter in
    Part 'Search & Filters' plus 'Owner Visibility'. Confidentiality is
    stored/displayed but does not restrict this view, matching the explicit
    'for future permission expansion' scope."""
    return await messages_service.get_communication_center(
        pg, current_user, page=page, page_size=page_size, reference_number=reference_number, subject=subject,
        sender=sender, recipient=recipient, department=department, tags=tags, priority=priority, status=status,
        attachment_type=attachment_type, date_from=date_from, date_to=date_to,
    )

@api_router.get("/owner/messages/{message_id}/timeline")
async def get_message_timeline(message_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await messages_service.get_message_timeline(pg, current_user, message_id)

# ============ Calendar Routes ============
# All business logic lives in services/calendar.py (Postgres-backed).

@api_router.post("/calendar/events")
async def create_calendar_event(data: CalendarEventCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.create_calendar_event(pg, current_user, data)

# Registered before /calendar/events/{event_id} - FastAPI matches routes in
# registration order, so this static path must come first or "search" would
# be swallowed by the {event_id} path parameter.
@api_router.get("/calendar/events/search")
async def search_calendar_events(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    page: int = 1, page_size: int = 20,
    reference_number: Optional[str] = None, title: Optional[str] = None, participant: Optional[str] = None,
    department: Optional[str] = None, category: Optional[str] = None, priority: Optional[str] = None,
    created_by: Optional[str] = None, status: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    """Search still respects the range-bounded philosophy: defaults to a
    120-day window around today when no explicit range is given, rather
    than ever scanning the full history."""
    return await calendar_service.search_calendar_events(
        pg, current_user, page=page, page_size=page_size, reference_number=reference_number, title=title,
        participant=participant, department=department, category=category, priority=priority,
        created_by=created_by, status=status, date_from=date_from, date_to=date_to,
    )

@api_router.get("/calendar/events/{event_id}")
async def get_calendar_event(event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    event = await calendar_service.get_accessible_calendar_event(pg, event_id, current_user)
    return await calendar_service.build_event_detail(pg, event, current_user)

@api_router.get("/calendar/events")
async def list_calendar_events(
    current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    view_start: str = None, view_end: str = None, page: int = 1, page_size: int = 200,
):
    """The one range query every calendar view (Month/Week/Day/Agenda) uses -
    never loads anything outside [view_start, view_end]. Occurrences of
    recurring events are expanded on the fly for this window only, so a
    yearly series created 5 years ago costs nothing extra to display this
    month."""
    return await calendar_service.list_calendar_events(pg, current_user, view_start, view_end, page, page_size)

@api_router.patch("/calendar/events/{event_id}")
async def update_calendar_event(
    event_id: str, updates: CalendarEventUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    scope: str = EventEditScope.ENTIRE_SERIES, occurrence_date: Optional[str] = None,
):
    return await calendar_service.update_calendar_event(pg, current_user, event_id, updates, scope, occurrence_date)

@api_router.post("/calendar/events/{event_id}/cancel")
async def cancel_calendar_event(
    event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db),
    scope: str = EventEditScope.ENTIRE_SERIES, occurrence_date: Optional[str] = None,
):
    """Soft cancel only - status flips to 'cancelled' (entire series) or a
    single occurrence gets an is_cancelled exception. No delete path exists
    anywhere for calendar_events; history is never removed. Cancellation is
    deliberately binary (This Event Only / Entire Series) - unlike editing,
    'this_and_future' is not a meaningful cancellation scope and is rejected
    explicitly rather than silently falling through to a full-series cancel."""
    return await calendar_service.cancel_calendar_event(pg, current_user, event_id, scope, occurrence_date)

@api_router.post("/calendar/events/{event_id}/participants/add")
async def add_calendar_participants(event_id: str, body: ParticipantIdsBody, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.add_calendar_participants(pg, current_user, event_id, body.participant_ids)

@api_router.post("/calendar/events/{event_id}/participants/remove")
async def remove_calendar_participants(event_id: str, body: ParticipantIdsBody, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.remove_calendar_participants(pg, current_user, event_id, body.participant_ids)

@api_router.post("/calendar/events/{event_id}/participants/{participant_id}/attendance")
async def mark_final_attendance(event_id: str, participant_id: str, data: FinalAttendanceUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Post-meeting record (Attended/Absent) - independent of the pre-meeting
    RSVP (Accepted/Declined/Tentative) tracked by attendance_status. Owner or
    the event's creator only, and only once the meeting has actually
    finished, per 'After a meeting finishes'."""
    return await calendar_service.mark_final_attendance(pg, current_user, event_id, participant_id, data.status)

@api_router.put("/calendar/events/{event_id}/notes")
async def update_meeting_notes(event_id: str, data: MeetingNotesUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Meeting Notes (Summary/Decisions/Action Items) live only on the event
    itself - never referenced from anywhere else, and this endpoint never
    calls notify_for_calendar_event. The activity log entry is reference-only
    (records that notes were updated, never the note content itself)."""
    return await calendar_service.update_meeting_notes(pg, current_user, event_id, data)

@api_router.post("/calendar/events/{event_id}/open-conversation")
async def open_conversation(event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Links a Calendar event to a Work Messages thread by calling directly
    into services.messages.create_message (in-process, same layer both
    modules now share) - reused exactly the way 'Convert to Task' already
    reuses POST /owner/tasks. Idempotent: once a thread exists for this
    event, every subsequent call returns that same thread instead of
    creating a new one each time."""
    return await calendar_service.open_conversation(pg, current_user, event_id)

@api_router.post("/calendar/events/check-conflicts")
async def check_conflicts_endpoint(data: ConflictCheckRequest, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Dry-run used by the compose UI before save - never mutates anything."""
    return await calendar_service.check_conflicts_endpoint(pg, current_user, data)

@api_router.post("/calendar/events/{event_id}/respond")
async def respond_to_calendar_event(event_id: str, data: EventResponseUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.respond_to_calendar_event(pg, current_user, event_id, data.status)

@api_router.post("/calendar/events/{event_id}/reminders")
async def create_calendar_reminder(event_id: str, data: EventReminderCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Owner may set a reminder on any company event (oversight role) even
    when not a participant; everyone else must be a participant or the
    creator - checked via get_accessible_calendar_event."""
    return await calendar_service.create_calendar_reminder(pg, current_user, event_id, data)

@api_router.post("/calendar/events/{event_id}/attachments")
async def upload_calendar_attachment(event_id: str, data: AttachmentUpload, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Same base64-by-reference upload contract as message_attachments -
    metadata-only Postgres row, bytes in MinIO."""
    return await calendar_service.upload_calendar_attachment(pg, current_user, event_id, data)

@api_router.get("/calendar-attachments/{attachment_id}")
async def get_calendar_attachment(attachment_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.get_calendar_attachment(pg, current_user, attachment_id)

@api_router.get("/owner/calendar/working-hours")
async def get_company_working_hours(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.get_company_working_hours(pg, current_user)

@api_router.put("/owner/calendar/working-hours")
async def update_company_working_hours(data: WorkingHoursUpdate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Owner-only write; stored as flattened columns on companies (same
    additive-settings convention as attendance_settings) so it stays
    configurable later without a schema redesign."""
    return await calendar_service.update_company_working_hours(pg, current_user, data)

@api_router.get("/owner/calendar/monitor")
async def calendar_owner_monitor(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db), page: int = 1, page_size: int = 20):
    """Full company-wide visibility, mirroring Communication Center's role -
    every event regardless of visibility level, with per-participant
    response detail for oversight."""
    return await calendar_service.calendar_owner_monitor(pg, current_user, page, page_size)

@api_router.get("/owner/calendar/events/{event_id}/activity")
async def get_calendar_event_activity(event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.get_calendar_event_activity(pg, current_user, event_id)

# ============ Company Holiday Management ============
# Holidays are calendar_events with category=company_holiday - not a
# parallel collection/API. This is a thin, holiday-specific layer over the
# existing event machinery (creation, recurrence, occurrence expansion, the
# activity log) plus the is_active enable/disable toggle.

@api_router.get("/calendar/holidays")
async def list_company_holidays(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Flat list of holiday base records (not occurrence-expanded), for the
    Owner's Company Holidays management page - distinct from GET
    /calendar/events, which is bounded to a view range and expands
    recurrence for calendar rendering."""
    return await calendar_service.list_company_holidays(pg, current_user)

@api_router.post("/calendar/events/{event_id}/deactivate")
async def deactivate_company_holiday(event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.deactivate_company_holiday(pg, current_user, event_id)

@api_router.post("/calendar/events/{event_id}/reactivate")
async def reactivate_company_holiday(event_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await calendar_service.reactivate_company_holiday(pg, current_user, event_id)

@api_router.post("/calendar/holidays/weekly-pattern")
async def create_weekly_holiday_pattern(data: WeeklyHolidayPatternCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Creates one permanent (never-ending) weekly-recurring company_holiday
    event per selected weekday - e.g. Friday-only, or Friday+Saturday. Reuses
    create_calendar_event directly (in-process function call, same pattern as
    /open-conversation calling into Work Messages) instead of duplicating its
    reference-number/participant/notification/activity-log logic."""
    return await calendar_service.create_weekly_holiday_pattern(pg, current_user, data)

# ============ Seed Data (dev/test bootstrap only - disabled in production) ============

@api_router.post("/seed")
async def seed_data(pg: AsyncSession = Depends(get_db)):
    # Intentionally unauthenticated (bootstraps the very first super_admin
    # account into an empty database, before any credentials exist to log
    # in with) - the automated test suite's session-scoped autouse fixture
    # also depends on calling this with no auth (see tests/conftest.py).
    # That combination is only safe in dev/test, where the seeded
    # credentials below are throwaway - a real deployment must 404 here
    # rather than let anyone create a super_admin with a known password.
    # See services/seed.py for what it creates.
    if ENVIRONMENT == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    await seed_service.seed_identity_data(pg)
    return {"message": "Seed data created successfully", "admin_email": "admin@jaz.com", "admin_password": "admin123", "owner_email": "owner@demo.com", "owner_password": "owner123", "employee_email": "employee1@demo.com", "employee_password": "emp123"}


# ============ Stripe Payment Routes ============

class CheckoutRequest(BaseModel):
    plan_id: str
    origin_url: str

@api_router.post("/payments/checkout")
async def create_checkout(req: CheckoutRequest, request: Request, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    host_url = str(request.base_url).rstrip("/")
    return await subscriptions_service.create_checkout(pg, current_user, req.plan_id, req.origin_url, host_url)

@api_router.get("/payments/status/{session_id}")
async def check_payment_status(session_id: str, request: Request, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    host_url = str(request.base_url).rstrip("/")
    return await subscriptions_service.check_payment_status(pg, current_user, session_id, host_url)

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request, pg: AsyncSession = Depends(get_db)):
    host_url = str(request.base_url).rstrip("/")
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    return await subscriptions_service.stripe_webhook(pg, body, signature, host_url)

@api_router.get("/owner/subscription-plans")
async def get_public_subscription_plans(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    """Get available subscription plans for company owners"""
    return await subscriptions_service.get_public_subscription_plans(pg, current_user)

@api_router.get("/owner/subscription")
async def get_owner_subscription(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await subscriptions_service.get_owner_subscription(pg, current_user)


# Include router
app.include_router(api_router)

_cors_origins_env = os.environ.get('CORS_ORIGINS')
if ENVIRONMENT == 'production' and not _cors_origins_env:
    raise RuntimeError(
        "CORS_ORIGINS must be explicitly set in production - refusing to "
        "default to '*' (all origins) with credentials allowed."
    )

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins_env.split(',') if _cors_origins_env else ['*'],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    await engine.dispose()