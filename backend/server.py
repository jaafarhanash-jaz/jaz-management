from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from fastapi.responses import JSONResponse
from dateutil.relativedelta import relativedelta
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta, date
from passlib.context import CryptContext
from jose import JWTError, jwt
import qrcode
from io import BytesIO
import base64
from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionResponse, CheckoutStatusResponse, CheckoutSessionRequest
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
import repositories.companies as companies_repo
import repositories.users as users_repo
import services.admin as admin_service
import services.attendance as attendance_service
import services.auth as auth_service
import services.calendar as calendar_service
import services.departments as departments_service
import services.employees as employees_service
import services.heartbeat as heartbeat_service
import services.messages as messages_service
import services.notifications as notifications_service
import services.reports as reports_service
import services.seed as seed_service
import services.tasks as tasks_service
from services.admin import parse_uuid

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
# serverSelectionTimeoutMS=5000: when MongoDB is unreachable, fail in ~5s
# instead of the driver default of 30s. A 30s hang made every request (login
# included) block for half a minute before erroring - a fast failure surfaces
# "database unavailable" quickly instead of looking like the app is frozen.
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
db = client[os.environ['DB_NAME']]

# Security
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Create the main app
app = FastAPI()

# When MongoDB is unreachable, Motor raises ServerSelectionTimeoutError, which
# FastAPI would otherwise surface as a generic 500 "Internal Server Error" -
# indistinguishable from any other bug and, on the login page, mislabeled as
# "invalid password". This handler exposes the real cause: a clear 503 telling
# the client the database is unavailable, for EVERY endpoint (not just login).
@app.exception_handler(ServerSelectionTimeoutError)
async def _db_unavailable_handler(request: Request, exc: ServerSelectionTimeoutError):
    logging.error(f"MongoDB unavailable handling {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=503,
        content={"detail": "قاعدة البيانات غير متاحة حالياً. يرجى المحاولة لاحقاً. (Database temporarily unavailable)"},
    )

api_router = APIRouter(prefix="/api")

# ============ Models ============

class UserRole:
    SUPER_ADMIN = "super_admin"
    COMPANY_OWNER = "company_owner"
    EMPLOYEE = "employee"

class TaskStatus:
    NEW = "new"                # = "Pending" in the daily/urgent workflow
    RECEIVED = "received"       # Critical Task Alert: employee acknowledged before starting work
    SEEN = "seen"               # daily/urgent only
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"  # legacy tasks only, unchanged
    COMPLETED = "completed"
    REJECTED = "rejected"       # legacy tasks only, unchanged
    OVERDUE = "overdue"         # legacy tasks only, unchanged
    CANCELLED = "cancelled"     # daily/urgent only

class TaskPriority:
    CRITICAL = "critical"       # triggers the Critical Task Alert System - nothing else does
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class TaskCategory:
    URGENT = "urgent"
    DAILY = "daily"
    # None/absent = legacy standard task, fully backward compatible

class RecurrenceType:
    # Only DAILY is implemented now; the schema supports the others so
    # recurring schedules can be extended later without a migration.
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SELECTED_DAYS = "selected_days"

class AttendanceStatus:
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"

class SubscriptionStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"

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

class EventLocationType:
    OFFICE = "office"
    CLIENT_SITE = "client_site"
    ONLINE = "online"
    OTHER = "other"

class AttendanceResponseStatus:
    NO_RESPONSE = "no_response"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"

class FinalAttendanceStatus:
    # Post-meeting record of who actually showed up - independent of
    # AttendanceResponseStatus above, which is the pre-meeting RSVP.
    ATTENDED = "attended"
    ABSENT = "absent"

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
    user: Dict[str, Any]
    role: str

class UserCreate(BaseModel):
    email: EmailStr
    phone: str
    password: str
    name: str
    role: str
    company_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None

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
    company_id: str
    date: str
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    check_in_location: Optional[Dict[str, float]] = None
    check_out_location: Optional[Dict[str, float]] = None
    distance_from_company_meters: Optional[float] = None
    check_out_distance_meters: Optional[float] = None
    working_duration_minutes: Optional[float] = None
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
    title: str
    message: str
    read_status: bool
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

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

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

def classify_attachment_type(mime_type: str) -> str:
    mime_type = (mime_type or "").lower()
    if mime_type.startswith("image/"):
        return AttachmentType.IMAGE
    if mime_type == "application/pdf":
        return AttachmentType.PDF
    if "word" in mime_type or mime_type == "application/msword":
        return AttachmentType.WORD
    if "excel" in mime_type or "spreadsheet" in mime_type:
        return AttachmentType.EXCEL
    if "powerpoint" in mime_type or "presentation" in mime_type:
        return AttachmentType.POWERPOINT
    if "zip" in mime_type or "compressed" in mime_type:
        return AttachmentType.ZIP
    if mime_type.startswith("audio/"):
        return AttachmentType.AUDIO
    if mime_type.startswith("video/"):
        return AttachmentType.VIDEO
    return AttachmentType.OTHER

# Work Messaging helpers (resolve_recipients, write_message_activity,
# compute_delivery_progress, get_accessible_message, deliver_message,
# notify_reply_participants, enrich_messages) moved to services/messages.py
# (Postgres-backed) and repositories/{messages,message_recipients,
# message_attachments,counters}.py.

# ---- Calendar helpers ----
# create/update/cancel/notify/access-check/conflict-check moved to
# services/calendar.py (Postgres-backed). combine_event_datetime,
# get_working_hours, step_occurrence, expand_event_occurrences, and the
# holiday-check chain below stay here - the not-yet-migrated owner/employee
# dashboard and analytics routes still call into them directly.

def combine_event_datetime(date_str: str, time_str: Optional[str], all_day: bool, is_end: bool) -> datetime:
    if all_day:
        time_str = "23:59:59" if is_end else "00:00:00"
    elif not time_str:
        time_str = "23:59:59" if is_end else "00:00:00"
    if len(time_str) == 5:
        time_str += ":00"
    return datetime.fromisoformat(f"{date_str}T{time_str}+00:00")

async def get_working_hours(company_id: str) -> dict:
    company = await db.companies.find_one({"id": company_id}, {"_id": 0, "working_hours": 1})
    stored = (company or {}).get("working_hours") or {}
    return {
        "working_days": stored.get("working_days", [0, 1, 2, 3, 4]),
        "start_time": stored.get("start_time", "08:00"),
        "end_time": stored.get("end_time", "17:00"),
    }

def step_occurrence(dt: datetime, recurrence_type: str, interval: int) -> datetime:
    interval = max(interval, 1)
    if recurrence_type == RecurrenceType.DAILY or recurrence_type == RecurrenceType.CUSTOM:
        return dt + timedelta(days=interval)
    if recurrence_type == RecurrenceType.WEEKLY:
        return dt + timedelta(weeks=interval)
    if recurrence_type == RecurrenceType.MONTHLY:
        return dt + relativedelta(months=interval)
    if recurrence_type == RecurrenceType.YEARLY:
        return dt + relativedelta(years=interval)
    return dt

# Safety bound on recurrence expansion regardless of how the rule is shaped -
# never a runaway loop, and consistent with "never load an entire year":
# expansion itself never generates more than this many candidate occurrences.
MAX_OCCURRENCE_ITERATIONS = 3660

async def expand_event_occurrences(event: dict, range_start: datetime, range_end: datetime) -> List[dict]:
    """Occurrences are computed on read for the requested window only -
    nothing is pre-materialized per-occurrence in storage. Applies
    calendar_event_exceptions (skip if cancelled, merge if overridden) so
    'This Event Only' edits/cancellations don't require touching the series."""
    base_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
    base_end = combine_event_datetime(event["end_date"], event.get("end_time"), event["all_day"], True)
    duration = base_end - base_start

    exceptions = await db.calendar_event_exceptions.find({"event_id": event["id"]}, {"_id": 0}).to_list(1000)
    exceptions_by_date = {e["occurrence_date"]: e for e in exceptions}

    occurrences = []
    if event.get("recurrence_type", RecurrenceType.NONE) == RecurrenceType.NONE:
        if base_start <= range_end and base_end >= range_start:
            occurrences.append({"occurrence_date": event["start_date"], "start": base_start, "end": base_end})
    else:
        cursor = base_start
        end_type = event.get("recurrence_end_type", RecurrenceEndType.NEVER)
        end_value = event.get("recurrence_end_value")
        end_date_limit = datetime.fromisoformat(f"{end_value}T23:59:59+00:00") if end_type == RecurrenceEndType.ON_DATE and end_value else None
        max_count = int(end_value) if end_type == RecurrenceEndType.AFTER_COUNT and end_value else None
        count = 0
        iterations = 0
        while cursor <= range_end and iterations < MAX_OCCURRENCE_ITERATIONS:
            iterations += 1
            count += 1
            if end_date_limit and cursor > end_date_limit:
                break
            if max_count and count > max_count:
                break
            occurrence_date = cursor.date().isoformat()
            occurrence_end = cursor + duration
            if cursor <= range_end and occurrence_end >= range_start:
                occurrences.append({"occurrence_date": occurrence_date, "start": cursor, "end": occurrence_end})
            cursor = step_occurrence(cursor, event["recurrence_type"], event.get("recurrence_interval", 1))

    result = []
    for occ in occurrences:
        exception = exceptions_by_date.get(occ["occurrence_date"])
        if exception and exception.get("is_cancelled"):
            continue
        merged = {**event, **(exception.get("override_fields") or {} if exception else {})}
        merged["occurrence_date"] = occ["occurrence_date"]
        merged["occurrence_start"] = occ["start"].isoformat()
        merged["occurrence_end"] = occ["end"].isoformat()
        merged["has_exception"] = exception is not None
        result.append(merged)
    return result

async def is_company_holiday(company_id: str, date_str: str) -> Optional[dict]:
    """Returns {"id", "title"} if date_str (YYYY-MM-DD) falls on an active,
    non-cancelled company holiday - else None. Used by Attendance/Reports to
    exclude holiday dates from absence/late/penalty/overtime calculations.
    The Mongo pre-filter widens for recurring holidays (base end_date only
    reflects the first occurrence) - expand_event_occurrences does the real,
    precise per-date check downstream."""
    holidays = await db.calendar_events.find({
        "company_id": company_id, "category": EventCategory.COMPANY_HOLIDAY,
        "status": {"$ne": "cancelled"}, "is_active": True,
        "start_date": {"$lte": date_str},
        "$or": [{"end_date": {"$gte": date_str}}, {"recurrence_type": {"$ne": RecurrenceType.NONE}}],
    }, {"_id": 0}).to_list(200)
    if not holidays:
        return None
    day_start = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
    day_end = datetime.fromisoformat(f"{date_str}T23:59:59+00:00")
    for holiday in holidays:
        for occ in await expand_event_occurrences(holiday, day_start, day_end):
            if occ["occurrence_date"] == date_str:
                return {"id": holiday["id"], "title": holiday["title"]}
    return None

async def is_weekly_holiday(company_id: str, date_str: str) -> bool:
    """True if date_str's weekday is NOT one of the company's configured
    working_days (the Default Weekly Working Schedule section's Working Day
    / Weekly Holiday toggle) - reuses the existing working_hours settings
    rather than a new collection. 0=Sunday..6=Saturday, same convention as
    working_hours.working_days."""
    working_hours = await get_working_hours(company_id)
    weekday = (datetime.fromisoformat(f"{date_str}T00:00:00+00:00").date().weekday() + 1) % 7
    return weekday not in working_hours.get("working_days", [0, 1, 2, 3, 4])

async def get_day_off_info(company_id: str, date_str: str) -> Optional[dict]:
    """Combined non-working-day check: an active named company holiday
    (calendar_events) or a configured weekly holiday (working_hours), in
    that priority order since a named holiday is the more specific label.
    Returns {"type": "company_holiday"|"weekly_holiday", "title": str|None}
    or None on an ordinary working day."""
    holiday = await is_company_holiday(company_id, date_str)
    if holiday:
        return {"type": "company_holiday", "title": holiday["title"]}
    if await is_weekly_holiday(company_id, date_str):
        return {"type": "weekly_holiday", "title": None}
    return None

async def filter_out_holiday_attendance(company_id: str, records: List[dict]) -> List[dict]:
    """Drops attendance records whose date falls on a non-working day
    (named company holiday or configured weekly holiday), per the Company
    Holiday Management requirement to exclude those dates from
    absence/late/attendance-rate/penalty calculations entirely - a stray
    check-in on a day off should never count as a 'late' violation or skew
    the attendance-rate denominator. One get_day_off_info lookup per
    distinct date in the batch, not per record."""
    dates = {r["date"] for r in records if r.get("date")}
    if not dates:
        return records
    off_dates = {d for d in dates if await get_day_off_info(company_id, d)}
    if not off_dates:
        return records
    return [r for r in records if r.get("date") not in off_dates]

# deliver_due_calendar_reminders moved to services/notifications.py (Postgres-backed).

async def compute_calendar_dashboard_widgets(current_user: dict, is_owner: bool) -> dict:
    """Shared by both GET /owner/dashboard and GET /employee/dashboard -
    additive widget data only, computed from a small bounded window (today
    through +7 days), never a broader scan. Owner sees company-wide events
    (oversight model); Employee sees only events they're actually invited to."""
    company_id = current_user["company_id"]
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today_start + timedelta(days=7)

    if is_owner:
        candidates = await db.calendar_events.find({
            "company_id": company_id, "status": {"$ne": "cancelled"},
            "start_date": {"$lte": week_end.date().isoformat()}, "end_date": {"$gte": today_start.date().isoformat()},
        }, {"_id": 0}).to_list(500)
    else:
        my_rows = await db.calendar_event_participants.find(
            {"company_id": company_id, "participant_id": current_user["id"]}, {"_id": 0, "event_id": 1}
        ).to_list(2000)
        my_event_ids = list({r["event_id"] for r in my_rows})
        candidates = await db.calendar_events.find({
            "id": {"$in": my_event_ids}, "status": {"$ne": "cancelled"},
            "start_date": {"$lte": week_end.date().isoformat()}, "end_date": {"$gte": today_start.date().isoformat()},
        }, {"_id": 0}).to_list(500) if my_event_ids else []

    occurrences = []
    for event in candidates:
        for occ in await expand_event_occurrences(event, today_start, week_end):
            occurrences.append({**event, **occ, "start": datetime.fromisoformat(occ["occurrence_start"])})
    occurrences.sort(key=lambda o: o["start"])

    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = today_start + timedelta(days=2)
    today_events = [o for o in occurrences if today_start <= o["start"] < tomorrow_start]
    tomorrow_events = [o for o in occurrences if tomorrow_start <= o["start"] < tomorrow_end]
    future_occurrences = [o for o in occurrences if o["start"] >= now]
    next_event = future_occurrences[0] if future_occurrences else None
    meetings_starting_soon = [o for o in future_occurrences if o["category"] == EventCategory.MEETING and o["start"] <= now + timedelta(hours=1)]
    upcoming_meetings = [o for o in future_occurrences if o["category"] == EventCategory.MEETING][:5]
    upcoming_holidays = [o for o in occurrences if o["category"] == EventCategory.COMPANY_HOLIDAY][:5]
    high_priority = [o for o in future_occurrences if o["priority"] in (EventPriority.HIGH, EventPriority.CRITICAL)][:5]

    widgets = {
        "today_events": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"], "category": o["category"], "priority": o["priority"]} for o in today_events],
        "tomorrow_events": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"]} for o in tomorrow_events],
        "next_event": {"id": next_event["id"], "title": next_event["title"], "start": next_event["occurrence_start"],
                        "seconds_remaining": max(int((next_event["start"] - now).total_seconds()), 0)} if next_event else None,
        "upcoming_meetings": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"]} for o in upcoming_meetings],
        "upcoming_holidays": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"]} for o in upcoming_holidays],
    }
    if is_owner:
        waiting_ids = [o["id"] for o in future_occurrences]
        waiting_rows = await db.calendar_event_participants.find(
            {"event_id": {"$in": waiting_ids}, "attendance_status": AttendanceResponseStatus.NO_RESPONSE}, {"_id": 0, "event_id": 1}
        ).to_list(2000) if waiting_ids else []
        waiting_event_ids = {r["event_id"] for r in waiting_rows}
        widgets.update({
            "meetings_starting_soon": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"]} for o in meetings_starting_soon],
            "events_requiring_response": len(waiting_event_ids),
            "high_priority_events": [{"id": o["id"], "title": o["title"], "start": o["occurrence_start"], "priority": o["priority"]} for o in high_priority],
        })
    else:
        my_pending = await db.calendar_event_participants.count_documents({
            "company_id": company_id, "participant_id": current_user["id"],
            "attendance_status": AttendanceResponseStatus.NO_RESPONSE, "event_id": {"$in": [o["id"] for o in future_occurrences]},
        }) if future_occurrences else 0
        widgets["unanswered_invitations"] = my_pending
    return widgets

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    pg: AsyncSession = Depends(get_db),
):
    # Postgres-backed (Auth module, migrated). See services/auth.py.
    return await auth_service.get_current_user(pg, credentials.credentials)

# ============ Auth Routes ============

@api_router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, pg: AsyncSession = Depends(get_db)):
    # Postgres-backed (Auth module, migrated). See services/auth.py.
    result = await auth_service.login(pg, request.email_or_phone, request.password)
    return LoginResponse(**result)

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(**{k: v for k, v in current_user.items() if k != "password"})

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

# ============ Company Owner Routes ============

@api_router.get("/owner/dashboard")
async def get_owner_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    company_id = current_user["company_id"]
    today = datetime.now(timezone.utc).date().isoformat()
    today_holiday = await get_day_off_info(company_id, today)
    working_hours_for_dashboard = await get_working_hours(company_id)

    # Get statistics
    # Scope attendance stats to CURRENT employees only, deduped per employee.
    # Counting raw attendance rows (the previous approach) let orphaned rows
    # (from deleted employees) or duplicate same-day check-ins inflate the
    # counts, producing impossible values like absent_today < 0 or
    # attendance_percentage > 100%. Company sizes are bounded by the
    # subscription's max_employees, so loading the id set is cheap.
    employee_ids = [u["id"] for u in await db.users.find(
        {"company_id": company_id, "role": UserRole.EMPLOYEE}, {"_id": 0, "id": 1}
    ).to_list(100000)]
    total_employees = len(employee_ids)

    today_rows = await db.attendance.find(
        {"company_id": company_id, "date": today, "employee_id": {"$in": employee_ids}}, {"_id": 0}
    ).to_list(100000) if employee_ids else []
    # Dedupe per employee (a stray duplicate check-in must not double-count).
    status_by_employee = {}
    checked_out_ids = set()
    for row in today_rows:
        status_by_employee[row["employee_id"]] = row.get("status")
        if row.get("check_out_time"):
            checked_out_ids.add(row["employee_id"])

    present_today = sum(1 for s in status_by_employee.values() if s == AttendanceStatus.PRESENT)
    # On a company holiday nobody is expected to check in, so "late" and
    # "absent" are not violations - suppressed entirely rather than counted.
    late_today = 0 if today_holiday else sum(1 for s in status_by_employee.values() if s == AttendanceStatus.LATE)
    # Clamp: absent can never be negative even if data is inconsistent.
    absent_today = 0 if today_holiday else max(0, total_employees - present_today - late_today)
    checked_out_today = len(checked_out_ids)
    # Clamp: attendance rate is a percentage of the workforce, never > 100%.
    attendance_percentage = min(100.0, round((present_today + late_today) / total_employees * 100, 1)) if total_employees > 0 else 0

    open_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        # SEEN is a new status introduced by Smart Task Management, sitting
        # between NEW and IN_PROGRESS for daily/urgent tasks - included here
        # so this existing "still open" count stays accurate rather than
        # silently undercounting once a task is auto-marked seen.
        "status": {"$in": [TaskStatus.NEW, TaskStatus.SEEN, TaskStatus.IN_PROGRESS]}
    })
    completed_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "status": TaskStatus.COMPLETED
    })
    overdue_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "status": TaskStatus.OVERDUE
    })

    active_daily_tasks = await db.daily_tasks.count_documents({"company_id": company_id, "is_active": True})
    pending_urgent_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "task_category": TaskCategory.URGENT,
        "status": {"$in": [TaskStatus.NEW, TaskStatus.SEEN, TaskStatus.IN_PROGRESS]}
    })
    completed_urgent_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "task_category": TaskCategory.URGENT,
        "status": TaskStatus.COMPLETED
    })

    # Get recent reports
    recent_reports = await db.reports.find(
        {"company_id": company_id},
        {"_id": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    return {
        "total_employees": total_employees,
        "present_today": present_today,
        "late_today": late_today,
        "absent_today": absent_today,
        "checked_out_today": checked_out_today,
        "attendance_percentage": attendance_percentage,
        "open_tasks": open_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "active_daily_tasks": active_daily_tasks,
        "pending_urgent_tasks": pending_urgent_tasks,
        "completed_urgent_tasks": completed_urgent_tasks,
        "recent_reports": recent_reports,
        "calendar_widgets": await compute_calendar_dashboard_widgets(current_user, is_owner=True),
        "today_is_holiday": today_holiday is not None,
        "today_holiday_type": today_holiday["type"] if today_holiday else None,
        "today_holiday_title": today_holiday["title"] if today_holiday else None,
        "weekly_holiday_days": [d for d in range(7) if d not in working_hours_for_dashboard["working_days"]],
    }

def compute_star_rating(score: float) -> str:
    if score >= 90:
        return "★★★★★ Excellent"
    if score >= 75:
        return "★★★★ Good"
    if score >= 50:
        return "★★★ Average"
    return "★★ Needs Improvement"

def _duration_minutes(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[float]:
    if not start_iso or not end_iso:
        return None
    try:
        delta = (datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds() / 60
        return delta if delta >= 0 else None
    except ValueError:
        return None

@api_router.get("/owner/analytics")
async def get_owner_analytics(current_user: dict = Depends(get_current_user)):
    """Employee performance analytics, computed entirely from existing task
    and attendance data - no AI, no new data sources. Reuses the same
    completion_rate/attendance_rate calculation already used by
    /employee/performance, extended with more factors and aggregated across
    the whole company for the owner's view."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = current_user["company_id"]
    employees = await db.users.find(
        {"company_id": company_id, "role": UserRole.EMPLOYEE}, {"_id": 0, "password": 0}
    ).to_list(1000)
    all_tasks = await db.tasks.find({"company_id": company_id}, {"_id": 0}).to_list(5000)

    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def completed_since(task, start_date):
        completed_at = task.get("completed_at")
        if not completed_at:
            return False
        try:
            return datetime.fromisoformat(completed_at).date() >= start_date
        except ValueError:
            return False

    completed_tasks_all = [t for t in all_tasks if t.get("status") == TaskStatus.COMPLETED]
    pending_tasks_all = [t for t in all_tasks if t.get("status") in (TaskStatus.NEW, TaskStatus.SEEN, TaskStatus.IN_PROGRESS)]
    urgent_tasks_all = [t for t in all_tasks if t.get("task_category") == TaskCategory.URGENT]

    tasks_completed_today = sum(1 for t in completed_tasks_all if (t.get("completed_at") or "")[:10] == today.isoformat())
    tasks_completed_week = sum(1 for t in completed_tasks_all if completed_since(t, week_start))
    tasks_completed_month = sum(1 for t in completed_tasks_all if completed_since(t, month_start))

    completion_durations = [d for t in all_tasks if (d := _duration_minutes(t.get("started_at"), t.get("completed_at"))) is not None]
    avg_completion_time = round(sum(completion_durations) / len(completion_durations), 1) if completion_durations else None

    response_durations = [d for t in all_tasks if (d := _duration_minutes(t.get("created_at"), t.get("started_at"))) is not None]
    avg_response_time = round(sum(response_durations) / len(response_durations), 1) if response_durations else None

    # Fetched once and holiday-filtered here rather than via count_documents,
    # so both the company-wide and per-employee stats below share a single
    # exclusion pass - a stray check-in on a company holiday must not count
    # as a "late" violation or skew anyone's attendance rate.
    all_attendance = await db.attendance.find(
        {"company_id": company_id}, {"_id": 0, "employee_id": 1, "date": 1, "status": 1}
    ).to_list(20000)
    all_attendance = await filter_out_holiday_attendance(company_id, all_attendance)

    total_attendance_all = len(all_attendance)
    present_attendance_all = sum(1 for a in all_attendance if a.get("status") == AttendanceStatus.PRESENT)
    late_attendance_all = sum(1 for a in all_attendance if a.get("status") == AttendanceStatus.LATE)
    company_attendance_rate = round((present_attendance_all / total_attendance_all * 100), 1) if total_attendance_all > 0 else 0

    employee_stats = []
    for emp in employees:
        emp_id = emp["id"]
        emp_tasks = [t for t in all_tasks if t.get("assigned_to") == emp_id]
        emp_completed = [t for t in emp_tasks if t.get("status") == TaskStatus.COMPLETED]
        emp_urgent = [t for t in emp_tasks if t.get("task_category") == TaskCategory.URGENT]
        emp_urgent_completed = [t for t in emp_urgent if t.get("status") == TaskStatus.COMPLETED]

        emp_attendance = [a for a in all_attendance if a.get("employee_id") == emp_id]
        emp_total_days = len(emp_attendance)
        emp_present_days = sum(1 for a in emp_attendance if a.get("status") == AttendanceStatus.PRESENT)
        emp_late_days = sum(1 for a in emp_attendance if a.get("status") == AttendanceStatus.LATE)

        completion_rate = (len(emp_completed) / len(emp_tasks) * 100) if emp_tasks else 0
        # No urgent tasks assigned yet shouldn't penalize the score - treat as neutral.
        urgent_completion_rate = (len(emp_urgent_completed) / len(emp_urgent) * 100) if emp_urgent else 100
        attendance_rate = (emp_present_days / emp_total_days * 100) if emp_total_days > 0 else 0

        emp_durations = [d for t in emp_completed if (d := _duration_minutes(t.get("started_at"), t.get("completed_at"))) is not None]
        emp_avg_completion = round(sum(emp_durations) / len(emp_durations), 1) if emp_durations else None

        if emp_avg_completion is None:
            speed_score = 70  # neutral - not enough data yet
        elif emp_avg_completion <= 60:
            speed_score = 100
        elif emp_avg_completion <= 180:
            speed_score = 75
        elif emp_avg_completion <= 480:
            speed_score = 50
        else:
            speed_score = 25

        late_penalty = min(emp_late_days * 2, 20)
        performance_score = (
            completion_rate * 0.35
            + urgent_completion_rate * 0.20
            + attendance_rate * 0.25
            + speed_score * 0.20
        ) - late_penalty
        performance_score = max(0, min(100, round(performance_score, 1)))

        employee_stats.append({
            "employee_id": emp_id,
            "name": emp["name"],
            "total_tasks": len(emp_tasks),
            "completed_tasks": len(emp_completed),
            "completion_rate": round(completion_rate, 1),
            "urgent_tasks": len(emp_urgent),
            "urgent_completed": len(emp_urgent_completed),
            "avg_completion_time_minutes": emp_avg_completion,
            "attendance_rate": round(attendance_rate, 1),
            "late_count": emp_late_days,
            "performance_score": performance_score,
            "rating": compute_star_rating(performance_score)
        })

    employee_stats.sort(key=lambda e: e["performance_score"], reverse=True)
    for idx, e in enumerate(employee_stats):
        e["rank"] = idx + 1

    # Insights - deterministic rules over the stats above, no AI/ML involved.
    insights = []
    for e in employee_stats:
        if e["urgent_tasks"] >= 2 and e["urgent_completed"] == e["urgent_tasks"]:
            insights.append(f"{e['name']} consistently completes urgent tasks quickly.")
        if e["late_count"] >= 3:
            insights.append(f"{e['name']} has frequent late arrivals.")
    if employee_stats:
        top = max(employee_stats, key=lambda e: e["completion_rate"])
        if top["total_tasks"] > 0:
            insights.append(f"{top['name']} has the highest completion rate this month.")

    tasks_completed_over_time = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        count = sum(1 for t in completed_tasks_all if (t.get("completed_at") or "")[:10] == day)
        tasks_completed_over_time.append({"date": day, "count": count})

    status_counts = {}
    for t in all_tasks:
        status_counts[t.get("status", "unknown")] = status_counts.get(t.get("status", "unknown"), 0) + 1
    task_distribution = [{"status": s, "count": c} for s, c in status_counts.items()]

    attendance_trend = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        day_total = await db.attendance.count_documents({"company_id": company_id, "date": day})
        day_present = await db.attendance.count_documents({"company_id": company_id, "date": day, "status": AttendanceStatus.PRESENT})
        rate = round((day_present / day_total * 100), 1) if day_total > 0 else 0
        attendance_trend.append({"date": day, "rate": rate})

    return {
        "total_tasks": len(all_tasks),
        "completed_tasks": len(completed_tasks_all),
        "pending_tasks": len(pending_tasks_all),
        "urgent_tasks": len(urgent_tasks_all),
        "avg_completion_time_minutes": avg_completion_time,
        "avg_response_time_minutes": avg_response_time,
        "tasks_completed_today": tasks_completed_today,
        "tasks_completed_week": tasks_completed_week,
        "tasks_completed_month": tasks_completed_month,
        "attendance_rate": company_attendance_rate,
        "late_attendance_count": late_attendance_all,
        "employee_ranking": employee_stats,
        "insights": insights,
        "charts": {
            "tasks_completed_over_time": tasks_completed_over_time,
            "task_distribution": task_distribution,
            "employee_performance_ranking": [{"name": e["name"], "score": e["performance_score"]} for e in employee_stats],
            "attendance_trend": attendance_trend
        }
    }

@api_router.get("/owner/employees", response_model=List[UserResponse])
async def get_employees(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.list_employees(pg, current_user["company_id"])

@api_router.post("/owner/employees", response_model=UserResponse)
async def create_employee(employee: UserCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await employees_service.create_employee(pg, current_user["company_id"], employee)

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

@api_router.get("/owner/tasks", response_model=List[TaskResponse])
async def get_tasks(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await tasks_service.list_tasks_for_owner(pg, current_user["company_id"])

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
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await attendance_service.list_attendance_for_owner(
        pg, current_user["company_id"], date=date, date_from=date_from, date_to=date_to,
        employee_id=employee_id, department=department, status=status, search=search,
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

@api_router.get("/owner/reports", response_model=List[ReportResponse])
async def get_reports(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await reports_service.list_reports_for_owner(pg, current_user["company_id"])

@api_router.get("/owner/departments", response_model=List[DepartmentResponse])
async def get_departments(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await departments_service.list_departments(pg, current_user["company_id"])

@api_router.post("/owner/departments", response_model=DepartmentResponse)
async def create_department(department: DepartmentCreate, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    return await departments_service.create_department(pg, current_user["company_id"], department)

# ============ Employee Routes ============

@api_router.get("/employee/dashboard")
async def get_employee_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employee_id = current_user["id"]
    today = datetime.now(timezone.utc).date().isoformat()
    
    # Get statistics
    total_tasks = await db.tasks.count_documents({"assigned_to": employee_id})
    completed_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.COMPLETED
    })
    pending_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": {"$in": [TaskStatus.NEW, TaskStatus.IN_PROGRESS]}
    })
    
    # Calculate completion rate
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    
    # Get today's attendance
    attendance = await db.attendance.find_one(
        {"employee_id": employee_id, "date": today},
        {"_id": 0}
    )
    
    # Get latest notification
    latest_notification = await db.notifications.find_one(
        {"user_id": employee_id},
        {"_id": 0},
        sort=[("created_at", -1)]
    )

    today_holiday = await get_day_off_info(current_user["company_id"], today)

    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "pending_tasks": pending_tasks,
        "completion_rate": round(completion_rate, 1),
        "checked_in": attendance is not None and attendance.get("check_in_time") is not None,
        "checked_out": attendance is not None and attendance.get("check_out_time") is not None,
        "latest_notification": latest_notification,
        "calendar_widgets": await compute_calendar_dashboard_widgets(current_user, is_owner=False),
        "today_is_holiday": today_holiday is not None,
        "today_holiday_type": today_holiday["type"] if today_holiday else None,
        "today_holiday_title": today_holiday["title"] if today_holiday else None,
    }

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
async def upload_task_proof(task_id: str, proof: dict, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
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
async def get_employee_performance(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employee_id = current_user["id"]
    
    # Get task statistics
    total_tasks = await db.tasks.count_documents({"assigned_to": employee_id})
    completed_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.COMPLETED
    })
    overdue_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.OVERDUE
    })
    
    # Get attendance statistics - holiday dates excluded entirely (see
    # filter_out_holiday_attendance) so a stray check-in on a company
    # holiday never counts as a late/absent violation or skews the rate.
    employee_attendance = await db.attendance.find(
        {"employee_id": employee_id}, {"_id": 0, "date": 1, "status": 1}
    ).to_list(20000)
    employee_attendance = await filter_out_holiday_attendance(current_user["company_id"], employee_attendance)
    total_days = len(employee_attendance)
    present_days = sum(1 for a in employee_attendance if a.get("status") == AttendanceStatus.PRESENT)
    late_days = sum(1 for a in employee_attendance if a.get("status") == AttendanceStatus.LATE)
    absent_days = sum(1 for a in employee_attendance if a.get("status") == AttendanceStatus.ABSENT)

    # Calculate metrics
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    attendance_rate = (present_days / total_days * 100) if total_days > 0 else 0
    
    # Simple performance rating
    performance_score = (completion_rate * 0.6 + attendance_rate * 0.4)
    
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "completion_rate": round(completion_rate, 1),
        "total_days": total_days,
        "present_days": present_days,
        "late_days": late_days,
        "absent_days": absent_days,
        "attendance_rate": round(attendance_rate, 1),
        "performance_score": round(performance_score, 1)
    }

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

# ============ Common Routes ============

@api_router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.get_notifications(pg, current_user)

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user), pg: AsyncSession = Depends(get_db)):
    return await notifications_service.mark_notification_read(pg, current_user, notification_id)

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

# ============ Seed Data (For Testing) ============

@api_router.post("/seed")
async def seed_data(pg: AsyncSession = Depends(get_db)):
    # Postgres-backed: super admin, demo plan, demo company+owner, demo
    # employees. Idempotent via business keys (email / plan name) - see
    # services/seed.py. The old Mongo demo departments/tasks seeding was
    # removed per the cleanup-first migration policy; it gets reintroduced
    # in seed_service as those modules migrate to Postgres.
    await seed_service.seed_identity_data(pg)
    return {"message": "Seed data created successfully", "admin_email": "admin@jaz.com", "admin_password": "admin123", "owner_email": "owner@demo.com", "owner_password": "owner123", "employee_email": "employee1@demo.com", "employee_password": "emp123"}


# ============ Stripe Payment Routes ============

class CheckoutRequest(BaseModel):
    plan_id: str
    origin_url: str

@api_router.post("/payments/checkout")
async def create_checkout(req: CheckoutRequest, request: Request, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Only company owners can subscribe")
    
    # Fetch plan from DB (server-side price only, no frontend manipulation)
    plan = await db.subscription_plans.find_one({"id": req.plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    amount = float(plan["price"])
    currency = "usd"
    
    stripe_key = os.environ.get('STRIPE_API_KEY')
    if not stripe_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=stripe_key, webhook_url=webhook_url)
    
    success_url = f"{req.origin_url}/company-owner/subscription?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{req.origin_url}/company-owner/subscription"
    
    metadata = {
        "user_id": current_user["id"],
        "company_id": current_user["company_id"],
        "plan_id": req.plan_id
    }
    
    checkout_req = CheckoutSessionRequest(
        amount=amount,
        currency=currency,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata
    )
    
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)
    
    # Create payment transaction record
    tx_doc = {
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "user_id": current_user["id"],
        "company_id": current_user["company_id"],
        "plan_id": req.plan_id,
        "amount": amount,
        "currency": currency,
        "metadata": metadata,
        "payment_status": "initiated",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.payment_transactions.insert_one(tx_doc)
    
    return {"url": session.url, "session_id": session.session_id}


@api_router.get("/payments/status/{session_id}")
async def check_payment_status(session_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    stripe_key = os.environ.get('STRIPE_API_KEY')
    if not stripe_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=stripe_key, webhook_url=webhook_url)
    
    status_resp: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)
    
    # Update payment transaction (only if not already processed)
    tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if tx and tx["payment_status"] != "paid" and status_resp.payment_status == "paid":
        await db.payment_transactions.update_one(
            {"session_id": session_id},
            {"$set": {
                "payment_status": status_resp.payment_status,
                "status": status_resp.status,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        # Activate subscription for company
        plan = await db.subscription_plans.find_one({"id": tx["plan_id"]}, {"_id": 0})
        if plan:
            end_date = datetime.now(timezone.utc) + timedelta(days=30 * plan["duration_months"])
            await db.companies.update_one(
                {"id": tx["company_id"]},
                {"$set": {
                    "subscription_status": "active",
                    "subscription_plan_id": tx["plan_id"],
                    "subscription_end_date": end_date.isoformat()
                }}
            )
    
    return {
        "status": status_resp.status,
        "payment_status": status_resp.payment_status,
        "amount_total": status_resp.amount_total,
        "currency": status_resp.currency
    }


@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    stripe_key = os.environ.get('STRIPE_API_KEY')
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=stripe_key, webhook_url=webhook_url)
    
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    
    webhook_response = await stripe_checkout.handle_webhook(body, signature)
    
    # Update payment transaction based on webhook
    if webhook_response.session_id:
        tx = await db.payment_transactions.find_one({"session_id": webhook_response.session_id}, {"_id": 0})
        if tx and tx["payment_status"] != "paid" and webhook_response.payment_status == "paid":
            await db.payment_transactions.update_one(
                {"session_id": webhook_response.session_id},
                {"$set": {
                    "payment_status": webhook_response.payment_status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            
            # Activate subscription
            plan = await db.subscription_plans.find_one({"id": tx["plan_id"]}, {"_id": 0})
            if plan:
                end_date = datetime.now(timezone.utc) + timedelta(days=30 * plan["duration_months"])
                await db.companies.update_one(
                    {"id": tx["company_id"]},
                    {"$set": {
                        "subscription_status": "active",
                        "subscription_plan_id": tx["plan_id"],
                        "subscription_end_date": end_date.isoformat()
                    }}
                )
    
    return {"status": "received"}


@api_router.get("/owner/subscription-plans")
async def get_public_subscription_plans(current_user: dict = Depends(get_current_user)):
    """Get available subscription plans for company owners"""
    plans = await db.subscription_plans.find({"is_active": True}, {"_id": 0}).to_list(100)
    return plans


@api_router.get("/owner/subscription")
async def get_owner_subscription(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    plan = None
    if company.get("subscription_plan_id"):
        plan = await db.subscription_plans.find_one({"id": company["subscription_plan_id"]}, {"_id": 0})
    
    return {
        "subscription_status": company.get("subscription_status"),
        "subscription_end_date": company.get("subscription_end_date"),
        "current_plan": plan
    }


# Include router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
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
    client.close()