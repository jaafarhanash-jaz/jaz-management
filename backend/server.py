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
import math
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
import repositories.users as users_repo
import services.auth as auth_service
import services.seed as seed_service

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

class AttachmentType:
    IMAGE = "image"
    PDF = "pdf"
    WORD = "word"
    EXCEL = "excel"
    POWERPOINT = "powerpoint"
    ZIP = "zip"
    AUDIO = "audio"
    VIDEO = "video"
    OTHER = "other"

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB decoded size cap

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

CALENDAR_REMINDER_PRESETS = {
    "5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1), "6h": timedelta(hours=6), "1d": timedelta(days=1),
    "3d": timedelta(days=3), "1w": timedelta(weeks=1),
}
CALENDAR_CONFLICT_LOOKAHEAD_DAYS = 90  # bounded recurrence expansion for conflict checks

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

def is_past_date(date_str: Optional[str]) -> bool:
    """Compares only the date portion so both plain 'YYYY-MM-DD' values and
    legacy full ISO datetime values (from the Stripe flow) compare correctly."""
    if not date_str:
        return False
    return date_str[:10] < datetime.now(timezone.utc).date().isoformat()

# Plan fields that identify/manage the plan itself, not its configuration -
# never copied onto a company.
PLAN_IDENTITY_FIELDS = {"id", "name", "is_active"}
# Plan field names that map to a differently-named company field, kept for
# backward compatibility with fields already in use elsewhere in the app.
PLAN_FIELD_RENAMES = {"price": "subscription_price"}

def plan_config_for_company(plan: dict) -> dict:
    """Maps a subscription plan's full configuration onto the company fields
    that mirror it. A company must inherit everything about its assigned plan
    - not just price and max_employees - whenever the plan is assigned,
    changed, or renewed. Any plan property added in the future is picked up
    automatically here with no further changes needed at the call sites."""
    config = {}
    for key, value in plan.items():
        if key in PLAN_IDENTITY_FIELDS:
            continue
        if key == "max_employees":
            config["max_employees"] = value
        else:
            config[PLAN_FIELD_RENAMES.get(key, f"subscription_{key}")] = value
    return config

async def resolve_subscription_status(company: dict) -> str:
    """Self-heal on read: if an active subscription's end date has passed,
    persist it as expired. No cron/scheduler - this runs inline wherever a
    company is accessed."""
    status_value = company.get("subscription_status")
    if status_value == SubscriptionStatus.ACTIVE and is_past_date(company.get("subscription_end_date")):
        await db.companies.update_one(
            {"id": company["id"]},
            {"$set": {"subscription_status": SubscriptionStatus.EXPIRED}}
        )
        return SubscriptionStatus.EXPIRED
    return status_value

# A user counts as online if their last heartbeat arrived within this window.
# No cron/scheduler - "offline" is computed lazily wherever presence is read,
# the same self-heal-on-read approach used for subscription expiry above.
PRESENCE_TIMEOUT_SECONDS = 60

def is_user_online(last_seen_at: Optional[str]) -> bool:
    if not last_seen_at:
        return False
    try:
        last_seen = datetime.fromisoformat(last_seen_at)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last_seen).total_seconds() <= PRESENCE_TIMEOUT_SECONDS

async def get_company_presence(company: dict) -> dict:
    """A company is online whenever its owner OR at least one employee has
    sent a heartbeat within the last PRESENCE_TIMEOUT_SECONDS."""
    owner = await db.users.find_one({"id": company.get("owner_id")}, {"_id": 0, "last_seen_at": 1})
    owner_last_seen = owner.get("last_seen_at") if owner else None
    owner_online = is_user_online(owner_last_seen)

    employees = await db.users.find(
        {"company_id": company["id"], "role": UserRole.EMPLOYEE},
        {"_id": 0, "last_seen_at": 1}
    ).to_list(1000)
    employees_online = sum(1 for e in employees if is_user_online(e.get("last_seen_at")))

    all_last_seens = [ls for ls in ([owner_last_seen] + [e.get("last_seen_at") for e in employees]) if ls]

    return {
        "owner_online": owner_online,
        "employees_online": employees_online,
        "company_online": owner_online or employees_online > 0,
        "last_seen": max(all_last_seens) if all_last_seens else None
    }

# ---- Smart QR Attendance helpers ----

# Per-company overrides live in companies.attendance_settings, stored as a
# nested object (not flat company columns) so a future `branches` collection
# can carry this same shape per-branch without a schema redesign.
DEFAULT_ATTENDANCE_RADIUS_METERS = 50.0
# Above this implied speed between two consecutive attendance points the
# movement is physically impossible for a commuting employee. Generous enough
# that normal GPS jitter or a fast highway drive never triggers it.
IMPOSSIBLE_TRAVEL_SPEED_KMH = 200.0
# Real GPS fixes essentially never report sub-1-meter accuracy; spoofing apps
# commonly report 0 or 1. Only ever used combined with the repeated-identical-
# coordinates signal - never rejects on its own.
SUSPICIOUS_GPS_ACCURACY_METERS = 1.0
REPEATED_COORDS_MIN_DAYS = 3

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((rlat2 - rlat1) / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))

def attendance_settings_for(company: dict) -> dict:
    """Attendance settings with backward-compatible defaults: QR attendance
    stays enabled and a missing location means radius validation is skipped,
    so companies created before this feature keep working unchanged until
    their owner configures a location."""
    stored = company.get("attendance_settings") or {}
    return {
        "latitude": stored.get("latitude"),
        "longitude": stored.get("longitude"),
        "radius_meters": stored.get("radius_meters", DEFAULT_ATTENDANCE_RADIUS_METERS),
        "qr_enabled": stored.get("qr_enabled", True),
        "updated_at": stored.get("updated_at"),
        "updated_by": stored.get("updated_by"),
        "updated_by_name": stored.get("updated_by_name"),
    }

async def ensure_qr_token(company: dict) -> str:
    """Self-heal on read (same pattern as resolve_subscription_status): a
    company whose printed QR predates this feature - when the QR embedded the
    raw company id - gets an opaque random token and a regenerated QR image
    the first time its QR is needed. The QR encodes only this token, never
    any company data."""
    token = company.get("qr_token")
    if not token:
        token = uuid.uuid4().hex
        company["qr_token"] = token
        company["qr_code"] = generate_qr_code(token)
        await db.companies.update_one(
            {"id": company["id"]},
            {"$set": {"qr_token": token, "qr_code": company["qr_code"]}}
        )
    return token

async def detect_fake_gps(employee_id: str, latitude: float, longitude: float, accuracy: Optional[float]) -> Optional[str]:
    """Best-effort server-side spoofing heuristics for a browser client (the
    web platform exposes no native mock-location flag). Only high-confidence
    combinations reject, so normal GPS inaccuracy never blocks a real
    employee: an impossible travel speed against the employee's own previous
    attendance point is decisive alone; suspiciously perfect accuracy and
    pin-identical coordinates across several days must BOTH be present."""
    now = datetime.now(timezone.utc)
    records = await db.attendance.find(
        {"employee_id": employee_id}, {"_id": 0}
    ).sort("date", -1).limit(10).to_list(10)

    events = []
    for record in records:
        for time_field, loc_field in (("check_in_time", "check_in_location"), ("check_out_time", "check_out_location")):
            loc = record.get(loc_field)
            if record.get(time_field) and loc and loc.get("latitude") is not None:
                events.append((record[time_field], loc))
    if events:
        last_time_iso, last_loc = max(events, key=lambda e: e[0])
        elapsed_hours = None
        try:
            elapsed_hours = (now - datetime.fromisoformat(last_time_iso)).total_seconds() / 3600
        except (ValueError, TypeError):
            pass
        if elapsed_hours and elapsed_hours > 0:
            distance_km = haversine_meters(latitude, longitude, last_loc["latitude"], last_loc["longitude"]) / 1000
            # Sub-500m jumps are ignored entirely: GPS jitter can never look
            # like teleporting at that scale, whatever the time delta.
            if distance_km > 0.5 and (distance_km / elapsed_hours) > IMPOSSIBLE_TRAVEL_SPEED_KMH:
                return "تم رفض تسجيل الحضور: تم اكتشاف موقع GPS غير موثوق (سرعة انتقال غير ممكنة)."

    if accuracy is not None and accuracy <= SUSPICIOUS_GPS_ACCURACY_METERS:
        identical_days = {
            r["date"] for r in records
            if (r.get("check_in_location") or {}).get("latitude") == latitude
            and (r.get("check_in_location") or {}).get("longitude") == longitude
        }
        if len(identical_days) >= REPEATED_COORDS_MIN_DAYS:
            return "تم رفض تسجيل الحضور: تم اكتشاف موقع GPS غير موثوق (إحداثيات متطابقة بدقة مشبوهة)."

    return None

async def validate_qr_attendance(current_user: dict, qr_code: Optional[str], latitude: Optional[float], longitude: Optional[float], accuracy: Optional[float]):
    """Shared server-side gate for check-in AND check-out. Returns
    (company, settings, distance_from_company_meters); raises a clear,
    user-legible 400 on any failure. A valid QR alone is never sufficient -
    GPS is always required, and the radius is enforced as soon as a company
    location exists (unless the owner disabled QR attendance entirely)."""
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    settings = attendance_settings_for(company)
    if not settings["qr_enabled"]:
        raise HTTPException(status_code=400, detail="تسجيل الحضور عبر QR معطل حالياً من قبل إدارة الشركة.")

    token = await ensure_qr_token(company)
    if qr_code != token:
        raise HTTPException(status_code=400, detail="رمز QR غير صالح. يرجى مسح رمز الشركة الصحيح.")

    if latitude is None or longitude is None:
        raise HTTPException(status_code=400, detail="خدمة الموقع (GPS) مطلوبة لتسجيل الحضور. يرجى السماح بالوصول إلى موقعك والمحاولة مجدداً.")

    rejection = await detect_fake_gps(current_user["id"], latitude, longitude, accuracy)
    if rejection:
        raise HTTPException(status_code=400, detail=rejection)

    distance = None
    if settings["latitude"] is not None and settings["longitude"] is not None:
        distance = round(haversine_meters(latitude, longitude, settings["latitude"], settings["longitude"]), 1)
        if distance > settings["radius_meters"]:
            raise HTTPException(
                status_code=400,
                detail=f"أنت خارج النطاق المسموح للشركة (المسافة الحالية {int(distance)} متر والمسموح {int(settings['radius_meters'])} متر)."
            )

    return company, settings, distance

# ---- Work Messaging helpers ----

async def next_reference_number(company_id: str) -> str:
    """Atomic per-company sequence (MSG-000001, MSG-000002...) via the
    standard Mongo find-and-increment pattern - avoids duplicate numbers
    under concurrent sends without needing a separate locking mechanism."""
    counter = await db.counters.find_one_and_update(
        {"id": f"message_seq:{company_id}"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"MSG-{counter['value']:06d}"

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

def validate_task_attachments(attachments: Optional[List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    """Same base64-inline validation as calendar/message attachments (reused
    classify_attachment_type/MAX_ATTACHMENT_BYTES, unmodified), but stored
    directly on the task document rather than a separate collection -
    matches the existing proof_files convention for tasks specifically."""
    if not attachments:
        return []
    validated = []
    for a in attachments:
        filename, mime_type, data = a.get("filename"), a.get("mime_type"), a.get("data")
        if not filename or not data:
            raise HTTPException(status_code=400, detail="Each attachment requires a filename and data")
        raw = data.split(",", 1)[-1] if data.startswith("data:") else data
        try:
            decoded_size = len(base64.b64decode(raw, validate=False))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid file data")
        if decoded_size > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=400, detail=f"File exceeds the {MAX_ATTACHMENT_BYTES // (1024*1024)}MB limit")
        validated.append({
            "filename": filename, "mime_type": mime_type,
            "attachment_type": classify_attachment_type(mime_type),
            "data": data, "size_bytes": decoded_size,
        })
    return validated

async def resolve_recipients(company_id: str, recipient_type: str, recipient_ids: List[str]) -> tuple:
    """Fan-out resolution at send time (same approach already used for
    urgent-task assignment): 'department' expands to whoever currently
    belongs to that department, captured as a snapshot - matches the
    department-snapshot convention already established on attendance
    records. Returns (list of real employee ids, department label or None)."""
    if recipient_type == MessageRecipientType.DEPARTMENT:
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="Department is required")
        department = recipient_ids[0]
        employees = await db.users.find(
            {"company_id": company_id, "role": UserRole.EMPLOYEE, "department": department},
            {"_id": 0, "id": 1}
        ).to_list(1000)
        if not employees:
            raise HTTPException(status_code=400, detail="No employees found in this department")
        return [e["id"] for e in employees], department

    if not recipient_ids:
        raise HTTPException(status_code=400, detail="At least one recipient is required")
    valid_count = await db.users.count_documents({
        "id": {"$in": recipient_ids}, "company_id": company_id
    })
    if valid_count != len(set(recipient_ids)):
        raise HTTPException(status_code=400, detail="One or more recipients do not belong to your company")
    return list(set(recipient_ids)), None

async def write_message_activity(company_id: str, message_id: str, actor: dict, verb: str, subject: str):
    """Event-only log - never stores message body, per the explicit
    'do not duplicate message content' requirement. Append-only: no
    update/delete route exists anywhere against this collection."""
    await db.message_activity_log.insert_one({
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "message_id": message_id,
        "actor_id": actor["id"],
        "actor_name": actor.get("name"),
        "verb": verb,
        "message_subject": subject,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

async def compute_delivery_progress(thread_id: str) -> Dict[str, Any]:
    """Computed on read from message_recipients - never stored redundantly,
    consistent with how absence/attendance-rate are already derived rather
    than cached elsewhere in this codebase. One row per (thread, participant)
    - the sender's own row is excluded so '12/20' reflects real recipients,
    not the sender counted as a phantom recipient of their own message."""
    recipients = await db.message_recipients.find(
        {"thread_id": thread_id, "role": "recipient"}, {"_id": 0, "status": 1}
    ).to_list(10000)
    total = len(recipients)
    reached = lambda *statuses: sum(1 for r in recipients if r["status"] in statuses)
    return {
        "total": total,
        "delivered": total,  # delivery is immediate/automatic on send
        "seen": reached(MessageRecipientStatus.SEEN, MessageRecipientStatus.ACCEPTED,
                         MessageRecipientStatus.COMPLETED, MessageRecipientStatus.ARCHIVED),
        "accepted": reached(MessageRecipientStatus.ACCEPTED, MessageRecipientStatus.COMPLETED,
                             MessageRecipientStatus.ARCHIVED),
        "completed": reached(MessageRecipientStatus.COMPLETED, MessageRecipientStatus.ARCHIVED),
    }

async def get_accessible_message(message_id: str, current_user: dict) -> dict:
    """Shared access check reused by every single-message endpoint below -
    sender, an actual participant of the thread (checked by thread_id, so
    this resolves correctly whether message_id points at the root or at one
    specific reply), or that company's owner. Super Admin is never granted a
    path here, satisfying 'must not read company messages' by omission."""
    message = await db.messages.find_one({"id": message_id}, {"_id": 0})
    if not message or message["company_id"] != current_user.get("company_id"):
        raise HTTPException(status_code=404, detail="Message not found")

    is_sender = message["sender_id"] == current_user["id"]
    is_owner = current_user["role"] == UserRole.COMPANY_OWNER
    participant_row = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"]}, {"_id": 0}
    )
    if not (is_sender or is_owner or participant_row):
        raise HTTPException(status_code=404, detail="Message not found")

    return message

REMINDER_PRESETS = {"30m": timedelta(minutes=30), "1h": timedelta(hours=1), "tomorrow": timedelta(days=1)}

async def deliver_due_reminders(user_id: str):
    """Self-heal on read, the same house pattern used for subscription
    expiry/presence: no scheduler exists in this codebase and none is
    introduced here. Whichever request next reads this user's notifications
    (an existing, already-polled-by-nothing-new endpoint) converts any
    reminder whose time has passed into a real notification row, then marks
    it delivered so it's never converted twice."""
    now_iso = datetime.now(timezone.utc).isoformat()
    due = await db.message_reminders.find(
        {"user_id": user_id, "notified_at": None, "remind_at": {"$lte": now_iso}}, {"_id": 0}
    ).to_list(100)
    for reminder in due:
        message = await db.messages.find_one({"id": reminder["message_id"]}, {"_id": 0, "subject": 1})
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "company_id": reminder["company_id"],
            "type": "message_reminder",
            "title": "تذكير برسالة",
            "message": f"تذكير: {message['subject'] if message else ''}",
            "read_status": False,
            "created_at": now_iso,
        })
        await db.message_reminders.update_one({"id": reminder["id"]}, {"$set": {"notified_at": now_iso}})

async def deliver_message(message: dict, employee_ids: List[str]):
    """Creates a brand-new thread's participant rows - one per (thread,
    participant), including the sender - plus one notification per real
    recipient and one 'sent' activity entry. Reused by immediate send,
    draft->send, and forward (each of which starts a fresh thread_id).
    Exactly one message_recipients row exists per participant per thread,
    never per individual message-in-thread - this is what makes 'one row
    per thread' in Inbox/Sent naturally correct rather than something the
    list endpoints have to fake by de-duplicating replies afterward."""
    now = datetime.now(timezone.utc).isoformat()

    await db.message_recipients.insert_one({
        "id": str(uuid.uuid4()),
        "thread_id": message["thread_id"],
        "company_id": message["company_id"],
        "recipient_id": message["sender_id"],
        "recipient_name": message.get("sender_name"),
        "role": "sender",
        "status": MessageRecipientStatus.DELIVERED,
        "is_unread": False,
        "is_starred": False,
        "delivered_at": now,
        "seen_at": None, "accepted_at": None, "completed_at": None, "archived_at": None,
    })

    for employee_id in employee_ids:
        recipient = await db.users.find_one({"id": employee_id}, {"_id": 0, "name": 1})
        await db.message_recipients.insert_one({
            "id": str(uuid.uuid4()),
            "thread_id": message["thread_id"],
            "company_id": message["company_id"],
            "recipient_id": employee_id,
            "recipient_name": recipient["name"] if recipient else None,
            "role": "recipient",
            "status": MessageRecipientStatus.DELIVERED,
            "is_unread": True,
            "is_starred": False,
            "delivered_at": now,
            "seen_at": None, "accepted_at": None, "completed_at": None, "archived_at": None,
        })
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": employee_id,
            "company_id": message["company_id"],
            "type": "work_message",
            "title": "رسالة عمل جديدة",
            "message": message["subject"],
            "read_status": False,
            "created_at": now,
        })

    await write_message_activity(
        message["company_id"], message["id"],
        {"id": message["sender_id"], "name": message.get("sender_name")},
        "sent", message["subject"],
    )

async def notify_reply_participants(reply: dict, thread_id: str, participant_ids: List[str]):
    """A reply does NOT create new message_recipients rows - it re-opens the
    existing thread-level row for every other participant (bumping it back
    to unread) and notifies them. This is what keeps a thread as exactly one
    row in Inbox/Sent even after replies arrive, per 'do not create
    independent reply messages / keep one conversation thread'."""
    now = datetime.now(timezone.utc).isoformat()
    for participant_id in participant_ids:
        await db.message_recipients.update_one(
            {"thread_id": thread_id, "recipient_id": participant_id},
            {"$set": {"is_unread": True}}
        )
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": participant_id,
            "company_id": reply["company_id"],
            "type": "message_reply",
            "title": "رد على رسالة",
            "message": reply["subject"],
            "read_status": False,
            "created_at": now,
        })
    await write_message_activity(
        reply["company_id"], reply["id"],
        {"id": reply["sender_id"], "name": reply.get("sender_name")},
        "replied", reply["subject"],
    )

async def enrich_messages(messages: List[dict], current_user: dict) -> List[dict]:
    """Batches every join a message list needs into one query per collection
    (never N+1), and never selects message_attachments.data - list views
    must stay cheap regardless of how large an attachment payload is
    (lazy-loaded separately only when a message is actually opened). Callers
    always pass root messages (id == thread_id), so grouping by thread_id
    below is equivalent to grouping by the message's own id, but also
    correctly aggregates attachments/replies that live on replies within
    the same thread."""
    if not messages:
        return []
    thread_ids = list({m["thread_id"] for m in messages})

    all_recipients = await db.message_recipients.find(
        {"thread_id": {"$in": thread_ids}}, {"_id": 0}
    ).to_list(10000)
    recipients_by_thread: Dict[str, list] = {}
    for r in all_recipients:
        recipients_by_thread.setdefault(r["thread_id"], []).append(r)

    attachments = await db.message_attachments.find(
        {"thread_id": {"$in": thread_ids}}, {"_id": 0, "thread_id": 1}
    ).to_list(10000)
    attachment_counts: Dict[str, int] = {}
    for a in attachments:
        attachment_counts[a["thread_id"]] = attachment_counts.get(a["thread_id"], 0) + 1

    reply_counts_raw = await db.messages.find(
        {"thread_id": {"$in": thread_ids}}, {"_id": 0, "id": 1, "thread_id": 1}
    ).to_list(10000)
    reply_counts: Dict[str, int] = {}
    for m in reply_counts_raw:
        reply_counts[m["thread_id"]] = reply_counts.get(m["thread_id"], 0) + 1

    now_iso = datetime.now(timezone.utc).isoformat()
    enriched = []
    for m in messages:
        participants = recipients_by_thread.get(m["thread_id"], [])
        recips_only = [p for p in participants if p.get("role") == "recipient"]
        mine = next((p for p in participants if p["recipient_id"] == current_user["id"]), None)
        total = len(recips_only)
        reached = lambda *statuses: sum(1 for r in recips_only if r["status"] in statuses)
        m["recipient_names"] = [r["recipient_name"] for r in recips_only if r["recipient_name"]]
        m["attachment_count"] = attachment_counts.get(m["thread_id"], 0)
        m["reply_count"] = max(reply_counts.get(m["thread_id"], 1) - 1, 0)
        m["delivery_progress"] = {
            "total": total,
            "delivered": total,
            "seen": reached(MessageRecipientStatus.SEEN, MessageRecipientStatus.ACCEPTED,
                             MessageRecipientStatus.COMPLETED, MessageRecipientStatus.ARCHIVED),
            "accepted": reached(MessageRecipientStatus.ACCEPTED, MessageRecipientStatus.COMPLETED,
                                 MessageRecipientStatus.ARCHIVED),
            "completed": reached(MessageRecipientStatus.COMPLETED, MessageRecipientStatus.ARCHIVED),
        }
        m["my_status"] = mine["status"] if mine else None
        m["my_is_unread"] = mine["is_unread"] if mine else None
        m["my_is_starred"] = mine["is_starred"] if mine else None
        m["is_expired"] = bool(m.get("expires_at")) and m["expires_at"] < now_iso
        enriched.append(m)
    return enriched

# ---- Calendar helpers ----
# Deliberately independent of every Work Messaging helper above (own
# collections, own counter namespace, own attachment collection) even where
# the shape is identical - Work Messages is on the do-not-modify list, so
# nothing here calls into or alters it. classify_attachment_type and
# MAX_ATTACHMENT_BYTES are reused as-is (pure, unmodified, side-effect-free)
# since reusing a pure function isn't modifying the feature that also uses it.

async def next_calendar_reference_number(company_id: str) -> str:
    """Same atomic find-and-increment pattern as messaging's reference
    numbers, deliberately reimplemented (not called) to avoid touching
    Work Messages' next_reference_number - own counter key, own prefix."""
    counter = await db.counters.find_one_and_update(
        {"id": f"calendar_seq:{company_id}"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"CAL-{counter['value']:06d}"

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

async def resolve_calendar_recipients(company_id: str, recipient_type: str, recipient_ids: List[str], owner_id: Optional[str]) -> List[str]:
    """Fan-out at creation time, same snapshot-at-send convention already
    used everywhere else in this codebase. 'company' (every employee) and
    multi-department are both real, working options now - not just
    architecture placeholders - since Company Holidays require them."""
    if recipient_type == EventRecipientType.OWNER:
        return [owner_id] if owner_id else []
    if recipient_type == EventRecipientType.COMPANY:
        employees = await db.users.find({"company_id": company_id, "role": UserRole.EMPLOYEE}, {"_id": 0, "id": 1}).to_list(10000)
        ids = [e["id"] for e in employees]
        return ids + ([owner_id] if owner_id else [])
    if recipient_type == EventRecipientType.DEPARTMENT:
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="At least one department is required")
        employees = await db.users.find(
            {"company_id": company_id, "role": UserRole.EMPLOYEE, "department": {"$in": recipient_ids}},
            {"_id": 0, "id": 1}
        ).to_list(10000)
        if not employees:
            raise HTTPException(status_code=400, detail="No employees found in the selected department(s)")
        return [e["id"] for e in employees]
    if recipient_type == EventRecipientType.OWNER_PLUS_EMPLOYEES:
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="At least one employee is required")
        valid = await db.users.count_documents({"id": {"$in": recipient_ids}, "company_id": company_id})
        if valid != len(set(recipient_ids)):
            raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")
        return list(set(recipient_ids)) + ([owner_id] if owner_id else [])
    # EMPLOYEE (single or multiple)
    if not recipient_ids:
        raise HTTPException(status_code=400, detail="At least one participant is required")
    valid = await db.users.count_documents({"id": {"$in": recipient_ids}, "company_id": company_id})
    if valid != len(set(recipient_ids)):
        raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")
    return list(set(recipient_ids))

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

def count_raw_occurrences_before(event: dict, cutoff: datetime) -> int:
    """Counts scheduled recurrence SLOTS before cutoff, ignoring exceptions -
    used only for this_and_future's after_count budget split. A cancelled
    occurrence still consumed one of the original N slots; it must not free
    up budget for an extra occurrence the series was never meant to have.
    (expand_event_occurrences is exception-aware and would undercount here.)"""
    base_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
    if event.get("recurrence_type", RecurrenceType.NONE) == RecurrenceType.NONE:
        return 1 if base_start < cutoff else 0
    cursor = base_start
    count = 0
    iterations = 0
    while cursor < cutoff and iterations < MAX_OCCURRENCE_ITERATIONS:
        iterations += 1
        count += 1
        cursor = step_occurrence(cursor, event["recurrence_type"], event.get("recurrence_interval", 1))
    return count

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

async def write_calendar_activity(company_id: str, event_id: str, actor: dict, verb: str, event_title: str):
    """Own collection, append-only, event-only (never description/attachments/
    notes) - same convention as message_activity_log, deliberately not
    shared with it."""
    await db.calendar_activity_log.insert_one({
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "event_id": event_id,
        "actor_id": actor.get("id"),
        "actor_name": actor.get("name"),
        "verb": verb,
        "event_title": event_title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

CALENDAR_NOTIFICATION_COPY = {
    "created": ("موعد جديد", "تمت دعوتك إلى: {title}"),
    "updated": ("تحديث موعد", "تم تحديث: {title}"),
    "time_changed": ("تغيير موعد", "تم تغيير وقت: {title}"),
    "location_changed": ("تغيير مكان", "تم تغيير مكان: {title}"),
    "cancelled": ("إلغاء موعد", "تم إلغاء: {title}"),
    "attachment_added": ("مرفق جديد", "تمت إضافة مرفق إلى: {title}"),
    "reminder": ("تذكير بموعد", "تذكير: {title}"),
    "owner_created": ("موعد جديد بواسطة موظف", "تم إنشاء موعد يتضمنك: {title}"),
    "accepted": ("تم القبول", "قَبِل {actor_name} دعوة: {title}"),
    "declined": ("تم الرفض", "رفض {actor_name} دعوة: {title}"),
    "tentative": ("رد مبدئي", "رد {actor_name} بشكل مبدئي على: {title}"),
    "all_responded": ("اكتمال الردود", "استجاب جميع المدعوين إلى: {title}"),
}

async def notify_for_calendar_event(event: dict, verb: str, participant_ids: List[str], actor: Optional[dict] = None, notify_owner_too: bool = False):
    """Single dispatch point for every calendar notification - the seam
    where a future email/push channel would plug in without touching any
    caller. Deduplicates recipients (never double-notifies the Owner if
    they're also a participant) and never reaches outside company_id."""
    title_template, message_template = CALENDAR_NOTIFICATION_COPY.get(verb, ("تحديث موعد", "{title}"))
    recipients = set(participant_ids)
    if notify_owner_too:
        company = await db.companies.find_one({"id": event["company_id"]}, {"_id": 0, "owner_id": 1})
        if company:
            recipients.add(company["owner_id"])
    recipients.discard((actor or {}).get("id"))  # never notify the person who caused their own event

    now = datetime.now(timezone.utc).isoformat()
    for user_id in recipients:
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "company_id": event["company_id"],
            "type": f"calendar_{verb}",
            "title": title_template,
            "message": message_template.format(title=event.get("title", ""), actor_name=(actor or {}).get("name", "")),
            "read_status": False,
            "created_at": now,
        })

async def get_accessible_calendar_event(event_id: str, current_user: dict) -> dict:
    """Read access: creator, an actual participant, the company Owner
    (always - matches the 'Owner sees everything' oversight model already
    established for Communication Center), or anyone the event's own
    visibility level opens it to."""
    event = await db.calendar_events.find_one({"id": event_id}, {"_id": 0})
    if not event or event["company_id"] != current_user.get("company_id"):
        raise HTTPException(status_code=404, detail="Event not found")

    if current_user["role"] == UserRole.COMPANY_OWNER:
        return event
    if event["created_by"] == current_user["id"]:
        return event
    participant = await db.calendar_event_participants.find_one(
        {"event_id": event_id, "participant_id": current_user["id"]}, {"_id": 0, "id": 1}
    )
    if participant:
        return event

    visibility = event.get("visibility", EventVisibility.COMPANY)
    if visibility == EventVisibility.COMPANY:
        return event
    if visibility == EventVisibility.DEPARTMENT:
        participant_depts = {p["department"] for p in await db.calendar_event_participants.find(
            {"event_id": event_id}, {"_id": 0, "department": 1}
        ).to_list(1000) if p.get("department")}
        if current_user.get("department") in participant_depts:
            return event

    raise HTTPException(status_code=404, detail="Event not found")

async def check_calendar_conflicts(company_id: str, participant_ids: List[str], start_dt: datetime, end_dt: datetime,
                                    all_day: bool, exclude_event_id: Optional[str] = None) -> List[dict]:
    """Bounded 90-day lookahead, never a full-collection scan. All-day events
    only conflict with other all-day events, per spec."""
    window_end = min(end_dt, start_dt + timedelta(days=CALENDAR_CONFLICT_LOOKAHEAD_DAYS))
    candidate_query = {
        "company_id": company_id,
        "status": {"$ne": "cancelled"},
        "start_date": {"$lte": window_end.date().isoformat()},
        "end_date": {"$gte": start_dt.date().isoformat()},
    }
    if exclude_event_id:
        candidate_query["id"] = {"$ne": exclude_event_id}
    if all_day:
        candidate_query["all_day"] = True

    candidates = await db.calendar_events.find(candidate_query, {"_id": 0}).to_list(500)
    if not candidates:
        return []

    candidate_ids = [c["id"] for c in candidates]
    participant_rows = await db.calendar_event_participants.find(
        {"event_id": {"$in": candidate_ids}, "participant_id": {"$in": participant_ids}}, {"_id": 0}
    ).to_list(5000)
    rows_by_event: Dict[str, list] = {}
    for r in participant_rows:
        rows_by_event.setdefault(r["event_id"], []).append(r)

    conflicts = []
    for candidate in candidates:
        involved = rows_by_event.get(candidate["id"], [])
        if candidate.get("created_by") in participant_ids:
            involved = involved or [{"participant_name": candidate.get("created_by_name")}]
        if not involved:
            continue
        for occ in await expand_event_occurrences(candidate, start_dt, window_end):
            occ_start, occ_end = datetime.fromisoformat(occ["occurrence_start"]), datetime.fromisoformat(occ["occurrence_end"])
            if occ_start < end_dt and occ_end > start_dt:
                conflicts.append({
                    "event_id": candidate["id"], "title": candidate["title"],
                    "occurrence_date": occ["occurrence_date"],
                    "participants": [p.get("participant_name") for p in involved if p.get("participant_name")],
                })
                break
    return conflicts

async def check_holiday_conflict(company_id: str, start_dt: datetime, end_dt: datetime) -> Optional[dict]:
    holidays = await db.calendar_events.find({
        "company_id": company_id, "category": EventCategory.COMPANY_HOLIDAY, "status": {"$ne": "cancelled"},
        "start_date": {"$lte": end_dt.date().isoformat()}, "end_date": {"$gte": start_dt.date().isoformat()},
    }, {"_id": 0}).to_list(50)
    for holiday in holidays:
        for occ in await expand_event_occurrences(holiday, start_dt, end_dt):
            occ_start, occ_end = datetime.fromisoformat(occ["occurrence_start"]), datetime.fromisoformat(occ["occurrence_end"])
            if occ_start < end_dt and occ_end > start_dt:
                return {"event_id": holiday["id"], "title": holiday["title"], "occurrence_date": occ["occurrence_date"]}
    return None

def next_date_for_weekday(target_weekday_sunday0: int, from_date: Optional[date] = None) -> str:
    """target_weekday_sunday0: 0=Sunday..6=Saturday, same convention as
    working_hours.working_days. Returns the next date on/after from_date
    (default today) that falls on that weekday, as YYYY-MM-DD - used to seed
    a weekly-recurring holiday's start_date so the existing recurrence
    machinery (step_occurrence) naturally repeats on the right day."""
    anchor = from_date or datetime.now(timezone.utc).date()
    anchor_sunday0 = (anchor.weekday() + 1) % 7  # date.weekday(): Mon=0..Sun=6
    days_ahead = (target_weekday_sunday0 - anchor_sunday0) % 7
    return (anchor + timedelta(days=days_ahead)).isoformat()

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

async def annotate_holiday_dates(company_id: str, records: List[dict]) -> None:
    """Mutates records in place, adding is_holiday/holiday_type/holiday_title
    so Reports can show 'Official Company Holiday' or 'Weekly Holiday'
    instead of the normal status label on non-working dates - never changes
    the underlying stored status."""
    dates = {r["date"] for r in records if r.get("date")}
    off_by_date = {}
    for d in dates:
        info = await get_day_off_info(company_id, d)
        if info:
            off_by_date[d] = info
    for r in records:
        info = off_by_date.get(r.get("date"))
        r["is_holiday"] = info is not None
        r["holiday_type"] = info["type"] if info else None
        r["holiday_title"] = info["title"] if info else None

async def deliver_due_calendar_reminders(user_id: str):
    """Same self-heal-on-read pattern as message reminders, deliberately a
    separate function/collection - folded into the same GET /notifications
    read alongside (not instead of) the existing message-reminder check."""
    now_iso = datetime.now(timezone.utc).isoformat()
    due = await db.calendar_event_reminders.find(
        {"user_id": user_id, "notified_at": None, "remind_at": {"$lte": now_iso}}, {"_id": 0}
    ).to_list(100)
    for reminder in due:
        event = await db.calendar_events.find_one({"id": reminder["event_id"]}, {"_id": 0, "title": 1, "company_id": 1})
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "company_id": reminder["company_id"],
            "type": "calendar_reminder",
            "title": "تذكير بموعد",
            "message": f"تذكير: {event['title'] if event else ''}",
            "read_status": False,
            "created_at": now_iso,
        })
        await db.calendar_event_reminders.update_one({"id": reminder["id"]}, {"$set": {"notified_at": now_iso}})

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

async def enforce_company_access(user: dict):
    """Blocks company owners/employees whose company subscription is expired
    or suspended. Super Admins have no company_id and are never affected.
    The detail is a structured object (not a plain string) so the frontend can
    show a dedicated blocked-experience page with the actual expiration date,
    rather than a generic error."""
    company_id = user.get("company_id")
    if not company_id:
        return
    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        return
    effective_status = await resolve_subscription_status(company)
    if effective_status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.SUSPENDED):
        raise HTTPException(status_code=403, detail={
            "field": "subscription",
            "status": effective_status,
            "message": f"Company subscription is {effective_status}",
            "subscription_end_date": company.get("subscription_end_date")
        })

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
    PRESENCE_TIMEOUT_SECONDS, computed on read (see get_company_presence)."""
    # Postgres-backed (Auth module, migrated).
    await users_repo.update_last_seen(pg, current_user["id"], datetime.now(timezone.utc))
    await pg.commit()

    response = {"message": "ok"}

    if current_user["role"] == UserRole.EMPLOYEE:
        # Critical Task Alert fallback detection - reuses this pre-existing,
        # already app-wide-polled heartbeat instead of a new polling loop.
        # Pages with their own task polling (e.g. Employee/Tasks.js) surface
        # critical tasks sooner; this is the guaranteed floor (<= heartbeat
        # interval) for pages that don't poll tasks at all.
        pending = await db.tasks.find(
            {
                "assigned_to": current_user["id"],
                "priority": TaskPriority.CRITICAL,
                "status": {"$in": [TaskStatus.NEW, TaskStatus.SEEN]},
            },
            {"_id": 0}
        ).to_list(50)

        pending_critical_tasks = []
        creator_names: Dict[str, Optional[str]] = {}
        for task in pending:
            if not task.get("alert_delivered_at"):
                delivered_at = datetime.now(timezone.utc).isoformat()
                # Atomic claim so concurrent heartbeats (multiple tabs) don't
                # fire the "delivered" notification more than once.
                claimed = await db.tasks.find_one_and_update(
                    {"id": task["id"], "alert_delivered_at": None},
                    {"$set": {"alert_delivered_at": delivered_at}}
                )
                if claimed:
                    task["alert_delivered_at"] = delivered_at
                    await db.notifications.insert_one({
                        "id": str(uuid.uuid4()),
                        "user_id": task["created_by"],
                        "company_id": current_user["company_id"],
                        "type": "critical_task_delivered",
                        "title": "تم تسليم مهمة عاجلة",
                        "message": f"تم عرض تنبيه المهمة العاجلة على الموظف {current_user['name']} للمهمة: {task['title']}",
                        "read_status": False,
                        "created_at": delivered_at
                    })
            creator_id = task.get("created_by")
            if creator_id and creator_id not in creator_names:
                creator = await db.users.find_one({"id": creator_id}, {"_id": 0, "name": 1})
                creator_names[creator_id] = creator["name"] if creator else None
            pending_critical_tasks.append({
                "id": task["id"],
                "title": task["title"],
                "description": task.get("description"),
                "priority": task["priority"],
                "status": task["status"],
                "due_date": task.get("due_date"),
                "due_time": task.get("due_time"),
                "created_at": task.get("created_at"),
                "created_by_name": creator_names.get(creator_id),
                "requires_proof": task.get("requires_proof", False),
                "attachments": task.get("attachments", []),
            })

        response["pending_critical_tasks"] = pending_critical_tasks

    return response

# ============ Super Admin Routes ============

EXPIRING_SOON_DAYS = 7

@api_router.get("/admin/statistics")
async def get_admin_statistics(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    companies = await db.companies.find({}, {"_id": 0}).to_list(1000)
    total_companies = len(companies)
    total_employees = await db.users.count_documents({"role": UserRole.EMPLOYEE})

    today = datetime.now(timezone.utc).date()
    soon_cutoff = (today + timedelta(days=EXPIRING_SOON_DAYS)).isoformat()
    today_str = today.isoformat()

    active_count = 0
    expired_count = 0
    total_revenue = 0
    expiring_soon_companies = []
    expired_companies_list = []
    companies_online_count = 0

    for company in companies:
        # Self-heal on read: resolve the true status before counting/displaying it
        effective_status = await resolve_subscription_status(company)

        presence = await get_company_presence(company)
        if presence["company_online"]:
            companies_online_count += 1

        if effective_status == SubscriptionStatus.ACTIVE:
            # Revenue only counts Active companies - expired/suspended contribute nothing
            total_revenue += company.get("subscription_price") or 0
            active_count += 1
            end_date = company.get("subscription_end_date")
            if end_date:
                end_date_only = end_date[:10]
                if today_str <= end_date_only <= soon_cutoff:
                    remaining_days = (datetime.fromisoformat(end_date_only).date() - today).days
                    expiring_soon_companies.append({
                        "id": company["id"],
                        "name": company["name"],
                        "end_date": end_date_only,
                        "remaining_days": remaining_days
                    })
        elif effective_status == SubscriptionStatus.EXPIRED:
            expired_count += 1
            expired_companies_list.append({
                "id": company["id"],
                "name": company["name"],
                "end_date": (company.get("subscription_end_date") or "")[:10],
                "status": effective_status
            })

    return {
        "total_companies": total_companies,
        "active_companies": active_count,
        "inactive_companies": total_companies - active_count,
        "total_employees": total_employees,
        "total_revenue": total_revenue,
        "expired_companies": expired_count,
        "expiring_soon_count": len(expiring_soon_companies),
        "expiring_soon_companies": expiring_soon_companies,
        "expired_companies_list": expired_companies_list,
        "companies_online": companies_online_count,
        "companies_offline": total_companies - companies_online_count
    }

@api_router.get("/admin/companies", response_model=List[CompanyResponse])
async def get_all_companies(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    companies = await db.companies.find({}, {"_id": 0}).to_list(1000)

    # Add employee count, owner contact info, and self-healed status to each company
    for company in companies:
        count = await db.users.count_documents({"company_id": company["id"], "role": UserRole.EMPLOYEE})
        company["employee_count"] = count
        owner = await db.users.find_one({"id": company.get("owner_id")}, {"_id": 0, "password": 0})
        if owner:
            company["owner_name"] = owner.get("name")
            company["owner_email"] = owner.get("email")
            company["owner_phone"] = owner.get("phone")
        company["subscription_status"] = await resolve_subscription_status(company)
        company.update(await get_company_presence(company))

    return companies

@api_router.post("/admin/companies", response_model=CompanyResponse)
async def create_company(company: CompanyCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if owner email already exists
    existing = await db.users.find_one({"email": company.owner_email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Subscription price and max_employees are always derived server-side from
    # the selected plan - never trusted from the client.
    plan = await db.subscription_plans.find_one({"id": company.subscription_plan_id}, {"_id": 0})
    if not plan or not plan.get("is_active"):
        raise HTTPException(status_code=400, detail="Selected subscription plan is not available")

    company_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    
    # The attendance QR encodes only an opaque random token - never any
    # company data (see ensure_qr_token for companies created before this).
    qr_token = uuid.uuid4().hex
    qr_code = generate_qr_code(qr_token)

    # Create owner user
    owner_user = {
        "id": owner_id,
        "email": company.owner_email,
        "phone": company.owner_phone,
        "password": hash_password(company.owner_password),
        "name": company.owner_name,
        "role": UserRole.COMPANY_OWNER,
        "company_id": company_id,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(owner_user)
    
    # Create company
    company_doc = {
        "id": company_id,
        "name": company.name,
        "owner_id": owner_id,
        "qr_code": qr_code,
        "qr_token": qr_token,
        "subscription_status": "active",
        "subscription_plan_id": company.subscription_plan_id,
        "subscription_start_date": None,
        "subscription_end_date": None,
        **plan_config_for_company(plan),
        "address": company.address,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "employee_count": 0
    }
    await db.companies.insert_one(company_doc)
    
    return CompanyResponse(**company_doc)

@api_router.put("/admin/companies/{company_id}")
async def update_company(company_id: str, updates: CompanyOwnerUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    owner_id = company.get("owner_id")

    # Whitelist allowed company fields. subscription_status, subscription_end_date
    # and subscription_price are intentionally excluded here: status/dates are only
    # changed via the dedicated activate/suspend/reactivate/renew actions (so their
    # validation can't be bypassed by a generic field edit), and price is always
    # derived from the assigned plan below, never client-set directly.
    allowed_fields = ["name", "address"]
    company_updates = {k: v for k, v in updates.model_dump(exclude_none=True).items() if k in allowed_fields}

    # Changing the plan updates the company's full plan configuration together,
    # but never touches subscription_status, subscription_start_date or
    # subscription_end_date.
    if updates.subscription_plan_id is not None:
        plan = await db.subscription_plans.find_one({"id": updates.subscription_plan_id}, {"_id": 0})
        if not plan or not plan.get("is_active"):
            raise HTTPException(status_code=400, detail="Selected subscription plan is not available")
        company_updates["subscription_plan_id"] = updates.subscription_plan_id
        company_updates.update(plan_config_for_company(plan))

    # Validate owner account changes before writing anything, so the company
    # and owner updates either both apply or neither does. MongoDB multi-document
    # transactions require a replica set, which this deployment doesn't use (and
    # nothing else in this app uses transactions either), so atomicity here is
    # enforced by validating everything up front rather than a DB transaction.
    owner_updates = {}

    if updates.owner_name is not None:
        if not updates.owner_name.strip():
            raise HTTPException(status_code=400, detail={"field": "owner_name", "message": "Owner name is required"})
        owner_updates["name"] = updates.owner_name

    if updates.owner_email is not None:
        existing = await db.users.find_one({"email": updates.owner_email, "id": {"$ne": owner_id}})
        if existing:
            raise HTTPException(status_code=400, detail={"field": "owner_email", "message": "Email already registered"})
        owner_updates["email"] = updates.owner_email

    if updates.owner_phone is not None:
        if not updates.owner_phone.strip():
            raise HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number is required"})
        existing = await db.users.find_one({"phone": updates.owner_phone, "id": {"$ne": owner_id}})
        if existing:
            raise HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number already registered"})
        owner_updates["phone"] = updates.owner_phone

    if updates.owner_password or updates.owner_password_confirm:
        if updates.owner_password != updates.owner_password_confirm:
            raise HTTPException(status_code=400, detail={"field": "owner_password_confirm", "message": "Passwords do not match"})
        owner_updates["password"] = hash_password(updates.owner_password)

    if not company_updates and not owner_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if company_updates:
        await db.companies.update_one({"id": company_id}, {"$set": company_updates})

    if owner_updates and owner_id:
        await db.users.update_one({"id": owner_id, "role": UserRole.COMPANY_OWNER}, {"$set": owner_updates})

    return {"message": "Company updated successfully"}

@api_router.post("/admin/companies/{company_id}/activate")
async def activate_subscription(company_id: str, data: SubscriptionActivate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if data.subscription_end_date < data.subscription_start_date:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    await db.companies.update_one(
        {"id": company_id},
        {"$set": {
            "subscription_status": SubscriptionStatus.ACTIVE,
            "subscription_start_date": data.subscription_start_date,
            "subscription_end_date": data.subscription_end_date
        }}
    )
    return {"message": "Subscription activated successfully"}

@api_router.post("/admin/companies/{company_id}/suspend")
async def suspend_subscription(company_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.companies.update_one(
        {"id": company_id},
        {"$set": {"subscription_status": SubscriptionStatus.SUSPENDED}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Company not found")

    return {"message": "Subscription suspended successfully"}

@api_router.post("/admin/companies/{company_id}/reactivate")
async def reactivate_subscription(company_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.get("subscription_status") != SubscriptionStatus.SUSPENDED:
        raise HTTPException(status_code=400, detail="Company is not suspended")

    if is_past_date(company.get("subscription_end_date")):
        raise HTTPException(
            status_code=400,
            detail="Subscription has expired and must be renewed by updating the subscription dates first"
        )

    await db.companies.update_one({"id": company_id}, {"$set": {"subscription_status": SubscriptionStatus.ACTIVE}})
    return {"message": "Subscription reactivated successfully"}

@api_router.post("/admin/companies/{company_id}/renew")
async def renew_subscription(company_id: str, data: SubscriptionRenew, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Option 1 (renew using current plan): plan_id omitted, falls back to the
    # company's current plan. Option 2 (renew using a different plan): the
    # admin's chosen plan_id is used instead.
    plan_id = data.subscription_plan_id or company.get("subscription_plan_id")
    if not plan_id:
        raise HTTPException(status_code=400, detail="No subscription plan selected")

    plan = await db.subscription_plans.find_one({"id": plan_id}, {"_id": 0})
    if not plan or not plan.get("is_active"):
        raise HTTPException(status_code=400, detail="Selected subscription plan is not available")

    # Extend from the later of the current end date or today, so renewing before
    # expiry doesn't discard remaining time. Uses the same 30-days-per-month
    # approximation already used by the existing Stripe webhook success handler.
    # subscription_start_date is intentionally left untouched.
    today = datetime.now(timezone.utc).date()
    base_date = today
    current_end = company.get("subscription_end_date")
    if current_end:
        try:
            base_date = max(today, datetime.fromisoformat(current_end[:10]).date())
        except ValueError:
            base_date = today
    new_end_date = (base_date + timedelta(days=30 * plan["duration_months"])).isoformat()

    await db.companies.update_one(
        {"id": company_id},
        {"$set": {
            "subscription_plan_id": plan_id,
            **plan_config_for_company(plan),
            "subscription_status": SubscriptionStatus.ACTIVE,
            "subscription_end_date": new_end_date
        }}
    )
    return {"message": "Subscription renewed successfully"}

@api_router.delete("/admin/companies/{company_id}")
async def delete_company(company_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Delete company and all related data
    await db.companies.delete_one({"id": company_id})
    await db.users.delete_many({"company_id": company_id})
    await db.tasks.delete_many({"company_id": company_id})
    await db.attendance.delete_many({"company_id": company_id})
    await db.reports.delete_many({"company_id": company_id})
    
    return {"message": "Company deleted successfully"}

@api_router.get("/admin/subscription-plans", response_model=List[SubscriptionPlanResponse])
async def get_subscription_plans(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    plans = await db.subscription_plans.find({}, {"_id": 0}).to_list(100)
    return plans

@api_router.post("/admin/subscription-plans", response_model=SubscriptionPlanResponse)
async def create_subscription_plan(plan: SubscriptionPlanCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    plan_doc = {
        "id": str(uuid.uuid4()),
        **plan.model_dump(),
        "is_active": True
    }
    await db.subscription_plans.insert_one(plan_doc)
    return SubscriptionPlanResponse(**plan_doc)

@api_router.put("/admin/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, updates: SubscriptionPlanUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    safe_updates = updates.model_dump(exclude_none=True)
    if not safe_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    result = await db.subscription_plans.update_one({"id": plan_id}, {"$set": safe_updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")

    return {"message": "Plan updated successfully"}

@api_router.delete("/admin/subscription-plans/{plan_id}")
async def delete_subscription_plan(plan_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    companies_using_plan = await db.companies.count_documents({"subscription_plan_id": plan_id})
    if companies_using_plan > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete this plan: {companies_using_plan} company(ies) are currently assigned to it"
        )

    result = await db.subscription_plans.delete_one({"id": plan_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")

    return {"message": "Plan deleted successfully"}

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
async def get_employees(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employees = await db.users.find(
        {"company_id": current_user["company_id"], "role": UserRole.EMPLOYEE},
        {"_id": 0, "password": 0}
    ).to_list(1000)
    
    return employees

@api_router.post("/owner/employees", response_model=UserResponse)
async def create_employee(employee: UserCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if email already exists
    existing = await db.users.find_one({"email": employee.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Enforce the company's max_employees limit (from its subscription plan).
    # Companies without a max_employees value (e.g. legacy companies with no
    # plan assigned yet) are treated as unlimited.
    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    max_employees = company.get("max_employees") if company else None
    if max_employees:
        current_employee_count = await db.users.count_documents({
            "company_id": current_user["company_id"],
            "role": UserRole.EMPLOYEE
        })
        if current_employee_count >= max_employees:
            raise HTTPException(
                status_code=400,
                detail="Employee limit reached. Please upgrade your subscription plan to add more employees."
            )

    employee_doc = {
        "id": str(uuid.uuid4()),
        "email": employee.email,
        "phone": employee.phone,
        "password": hash_password(employee.password),
        "name": employee.name,
        "role": UserRole.EMPLOYEE,
        "company_id": current_user["company_id"],
        "department": employee.department,
        "position": employee.position,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(employee_doc)
    
    return UserResponse(**{k: v for k, v in employee_doc.items() if k != "password"})

@api_router.put("/owner/employees/{employee_id}")
async def update_employee(employee_id: str, updates: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Whitelist allowed fields to prevent tampering
    allowed_fields = ["name", "phone", "department", "position", "status", "avatar"]
    safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    if "password" in updates and updates["password"]:
        safe_updates["password"] = hash_password(updates["password"])
    
    if not safe_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    
    # Ensure can only update employees from same company
    result = await db.users.update_one(
        {"id": employee_id, "company_id": current_user["company_id"], "role": UserRole.EMPLOYEE},
        {"$set": safe_updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return {"message": "Employee updated successfully"}

@api_router.delete("/owner/employees/{employee_id}")
async def delete_employee(employee_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.users.delete_one(
        {"id": employee_id, "company_id": current_user["company_id"], "role": UserRole.EMPLOYEE}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return {"message": "Employee deleted successfully"}

@api_router.get("/owner/tasks", response_model=List[TaskResponse])
async def get_tasks(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    tasks = await db.tasks.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).to_list(1000)

    # Add employee names
    for task in tasks:
        employee = await db.users.find_one({"id": task["assigned_to"]}, {"_id": 0})
        if employee:
            task["assigned_to_name"] = employee["name"]
        if task.get("completed_by"):
            completer = await db.users.find_one({"id": task["completed_by"]}, {"_id": 0})
            if completer:
                task["completed_by_name"] = completer["name"]

    return tasks

@api_router.post("/owner/tasks", response_model=TaskResponse)
async def create_task(task: TaskCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    task_doc = {
        "id": str(uuid.uuid4()),
        "company_id": current_user["company_id"],
        "assigned_to": task.assigned_to,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "status": TaskStatus.NEW,
        "due_date": task.due_date,
        "due_time": task.due_time,
        "requires_proof": task.requires_proof,
        "proof_files": [],
        "attachments": validate_task_attachments(task.attachments),
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        # Critical Task Alert timeline - additive, populated for all tasks
        # for consistency though only read for priority == "critical".
        "alert_delivered_at": None,
        "received_at": None
    }
    await db.tasks.insert_one(task_doc)
    
    # Create notification for employee
    notification = {
        "id": str(uuid.uuid4()),
        "user_id": task.assigned_to,
        "company_id": current_user["company_id"],
        "type": "task_assigned",
        "title": "مهمة جديدة",
        "message": f"تم تعيين مهمة جديدة لك: {task.title}",
        "read_status": False,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.notifications.insert_one(notification)
    
    # Get employee name
    employee = await db.users.find_one({"id": task.assigned_to}, {"_id": 0})
    if employee:
        task_doc["assigned_to_name"] = employee["name"]
    
    return TaskResponse(**task_doc)

@api_router.put("/owner/tasks/{task_id}")
async def update_task(task_id: str, updates: TaskUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}
    
    result = await db.tasks.update_one(
        {"id": task_id, "company_id": current_user["company_id"]},
        {"$set": update_dict}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task updated successfully"}

@api_router.delete("/owner/tasks/{task_id}")
async def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    task = await db.tasks.find_one({"id": task_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Task history must always remain available - in-progress, completed, and
    # cancelled tasks are never physically deleted. An in-progress task can be
    # cancelled instead (see /owner/tasks/{id}/cancel), which keeps the row.
    status = task.get("status")
    if status == TaskStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Cannot delete a task that is in progress. Cancel it instead to preserve history.")
    if status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Cannot delete a {status} task - history must remain available.")

    result = await db.tasks.delete_one(
        {"id": task_id, "company_id": current_user["company_id"]}
    )

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"message": "Task deleted successfully"}

@api_router.post("/owner/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    task = await db.tasks.find_one({"id": task_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Task is already {task.get('status')}")

    await db.tasks.update_one({"id": task_id}, {"$set": {"status": TaskStatus.CANCELLED}})
    return {"message": "Task cancelled successfully"}

# ---- Daily Recurring Tasks (templates) ----

@api_router.get("/owner/daily-tasks", response_model=List[DailyTaskResponse])
async def get_daily_tasks(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    templates = await db.daily_tasks.find({"company_id": current_user["company_id"]}, {"_id": 0}).to_list(1000)
    for template in templates:
        names = []
        for emp_id in template.get("assigned_to", []):
            emp = await db.users.find_one({"id": emp_id}, {"_id": 0})
            if emp:
                names.append(emp["name"])
        template["assigned_to_names"] = names
    return templates

@api_router.post("/owner/daily-tasks", response_model=DailyTaskResponse)
async def create_daily_task(template: DailyTaskCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    if not template.assigned_to:
        raise HTTPException(status_code=400, detail="At least one employee must be assigned")

    template_doc = {
        "id": str(uuid.uuid4()),
        "company_id": current_user["company_id"],
        "title": template.title,
        "description": template.description,
        "assigned_to": template.assigned_to,
        "execution_time": template.execution_time,
        "requires_proof": template.requires_proof,
        "is_active": True,
        "recurrence_type": RecurrenceType.DAILY,
        "recurrence_config": {},
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.daily_tasks.insert_one(template_doc)
    return DailyTaskResponse(**template_doc)

@api_router.put("/owner/daily-tasks/{template_id}")
async def update_daily_task(template_id: str, updates: DailyTaskUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    update_dict = updates.model_dump(exclude_none=True)
    if not update_dict:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    result = await db.daily_tasks.update_one(
        {"id": template_id, "company_id": current_user["company_id"]},
        {"$set": update_dict}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Daily task not found")
    return {"message": "Daily task updated successfully"}

@api_router.post("/owner/daily-tasks/{template_id}/toggle")
async def toggle_daily_task(template_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    template = await db.daily_tasks.find_one({"id": template_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Daily task not found")

    new_active = not template.get("is_active", True)
    await db.daily_tasks.update_one({"id": template_id}, {"$set": {"is_active": new_active}})
    return {"message": "Daily task updated successfully", "is_active": new_active}

@api_router.delete("/owner/daily-tasks/{template_id}")
async def delete_daily_task(template_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    # Deletes the template only - past daily-task occurrences already created
    # in the tasks collection are left untouched, preserving history.
    result = await db.daily_tasks.delete_one({"id": template_id, "company_id": current_user["company_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Daily task not found")
    return {"message": "Daily task deleted successfully"}

# ---- Urgent Tasks ----

@api_router.post("/owner/urgent-tasks")
async def create_urgent_task(task: UrgentTaskCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    if not task.assigned_to:
        raise HTTPException(status_code=400, detail="At least one employee must be assigned")

    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    created_ids = []

    for employee_id in task.assigned_to:
        task_doc = {
            "id": str(uuid.uuid4()),
            "company_id": current_user["company_id"],
            "assigned_to": employee_id,
            "title": task.title,
            "description": task.description,
            "priority": TaskPriority.HIGH,
            "status": TaskStatus.NEW,
            "due_date": task.due_date,
            "requires_proof": task.requires_proof,
            "proof_files": [],
            "created_by": current_user["id"],
            "created_at": now,
            "completed_at": None,
            "task_category": TaskCategory.URGENT,
            "execution_date": task.execution_date,
            "execution_time": task.execution_time,
            "due_time": task.due_time,
            "started_at": None,
            "seen_at": None,
            "completed_by": None,
            "batch_id": batch_id
        }
        await db.tasks.insert_one(task_doc)
        created_ids.append(task_doc["id"])

        notification = {
            "id": str(uuid.uuid4()),
            "user_id": employee_id,
            "company_id": current_user["company_id"],
            "type": "urgent_task",
            "title": "مهمة جديدة",
            "message": f"لديك مهمة جديدة: {task.title}",
            "read_status": False,
            "created_at": now
        }
        await db.notifications.insert_one(notification)

    return {"message": "Urgent task created successfully", "task_ids": created_ids, "batch_id": batch_id}

@api_router.get("/owner/attendance", response_model=List[AttendanceResponse])
async def get_attendance(
    current_user: dict = Depends(get_current_user),
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

    query = {"company_id": current_user["company_id"]}
    if date_from or date_to:
        date_query = {}
        if date_from:
            date_query["$gte"] = date_from
        if date_to:
            date_query["$lte"] = date_to
        query["date"] = date_query
    elif date:
        query["date"] = date
    else:
        query["date"] = datetime.now(timezone.utc).date().isoformat()

    if employee_id:
        query["employee_id"] = employee_id
    # "checked_out" is a derived state (a set check_out_time), not a stored
    # status value like present/late.
    if status == "checked_out":
        query["check_out_time"] = {"$ne": None}
    elif status:
        query["status"] = status

    attendance = await db.attendance.find(query, {"_id": 0}).sort([("date", -1), ("check_in_time", -1)]).to_list(2000)

    employees = await db.users.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0, "id": 1, "name": 1, "department": 1}
    ).to_list(1000)
    employee_map = {e["id"]: e for e in employees}

    results = []
    for record in attendance:
        employee = employee_map.get(record["employee_id"])
        if employee:
            record["employee_name"] = employee["name"]
        # Records that predate the department snapshot fall back to the
        # employee's current department for display only - stored data is
        # never touched.
        if record.get("employee_department") is None and employee:
            record["employee_department"] = employee.get("department")
        if record.get("working_duration_minutes") is None:
            record["working_duration_minutes"] = _duration_minutes(record.get("check_in_time"), record.get("check_out_time"))
        if department and record.get("employee_department") != department:
            continue
        if search and search.strip().lower() not in (record.get("employee_name") or "").lower():
            continue
        results.append(record)

    await annotate_holiday_dates(current_user["company_id"], results)
    return results

@api_router.get("/owner/attendance/settings")
async def get_attendance_settings(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    await ensure_qr_token(company)
    settings = attendance_settings_for(company)
    return {
        "settings": settings,
        "location_configured": settings["latitude"] is not None and settings["longitude"] is not None,
        "qr_code": company["qr_code"],
        # The owner already holds the token (it's what their QR image encodes);
        # exposing it here is owner-scoped and enables API-level testing.
        "qr_token": company["qr_token"],
        "company_name": company["name"],
    }

@api_router.put("/owner/attendance/settings")
async def update_attendance_settings(updates: AttendanceSettingsUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    provided = updates.model_dump(exclude_none=True)
    if not provided:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if ("latitude" in provided) != ("longitude" in provided):
        raise HTTPException(status_code=400, detail="يجب تحديد خط العرض وخط الطول معاً.")

    # updated_at/updated_by/updated_by_name are always derived from the
    # authenticated session here, never accepted from the request body -
    # AttendanceSettingsUpdate has no fields for them.
    provided["updated_at"] = datetime.now(timezone.utc).isoformat()
    provided["updated_by"] = current_user["id"]
    provided["updated_by_name"] = current_user.get("name")

    await db.companies.update_one(
        {"id": current_user["company_id"]},
        {"$set": {f"attendance_settings.{key}": value for key, value in provided.items()}}
    )

    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    settings = attendance_settings_for(company)
    return {
        "message": "Attendance settings updated",
        "settings": settings,
        "location_configured": settings["latitude"] is not None and settings["longitude"] is not None,
    }

@api_router.get("/owner/attendance/analytics")
async def get_attendance_analytics(
    current_user: dict = Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = current_user["company_id"]
    today = datetime.now(timezone.utc).date()
    end_date = min(date_to or today.isoformat(), today.isoformat())
    start_date = date_from or (today - timedelta(days=29)).isoformat()

    records = await db.attendance.find(
        {"company_id": company_id, "date": {"$gte": start_date, "$lte": end_date}},
        {"_id": 0}
    ).to_list(10000)

    employees = await db.users.find(
        {"company_id": company_id, "role": UserRole.EMPLOYEE},
        {"_id": 0, "id": 1, "name": 1, "department": 1}
    ).to_list(1000)
    total_employees = len(employees)

    def time_of_day_minutes(iso: Optional[str]) -> Optional[float]:
        if not iso:
            return None
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            return None
        return parsed.hour * 60 + parsed.minute + parsed.second / 60

    def format_clock(minutes_list: list) -> Optional[str]:
        if not minutes_list:
            return None
        avg = sum(minutes_list) / len(minutes_list)
        return f"{int(avg // 60):02d}:{int(avg % 60):02d}"

    check_in_minutes = []
    check_out_minutes = []
    durations = []
    for record in records:
        m_in = time_of_day_minutes(record.get("check_in_time"))
        if m_in is not None:
            check_in_minutes.append(m_in)
        m_out = time_of_day_minutes(record.get("check_out_time"))
        if m_out is not None:
            check_out_minutes.append(m_out)
        duration = record.get("working_duration_minutes")
        if duration is None:
            duration = _duration_minutes(record.get("check_in_time"), record.get("check_out_time"))
        if duration is not None:
            durations.append(duration)

    try:
        day_count = (datetime.fromisoformat(end_date).date() - datetime.fromisoformat(start_date).date()).days + 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date range")
    if day_count < 1:
        raise HTTPException(status_code=400, detail="Invalid date range")
    day_count = min(day_count, 366)

    attended_records = [r for r in records if r.get("check_in_time")]
    late_count = sum(1 for r in records if r.get("status") == AttendanceStatus.LATE)
    expected_attendance = total_employees * day_count
    # Absence is derived (expected minus attended), never stored - consistent
    # with how the owner dashboard already computes absent_today.
    absence_count = max(0, expected_attendance - len(attended_records))
    attendance_percentage = round(len(attended_records) / expected_attendance * 100, 1) if expected_attendance > 0 else 0
    # Same denominator as attendance_percentage, so the two read consistently
    # together (e.g. "92% present, 15% of which were late").
    late_percentage = round(late_count / expected_attendance * 100, 1) if expected_attendance > 0 else 0

    distances = [r["distance_from_company_meters"] for r in records if r.get("distance_from_company_meters") is not None]
    average_distance_meters = round(sum(distances) / len(distances), 1) if distances else None

    by_date = {}
    for record in records:
        by_date.setdefault(record["date"], []).append(record)

    attendance_trend = []
    late_trend = []
    working_hours_trend = []
    for offset in range(day_count):
        day = (datetime.fromisoformat(start_date).date() + timedelta(days=offset)).isoformat()
        day_records = by_date.get(day, [])
        day_attended = sum(1 for r in day_records if r.get("check_in_time"))
        day_late = sum(1 for r in day_records if r.get("status") == AttendanceStatus.LATE)
        day_durations = []
        for r in day_records:
            d = r.get("working_duration_minutes")
            if d is None:
                d = _duration_minutes(r.get("check_in_time"), r.get("check_out_time"))
            if d is not None:
                day_durations.append(d)
        rate = round(day_attended / total_employees * 100, 1) if total_employees > 0 else 0
        attendance_trend.append({"date": day, "rate": rate})
        late_trend.append({"date": day, "count": day_late})
        working_hours_trend.append({
            "date": day,
            "hours": round(sum(day_durations) / len(day_durations) / 60, 2) if day_durations else 0
        })

    per_employee = {
        e["id"]: {
            "employee_id": e["id"],
            "name": e["name"],
            "department": e.get("department"),
            "days_attended": 0,
            "late_count": 0,
            "total_minutes": 0.0,
            "duration_days": 0,
        }
        for e in employees
    }
    for record in records:
        stats = per_employee.get(record["employee_id"])
        if not stats:
            continue
        if record.get("check_in_time"):
            stats["days_attended"] += 1
        if record.get("status") == AttendanceStatus.LATE:
            stats["late_count"] += 1
        duration = record.get("working_duration_minutes")
        if duration is None:
            duration = _duration_minutes(record.get("check_in_time"), record.get("check_out_time"))
        if duration is not None:
            stats["total_minutes"] += duration
            stats["duration_days"] += 1

    # Department Attendance: same expected/attended ratio as
    # attendance_percentage, scoped to employees in each department. Employees
    # without a department (free-text field, may be unset) are excluded here,
    # matching how the Records tab's department filter already only lists
    # non-empty department values.
    department_totals = {}
    for stats in per_employee.values():
        department = stats["department"]
        if not department:
            continue
        bucket = department_totals.setdefault(department, {"attended": 0, "employee_count": 0})
        bucket["attended"] += stats["days_attended"]
        bucket["employee_count"] += 1
    department_attendance = [
        {
            "department": department,
            "attendance_rate": round(bucket["attended"] / (bucket["employee_count"] * day_count) * 100, 1)
            if bucket["employee_count"] > 0 else 0,
        }
        for department, bucket in sorted(department_totals.items())
    ]

    ranking = []
    for stats in per_employee.values():
        attendance_rate = stats["days_attended"] / day_count * 100
        on_time_rate = ((stats["days_attended"] - stats["late_count"]) / stats["days_attended"] * 100) if stats["days_attended"] > 0 else 0
        score = round(attendance_rate * 0.7 + on_time_rate * 0.3, 1)
        ranking.append({
            "employee_id": stats["employee_id"],
            "name": stats["name"],
            "department": stats["department"],
            "days_attended": stats["days_attended"],
            "late_count": stats["late_count"],
            "avg_working_hours": round(stats["total_minutes"] / stats["duration_days"] / 60, 1) if stats["duration_days"] > 0 else 0,
            "attendance_rate": round(attendance_rate, 1),
            "score": score,
        })
    ranking.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(ranking):
        item["rank"] = index + 1

    return {
        "date_from": start_date,
        "date_to": end_date,
        "total_employees": total_employees,
        # Averages are computed in UTC, consistent with the existing
        # late-after-9-UTC rule used at check-in.
        "average_check_in_time": format_clock(check_in_minutes),
        "average_check_out_time": format_clock(check_out_minutes),
        "average_working_hours": round(sum(durations) / len(durations) / 60, 1) if durations else 0,
        "attendance_percentage": attendance_percentage,
        "late_count": late_count,
        "late_percentage": late_percentage,
        "absence_count": absence_count,
        "average_distance_meters": average_distance_meters,
        "department_attendance": department_attendance,
        "employee_ranking": ranking,
        "charts": {
            "attendance_trend": attendance_trend,
            "late_trend": late_trend,
            "working_hours_trend": working_hours_trend,
        },
    }

ATTENDANCE_EDITABLE_FIELDS = ["check_in_time", "check_out_time", "status"]

@api_router.patch("/owner/attendance/{attendance_id}")
async def edit_attendance(attendance_id: str, updates: AttendanceManualEdit, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    record = await db.attendance.find_one(
        {"id": attendance_id, "company_id": current_user["company_id"]}, {"_id": 0}
    )
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    provided = updates.model_dump(exclude_none=True)
    changes = {k: v for k, v in provided.items() if k in ATTENDANCE_EDITABLE_FIELDS and record.get(k) != v}
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if "status" in changes and changes["status"] not in (AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ABSENT):
        raise HTTPException(status_code=400, detail="Invalid status value")

    now = datetime.now(timezone.utc).isoformat()
    # Append-only audit trail: one entry per changed field. No update/delete
    # path exists for this collection anywhere in the API - audit history is
    # immutable by construction.
    audit_entries = [{
        "id": str(uuid.uuid4()),
        "attendance_id": attendance_id,
        "company_id": current_user["company_id"],
        "edited_by": current_user["id"],
        "edited_by_name": current_user.get("name"),
        "edited_at": now,
        "field": field,
        "old_value": record.get(field),
        "new_value": value,
    } for field, value in changes.items()]

    new_check_in = changes.get("check_in_time", record.get("check_in_time"))
    new_check_out = changes.get("check_out_time", record.get("check_out_time"))
    changes["working_duration_minutes"] = _duration_minutes(new_check_in, new_check_out)

    await db.attendance.update_one({"id": attendance_id}, {"$set": changes})
    await db.attendance_audit_log.insert_many(audit_entries)

    return {"message": "Attendance record updated", "audit_entries": len(audit_entries)}

@api_router.get("/owner/attendance/audit-log")
async def get_attendance_audit_log(current_user: dict = Depends(get_current_user)):
    """Read-only view of attendance_audit_log, written by edit_attendance
    above. No update/delete route exists for this collection anywhere -
    history is immutable by construction, matching the append-only pattern
    already used for the audit trail itself."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    entries = await db.attendance_audit_log.find(
        {"company_id": current_user["company_id"]}, {"_id": 0}
    ).sort("edited_at", -1).limit(200).to_list(200)

    attendance_ids = list({e["attendance_id"] for e in entries})
    records = await db.attendance.find(
        {"id": {"$in": attendance_ids}}, {"_id": 0, "id": 1, "employee_id": 1, "date": 1}
    ).to_list(len(attendance_ids)) if attendance_ids else []
    record_map = {r["id"]: r for r in records}
    employee_ids = list({r["employee_id"] for r in records})
    employees = await db.users.find(
        {"id": {"$in": employee_ids}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(len(employee_ids)) if employee_ids else []
    employee_names = {e["id"]: e["name"] for e in employees}

    for entry in entries:
        record = record_map.get(entry["attendance_id"])
        entry["employee_name"] = employee_names.get(record["employee_id"]) if record else None
        entry["attendance_date"] = record["date"] if record else None

    return entries

@api_router.get("/owner/reports", response_model=List[ReportResponse])
async def get_reports(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    reports = await db.reports.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    
    # Add employee names
    for report in reports:
        employee = await db.users.find_one({"id": report["employee_id"]}, {"_id": 0})
        if employee:
            report["employee_name"] = employee["name"]
    
    return reports

@api_router.get("/owner/departments", response_model=List[DepartmentResponse])
async def get_departments(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    departments = await db.departments.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).to_list(100)
    
    # Add head names
    for dept in departments:
        if dept.get("head_id"):
            head = await db.users.find_one({"id": dept["head_id"]}, {"_id": 0})
            if head:
                dept["head_name"] = head["name"]
    
    return departments

@api_router.post("/owner/departments", response_model=DepartmentResponse)
async def create_department(department: DepartmentCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    dept_doc = {
        "id": str(uuid.uuid4()),
        "company_id": current_user["company_id"],
        "name": department.name,
        "head_id": department.head_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.departments.insert_one(dept_doc)
    
    # Add head name if exists
    if dept_doc.get("head_id"):
        head = await db.users.find_one({"id": dept_doc["head_id"]}, {"_id": 0})
        if head:
            dept_doc["head_name"] = head["name"]
    
    return DepartmentResponse(**dept_doc)

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

async def generate_daily_task_instances(employee_id: str, company_id: str):
    """Self-heal on read: lazily creates today's occurrence (as an ordinary
    tasks row) for every active daily task template assigned to this employee,
    the same no-scheduler approach already used for subscription expiry and
    presence. If the employee never opens their task list on a given day, no
    instance is created for that day - an accepted tradeoff of not running a
    background job."""
    today = datetime.now(timezone.utc).date().isoformat()
    templates = await db.daily_tasks.find(
        {"company_id": company_id, "is_active": True, "assigned_to": employee_id},
        {"_id": 0}
    ).to_list(1000)

    for template in templates:
        existing = await db.tasks.find_one({
            "daily_task_id": template["id"],
            "assigned_to": employee_id,
            "occurrence_date": today
        })
        if existing:
            continue
        await db.tasks.insert_one({
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "assigned_to": employee_id,
            "title": template["title"],
            "description": template["description"],
            "priority": TaskPriority.MEDIUM,
            "status": TaskStatus.NEW,
            "due_date": today,
            "requires_proof": template.get("requires_proof", False),
            "proof_files": [],
            "created_by": template["created_by"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "task_category": TaskCategory.DAILY,
            "daily_task_id": template["id"],
            "occurrence_date": today,
            "execution_time": template.get("execution_time"),
            "started_at": None,
            "seen_at": None,
            "completed_by": None
        })

@api_router.get("/employee/tasks", response_model=List[TaskResponse])
async def get_employee_tasks(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    await generate_daily_task_instances(current_user["id"], current_user["company_id"])

    tasks = await db.tasks.find(
        {"assigned_to": current_user["id"]},
        {"_id": 0}
    ).to_list(1000)

    for task in tasks:
        task["assigned_to_name"] = current_user["name"]
        # Self-heal: the first time a daily/urgent task is returned to the
        # employee, mark it seen - answers "was it opened?" without a
        # separate explicit endpoint.
        if task.get("task_category") in (TaskCategory.URGENT, TaskCategory.DAILY) and task.get("status") == TaskStatus.NEW:
            seen_at = datetime.now(timezone.utc).isoformat()
            await db.tasks.update_one({"id": task["id"]}, {"$set": {"status": TaskStatus.SEEN, "seen_at": seen_at}})
            task["status"] = TaskStatus.SEEN
            task["seen_at"] = seen_at

    return tasks

@api_router.put("/employee/tasks/{task_id}/status")
async def update_task_status(task_id: str, status_data: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    new_status = status_data.get("status")
    update_data = {"status": new_status}
    if new_status == TaskStatus.COMPLETED:
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

    critical_task_started = None
    if new_status == TaskStatus.IN_PROGRESS:
        # Critical Task Alert "Start Task" reuses this generic endpoint
        # instead of the dedicated /start endpoint, so stamp started_at
        # here too (only if not already set) to keep the timeline populated.
        existing = await db.tasks.find_one(
            {"id": task_id, "assigned_to": current_user["id"]}, {"_id": 0}
        )
        if existing:
            if not existing.get("started_at"):
                update_data["started_at"] = datetime.now(timezone.utc).isoformat()
            if existing.get("priority") == TaskPriority.CRITICAL:
                critical_task_started = existing

    result = await db.tasks.update_one(
        {"id": task_id, "assigned_to": current_user["id"]},
        {"$set": update_data}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    if critical_task_started:
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": critical_task_started["created_by"],
            "company_id": current_user["company_id"],
            "type": "critical_task_started",
            "title": "بدأ الموظف العمل على المهمة العاجلة",
            "message": f"بدأ الموظف {current_user['name']} العمل على المهمة العاجلة: {critical_task_started['title']}",
            "read_status": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        })

    return {"message": "Task status updated successfully"}

@api_router.post("/employee/tasks/{task_id}/receive")
async def receive_critical_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """Critical Task Alert 'Receive Task' action - acknowledges the
    full-screen alert without starting work yet. Dedicated endpoint
    (rather than the generic status one) because it's specific to the
    critical-task workflow and has its own validation/notification."""
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    task = await db.tasks.find_one({"id": task_id, "assigned_to": current_user["id"]}, {"_id": 0})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("priority") != TaskPriority.CRITICAL:
        raise HTTPException(status_code=400, detail="Only urgent tasks use the receive workflow")
    if task.get("status") not in (TaskStatus.NEW, TaskStatus.SEEN):
        raise HTTPException(status_code=400, detail=f"Task cannot be received from status {task.get('status')}")

    received_at = datetime.now(timezone.utc).isoformat()
    await db.tasks.update_one(
        {"id": task_id},
        {"$set": {"status": TaskStatus.RECEIVED, "received_at": received_at}}
    )

    await db.notifications.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": task["created_by"],
        "company_id": current_user["company_id"],
        "type": "critical_task_received",
        "title": "تم استلام المهمة العاجلة",
        "message": f"قام الموظف {current_user['name']} باستلام المهمة العاجلة: {task['title']}",
        "read_status": False,
        "created_at": received_at
    })

    return {"message": "Task received successfully", "received_at": received_at}

@api_router.post("/employee/tasks/{task_id}/proof")
async def upload_task_proof(task_id: str, proof: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.tasks.update_one(
        {"id": task_id, "assigned_to": current_user["id"]},
        {"$push": {"proof_files": proof.get("file_url")}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"message": "Proof uploaded successfully"}

@api_router.post("/employee/tasks/{task_id}/start")
async def start_task(task_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    task = await db.tasks.find_one({"id": task_id, "assigned_to": current_user["id"]}, {"_id": 0})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") not in (TaskStatus.NEW, TaskStatus.SEEN):
        raise HTTPException(status_code=400, detail=f"Task cannot be started from status {task.get('status')}")

    started_at = datetime.now(timezone.utc).isoformat()
    await db.tasks.update_one({"id": task_id}, {"$set": {"status": TaskStatus.IN_PROGRESS, "started_at": started_at}})
    return {"message": "Task started successfully", "started_at": started_at}

@api_router.post("/employee/tasks/{task_id}/complete")
async def complete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")

    task = await db.tasks.find_one({"id": task_id, "assigned_to": current_user["id"]}, {"_id": 0})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != TaskStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail=f"Task cannot be completed from status {task.get('status')}")
    if task.get("requires_proof") and not task.get("proof_files"):
        raise HTTPException(status_code=400, detail="Photo proof is required before this task can be completed")

    completed_at = datetime.now(timezone.utc).isoformat()
    await db.tasks.update_one(
        {"id": task_id},
        {"$set": {"status": TaskStatus.COMPLETED, "completed_at": completed_at, "completed_by": current_user["id"]}}
    )
    return {"message": "Task completed successfully", "completed_at": completed_at}

@api_router.post("/employee/attendance/check-in")
async def check_in(data: AttendanceCheckIn, current_user: dict = Depends(get_current_user)):
    company, settings, distance = await validate_qr_attendance(
        current_user, data.qr_code, data.latitude, data.longitude, data.accuracy
    )

    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc)

    # Check if already checked in
    existing = await db.attendance.find_one({
        "employee_id": current_user["id"],
        "date": today
    })

    if existing and existing.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Already checked in today")

    # Determine status (late if after 9 AM)
    hour = now.hour
    attendance_status = AttendanceStatus.LATE if hour >= 9 else AttendanceStatus.PRESENT

    location = {"latitude": data.latitude, "longitude": data.longitude}
    if data.accuracy is not None:
        location["accuracy"] = data.accuracy

    # check_in_time is the attendance event itself; created_at is when the
    # record reached the server. Identical today, but kept separate so a
    # future offline-sync client can submit past events without a schema
    # change. employee_department is snapshotted at creation: history must
    # not follow the employee if they later move to another department.
    attendance_doc = {
        "id": str(uuid.uuid4()),
        "employee_id": current_user["id"],
        "employee_department": current_user.get("department"),
        "company_id": current_user["company_id"],
        "date": today,
        "check_in_time": now.isoformat(),
        "check_out_time": None,
        "check_in_location": location,
        "check_out_location": None,
        "distance_from_company_meters": distance,
        "device_info": data.device_info,
        "created_at": now.isoformat(),
        "status": attendance_status
    }

    if existing:
        await db.attendance.update_one(
            {"id": existing["id"]},
            {"$set": attendance_doc}
        )
    else:
        await db.attendance.insert_one(attendance_doc)

    return {"message": "Checked in successfully", "status": attendance_status}

@api_router.post("/employee/attendance/check-out")
async def check_out(data: AttendanceCheckOut, current_user: dict = Depends(get_current_user)):
    company, settings, distance = await validate_qr_attendance(
        current_user, data.qr_code, data.latitude, data.longitude, data.accuracy
    )

    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc)

    # Check if checked in
    attendance = await db.attendance.find_one({
        "employee_id": current_user["id"],
        "date": today
    })

    if not attendance or not attendance.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Not checked in yet")

    if attendance.get("check_out_time"):
        raise HTTPException(status_code=400, detail="Already checked out")

    location = {"latitude": data.latitude, "longitude": data.longitude}
    if data.accuracy is not None:
        location["accuracy"] = data.accuracy

    check_out_time = now.isoformat()
    await db.attendance.update_one(
        {"id": attendance["id"]},
        {"$set": {
            "check_out_time": check_out_time,
            "check_out_location": location,
            "check_out_distance_meters": distance,
            "working_duration_minutes": _duration_minutes(attendance["check_in_time"], check_out_time)
        }}
    )

    return {"message": "Checked out successfully"}

@api_router.get("/employee/attendance/history", response_model=List[AttendanceResponse])
async def get_attendance_history(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    attendance = await db.attendance.find(
        {"employee_id": current_user["id"]},
        {"_id": 0}
    ).sort("date", -1).limit(30).to_list(30)
    
    for record in attendance:
        record["employee_name"] = current_user["name"]
    
    return attendance

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
async def create_report(report: ReportCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    report_doc = {
        "id": str(uuid.uuid4()),
        "employee_id": current_user["id"],
        "employee_name": current_user["name"],
        "company_id": current_user["company_id"],
        "title": report.title,
        "description": report.description,
        "files": report.files,
        "images": report.images,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.reports.insert_one(report_doc)
    
    return ReportResponse(**report_doc)

@api_router.put("/employee/profile")
async def update_profile(updates: dict, current_user: dict = Depends(get_current_user)):
    # Only allow updating certain fields
    allowed_fields = ["name", "phone", "avatar"]
    filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    
    if not filtered_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    
    result = await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": filtered_updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"message": "Profile updated successfully"}

# ============ Common Routes ============

@api_router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(current_user: dict = Depends(get_current_user)):
    await deliver_due_reminders(current_user["id"])
    await deliver_due_calendar_reminders(current_user["id"])
    notifications = await db.notifications.find(
        {"user_id": current_user["id"]},
        {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)

    return notifications

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.notifications.update_one(
        {"id": notification_id, "user_id": current_user["id"]},
        {"$set": {"read_status": True}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification marked as read"}

@api_router.get("/company/{company_id}/qr")
async def get_company_qr(company_id: str, current_user: dict = Depends(get_current_user)):
    # Previously unauthenticated. The QR now encodes the attendance token, so
    # it must not be publicly harvestable - only the company's own owner (or
    # the platform super admin) may fetch it.
    is_own_company_owner = (
        current_user["role"] == UserRole.COMPANY_OWNER
        and current_user.get("company_id") == company_id
    )
    if current_user["role"] != UserRole.SUPER_ADMIN and not is_own_company_owner:
        raise HTTPException(status_code=403, detail="Access denied")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    await ensure_qr_token(company)
    return {"qr_code": company["qr_code"]}

# ============ Work Messaging Routes ============
# Shared between Company Owner and Employee (both are valid senders/
# recipients of internal work messages); Super Admin has no route in here at
# all, so "must not read company messages" holds by simple omission.

def require_company_member(current_user: dict):
    if current_user["role"] not in (UserRole.COMPANY_OWNER, UserRole.EMPLOYEE):
        raise HTTPException(status_code=403, detail="Access denied")

def paginate_params(page: int, page_size: int) -> tuple:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    return page, page_size

@api_router.post("/messages", response_model=MessageResponse)
async def create_message(data: MessageCreate, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    if data.priority not in (MessagePriority.NORMAL, MessagePriority.IMPORTANT, MessagePriority.URGENT):
        raise HTTPException(status_code=400, detail="Invalid priority")
    if data.confidentiality not in (
        MessageConfidentiality.PUBLIC, MessageConfidentiality.INTERNAL,
        MessageConfidentiality.CONFIDENTIAL, MessageConfidentiality.HIGHLY_CONFIDENTIAL
    ):
        raise HTTPException(status_code=400, detail="Invalid confidentiality level")

    company_id = current_user["company_id"]
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    department_label = None
    employee_ids: List[str] = []
    if not data.is_draft:
        if data.recipient_type not in (MessageRecipientType.EMPLOYEE, MessageRecipientType.DEPARTMENT):
            raise HTTPException(status_code=400, detail="Invalid recipient type")
        employee_ids, department_label = await resolve_recipients(company_id, data.recipient_type, data.recipient_ids)

    message_doc = {
        "id": message_id,
        "reference_number": await next_reference_number(company_id),
        "company_id": company_id,
        "thread_id": message_id,
        "parent_message_id": None,
        "sender_id": current_user["id"],
        "sender_name": current_user.get("name"),
        "subject": data.subject,
        "body": data.body,
        "priority": data.priority,
        "confidentiality": data.confidentiality,
        "tags": data.tags,
        "requires_acknowledgement": data.requires_acknowledgement,
        "completion_required": data.completion_required,
        "expires_at": data.expires_at,
        "recipient_type": data.recipient_type,
        "recipient_ids": data.recipient_ids,
        "recipient_department": department_label,
        "is_draft": data.is_draft,
        "is_forward": False,
        "forwarded_from_id": None,
        "is_pinned": False,
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.messages.insert_one(message_doc)

    if not data.is_draft:
        await deliver_message(message_doc, employee_ids)

    result = (await enrich_messages([message_doc], current_user))[0]
    return MessageResponse(**{k: v for k, v in result.items() if k != "recipient_ids"})

@api_router.patch("/messages/{message_id}", response_model=MessageResponse)
async def update_message(message_id: str, updates: MessageUpdate, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message or message["sender_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")

    # "Sender may edit only before any recipient has Seen it" - checked
    # directly against seen_at rather than the status enum, matching the
    # spec's literal wording. role="recipient" excludes the sender's own
    # row (their own delivered_at/seen_at reflect nothing meaningful here).
    seen_exists = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "role": "recipient", "seen_at": {"$ne": None}}, {"_id": 0, "id": 1}
    )
    if seen_exists:
        raise HTTPException(status_code=400, detail="لا يمكن تعديل الرسالة بعد أن شاهدها أحد المستلمين.")

    changes = updates.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.messages.update_one({"id": message_id}, {"$set": changes})

    updated = await db.messages.find_one({"id": message_id}, {"_id": 0})
    result = (await enrich_messages([updated], current_user))[0]
    return MessageResponse(**{k: v for k, v in result.items() if k != "recipient_ids"})

@api_router.post("/messages/{message_id}/send", response_model=MessageResponse)
async def send_draft(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message or message["sender_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")
    if not message.get("is_draft"):
        raise HTTPException(status_code=400, detail="Message has already been sent")

    employee_ids, department_label = await resolve_recipients(
        message["company_id"], message["recipient_type"], message["recipient_ids"]
    )
    now = datetime.now(timezone.utc).isoformat()
    await db.messages.update_one(
        {"id": message_id},
        {"$set": {"is_draft": False, "recipient_department": department_label, "updated_at": now}}
    )
    message["is_draft"] = False
    await deliver_message(message, employee_ids)

    updated = await db.messages.find_one({"id": message_id}, {"_id": 0})
    result = (await enrich_messages([updated], current_user))[0]
    return MessageResponse(**{k: v for k, v in result.items() if k != "recipient_ids"})

@api_router.post("/messages/{message_id}/reply", response_model=MessageResponse)
async def reply_to_message(message_id: str, data: MessageReply, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    root_or_reply = await get_accessible_message(message_id, current_user)
    thread_id = root_or_reply["thread_id"]

    # Reply-all within the thread: every existing participant row except the
    # person replying. A structured work thread, not a 1:1 DM. Participant
    # rows already cover the original sender too (deliver_message creates a
    # role="sender" row), so this is a single query, not a sender+recipient
    # union like it would need to be if the sender had no row of their own.
    participant_rows = await db.message_recipients.find(
        {"thread_id": thread_id}, {"_id": 0, "recipient_id": 1}
    ).to_list(10000)
    participant_ids = {r["recipient_id"] for r in participant_rows}
    participant_ids.discard(current_user["id"])
    if not participant_ids:
        raise HTTPException(status_code=400, detail="No other participants in this thread")

    root = await db.messages.find_one({"id": thread_id}, {"_id": 0})
    subject = root["subject"] if root else root_or_reply["subject"]
    reply_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    reply_doc = {
        "id": reply_id,
        "reference_number": await next_reference_number(current_user["company_id"]),
        "company_id": current_user["company_id"],
        "thread_id": thread_id,
        "parent_message_id": message_id,
        "sender_id": current_user["id"],
        "sender_name": current_user.get("name"),
        "subject": subject if subject.startswith("Re: ") else f"Re: {subject}",
        "body": data.body,
        "priority": MessagePriority.NORMAL,
        "confidentiality": root_or_reply.get("confidentiality", MessageConfidentiality.INTERNAL),
        "tags": [],
        "requires_acknowledgement": False,
        "completion_required": False,
        "expires_at": None,
        "recipient_type": MessageRecipientType.EMPLOYEE,
        "recipient_ids": list(participant_ids),
        "recipient_department": None,
        "is_draft": False,
        "is_forward": False,
        "forwarded_from_id": None,
        "is_pinned": False,
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.messages.insert_one(reply_doc)
    await notify_reply_participants(reply_doc, thread_id, list(participant_ids))

    result = (await enrich_messages([root or root_or_reply], current_user))[0]
    return MessageResponse(**{k: v for k, v in result.items() if k != "recipient_ids"})

@api_router.post("/messages/{message_id}/forward", response_model=MessageResponse)
async def forward_message(message_id: str, data: MessageCreate, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    original = await get_accessible_message(message_id, current_user)
    if data.recipient_type not in (MessageRecipientType.EMPLOYEE, MessageRecipientType.DEPARTMENT):
        raise HTTPException(status_code=400, detail="Invalid recipient type")

    employee_ids, department_label = await resolve_recipients(
        current_user["company_id"], data.recipient_type, data.recipient_ids
    )
    forward_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    forward_doc = {
        "id": forward_id,
        "reference_number": await next_reference_number(current_user["company_id"]),
        "company_id": current_user["company_id"],
        "thread_id": forward_id,
        "parent_message_id": None,
        "sender_id": current_user["id"],
        "sender_name": current_user.get("name"),
        "subject": original["subject"] if original["subject"].startswith("Fwd: ") else f"Fwd: {original['subject']}",
        "body": f"{data.body}\n\n---\n{original['body']}" if data.body else original["body"],
        "priority": data.priority,
        "confidentiality": original.get("confidentiality", MessageConfidentiality.INTERNAL),
        "tags": original.get("tags", []),
        "requires_acknowledgement": data.requires_acknowledgement,
        "completion_required": data.completion_required,
        "expires_at": data.expires_at,
        "recipient_type": data.recipient_type,
        "recipient_ids": data.recipient_ids,
        "recipient_department": department_label,
        "is_draft": False,
        "is_forward": True,
        "forwarded_from_id": message_id,
        "is_pinned": False,
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.messages.insert_one(forward_doc)

    # Copy attachment metadata+data onto the new message so its access-control
    # boundary (get_accessible_message on forward_id) stays simple - avoids a
    # many-to-many attachment-ownership model for what should stay lightweight.
    original_attachments = await db.message_attachments.find({"message_id": message_id}, {"_id": 0}).to_list(50)
    for att in original_attachments:
        await db.message_attachments.insert_one({
            **att, "id": str(uuid.uuid4()), "message_id": forward_id, "thread_id": forward_id, "uploaded_at": now
        })

    await deliver_message(forward_doc, employee_ids)

    result = (await enrich_messages([forward_doc], current_user))[0]
    return MessageResponse(**{k: v for k, v in result.items() if k != "recipient_ids"})

async def _list_messages(query: dict, current_user: dict, page: int, page_size: int, sort_field: str = "created_at"):
    page, page_size = paginate_params(page, page_size)
    total = await db.messages.count_documents(query)
    docs = await db.messages.find(query, {"_id": 0}).sort([
        ("is_pinned", -1), (sort_field, -1)
    ]).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    enriched = await enrich_messages(docs, current_user)
    items = [MessageResponse(**{k: v for k, v in m.items() if k != "recipient_ids"}) for m in enriched]
    return {"items": items, "total": total, "page": page, "page_size": page_size}

def apply_common_filters(query: dict, subject: Optional[str], priority: Optional[str],
                          tags: Optional[str], date_from: Optional[str], date_to: Optional[str]):
    if subject:
        query["subject"] = {"$regex": subject, "$options": "i"}
    if priority:
        query["priority"] = priority
    if tags:
        query["tags"] = {"$in": [t.strip() for t in tags.split(",") if t.strip()]}
    if date_from or date_to:
        date_query = {}
        if date_from:
            date_query["$gte"] = date_from
        if date_to:
            date_query["$lte"] = date_to + "T23:59:59"
        query["created_at"] = date_query

@api_router.get("/messages/recipients")
async def get_message_recipients(current_user: dict = Depends(get_current_user)):
    """Company-scoped directory for the Work Messages recipient picker,
    accessible to BOTH owners and employees. The compose picker previously
    loaded recipients from the owner-only /owner/employees endpoint, so an
    employee's request 403'd and both the "Specific Employee" and
    "Department" dropdowns rendered empty. This endpoint returns the same
    company's employees plus the distinct department names among them.
    Company isolation is enforced by the company_id filter; the id/department
    values returned are exactly what resolve_recipients accepts at send time."""
    require_company_member(current_user)
    employees = await db.users.find(
        {"company_id": current_user["company_id"], "role": UserRole.EMPLOYEE},
        {"_id": 0, "id": 1, "name": 1, "department": 1, "position": 1}
    ).to_list(1000)
    departments = sorted({e["department"] for e in employees if e.get("department")})
    return {"employees": employees, "departments": departments}

@api_router.get("/messages/inbox")
async def get_inbox(
    current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20,
    subject: Optional[str] = None, priority: Optional[str] = None, tags: Optional[str] = None,
    status: Optional[str] = None, sender: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None, unread_only: bool = False,
):
    """One row per thread, never per reply - resolved via the participant's
    own thread-level message_recipients row, then the corresponding root
    message (whose id always equals its own thread_id). Normal folder view
    is scoped to role="recipient" (correct Inbox semantics - Sent items
    don't belong here). unread_only powers the sidebar badge count instead
    (?unread_only=true&page_size=1, reading just `total`) and deliberately
    drops that role filter, since a new reply on a thread you SENT also
    flips your own (role="sender") row back to unread - the badge should
    reflect that too, not just newly-received messages."""
    require_company_member(current_user)
    recipient_query = {
        "recipient_id": current_user["id"],
        "status": {"$ne": MessageRecipientStatus.ARCHIVED},
    }
    if unread_only:
        recipient_query["is_unread"] = True
    else:
        recipient_query["role"] = "recipient"
    if status:
        recipient_query["status"] = status
    recipient_rows = await db.message_recipients.find(recipient_query, {"_id": 0, "thread_id": 1}).to_list(10000)
    thread_ids = [r["thread_id"] for r in recipient_rows]
    query = {"id": {"$in": thread_ids}, "company_id": current_user["company_id"]}
    if sender:
        query["sender_name"] = {"$regex": sender, "$options": "i"}
    apply_common_filters(query, subject, priority, tags, date_from, date_to)
    return await _list_messages(query, current_user, page, page_size)

@api_router.get("/messages/sent")
async def get_sent(
    current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20,
    subject: Optional[str] = None, priority: Optional[str] = None, tags: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    require_company_member(current_user)
    query = {"sender_id": current_user["id"], "company_id": current_user["company_id"],
              "is_draft": False, "parent_message_id": None}
    apply_common_filters(query, subject, priority, tags, date_from, date_to)
    return await _list_messages(query, current_user, page, page_size)

@api_router.get("/messages/drafts")
async def get_drafts(current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20):
    require_company_member(current_user)
    query = {"sender_id": current_user["id"], "company_id": current_user["company_id"], "is_draft": True}
    return await _list_messages(query, current_user, page, page_size, sort_field="updated_at")

@api_router.get("/messages/starred")
async def get_starred(current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20):
    require_company_member(current_user)
    recipient_rows = await db.message_recipients.find(
        {"recipient_id": current_user["id"], "is_starred": True}, {"_id": 0, "thread_id": 1}
    ).to_list(10000)
    query = {"id": {"$in": [r["thread_id"] for r in recipient_rows]}, "company_id": current_user["company_id"]}
    return await _list_messages(query, current_user, page, page_size)

@api_router.get("/messages/archived")
async def get_archived(current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20):
    require_company_member(current_user)
    recipient_rows = await db.message_recipients.find(
        {"recipient_id": current_user["id"], "status": MessageRecipientStatus.ARCHIVED}, {"_id": 0, "thread_id": 1}
    ).to_list(10000)
    query = {"id": {"$in": [r["thread_id"] for r in recipient_rows]}, "company_id": current_user["company_id"]}
    return await _list_messages(query, current_user, page, page_size, sort_field="created_at")

@api_router.get("/messages/{message_id}")
async def get_message_thread(message_id: str, current_user: dict = Depends(get_current_user)):
    """Read-only: does NOT mark as seen. The frontend calls POST .../open
    right after fetching - opening is a distinct, auditable action, not a
    side effect of a GET, matching this codebase's dedicated-action-endpoint
    convention for anything workflow-meaningful."""
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    thread_id = message["thread_id"]

    thread_messages = await db.messages.find({"thread_id": thread_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    enriched = await enrich_messages(thread_messages, current_user)

    attachments = await db.message_attachments.find(
        {"thread_id": thread_id}, {"_id": 0, "data": 0}
    ).to_list(200)
    attachments_by_message: Dict[str, list] = {}
    for a in attachments:
        attachments_by_message.setdefault(a["message_id"], []).append(a)

    activity = await db.message_activity_log.find({"message_id": {"$in": [m["id"] for m in thread_messages]}}, {"_id": 0}) \
        .sort("created_at", 1).to_list(500)

    thread = [{
        **{k: v for k, v in m.items() if k != "recipient_ids"},
        "attachments": attachments_by_message.get(m["id"], []),
    } for m in enriched]

    return {"thread_id": thread_id, "messages": thread, "activity": activity}

@api_router.post("/messages/{message_id}/open")
async def open_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    recipient = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"]}, {"_id": 0}
    )
    if not recipient:
        return {"message": "Opened"}  # should not happen given get_accessible_message already required a row

    updates = {"is_unread": False}
    if recipient["status"] == MessageRecipientStatus.DELIVERED:
        updates["status"] = MessageRecipientStatus.SEEN
        updates["seen_at"] = datetime.now(timezone.utc).isoformat()
    await db.message_recipients.update_one({"id": recipient["id"]}, {"$set": updates})

    if "seen_at" in updates and recipient["role"] == "recipient":
        await write_message_activity(current_user["company_id"], message_id, current_user, "opened", message["subject"])
    return {"message": "Opened"}

@api_router.post("/messages/{message_id}/accept")
async def accept_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    if message["sender_id"] == current_user["id"]:
        raise HTTPException(status_code=400, detail="A sender cannot accept their own message")
    if not message.get("requires_acknowledgement"):
        raise HTTPException(status_code=400, detail="This message does not require acknowledgement")
    recipient = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"], "role": "recipient"}, {"_id": 0}
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    if recipient["status"] not in (MessageRecipientStatus.DELIVERED, MessageRecipientStatus.SEEN):
        raise HTTPException(status_code=400, detail="Message already acknowledged or beyond that stage")

    now = datetime.now(timezone.utc).isoformat()
    await db.message_recipients.update_one(
        {"id": recipient["id"]},
        {"$set": {"status": MessageRecipientStatus.ACCEPTED, "accepted_at": now,
                   "seen_at": recipient["seen_at"] or now, "is_unread": False}}
    )
    await write_message_activity(current_user["company_id"], message_id, current_user, "accepted", message["subject"])
    return {"message": "Accepted"}

@api_router.post("/messages/{message_id}/complete")
async def complete_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    if message["sender_id"] == current_user["id"]:
        raise HTTPException(status_code=400, detail="A sender cannot complete their own message")
    if not message.get("completion_required"):
        raise HTTPException(status_code=400, detail="This message does not require completion")
    recipient = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"], "role": "recipient"}, {"_id": 0}
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    if recipient["status"] == MessageRecipientStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Already completed")
    if message.get("requires_acknowledgement") and recipient["status"] not in (
        MessageRecipientStatus.ACCEPTED, MessageRecipientStatus.COMPLETED
    ):
        raise HTTPException(status_code=400, detail="This message must be accepted before it can be completed")

    now = datetime.now(timezone.utc).isoformat()
    await db.message_recipients.update_one(
        {"id": recipient["id"]},
        {"$set": {"status": MessageRecipientStatus.COMPLETED, "completed_at": now,
                   "seen_at": recipient["seen_at"] or now, "is_unread": False}}
    )
    await write_message_activity(current_user["company_id"], message_id, current_user, "completed", message["subject"])
    return {"message": "Completed"}

@api_router.post("/messages/{message_id}/archive")
async def archive_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    recipient = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"]}, {"_id": 0}
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")

    # Archive is a personal-view state, never a deletion - the row (and every
    # timestamp on it) is preserved permanently, satisfying "not even by
    # Company Owner" since no delete path exists anywhere for this collection.
    # Works for the sender's own row too, so a sent thread can be archived
    # from Sent exactly like a recipient archives it from Inbox.
    await db.message_recipients.update_one(
        {"id": recipient["id"]},
        {"$set": {"status": MessageRecipientStatus.ARCHIVED, "archived_at": datetime.now(timezone.utc).isoformat()}}
    )
    await write_message_activity(current_user["company_id"], message_id, current_user, "archived", message["subject"])
    return {"message": "Archived"}

@api_router.post("/messages/{message_id}/unread")
async def mark_message_unread(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    result = await db.message_recipients.update_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"]},
        {"$set": {"is_unread": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"message": "Marked as unread"}

@api_router.post("/messages/{message_id}/star")
async def toggle_star_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await get_accessible_message(message_id, current_user)
    recipient = await db.message_recipients.find_one(
        {"thread_id": message["thread_id"], "recipient_id": current_user["id"]}, {"_id": 0}
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    new_value = not recipient.get("is_starred", False)
    await db.message_recipients.update_one({"id": recipient["id"]}, {"$set": {"is_starred": new_value}})
    return {"message": "Starred" if new_value else "Unstarred", "is_starred": new_value}

@api_router.post("/messages/{message_id}/close")
async def close_message(message_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message or message["sender_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.get("closed_at"):
        raise HTTPException(status_code=400, detail="Thread already closed")

    now = datetime.now(timezone.utc).isoformat()
    await db.messages.update_one({"id": message_id}, {"$set": {"closed_at": now, "updated_at": now}})
    await write_message_activity(current_user["company_id"], message_id, current_user, "closed", message["subject"])
    return {"message": "Thread closed"}

@api_router.post("/owner/messages/{message_id}/pin")
async def toggle_pin_message(message_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    new_value = not message.get("is_pinned", False)
    await db.messages.update_one({"id": message_id}, {"$set": {"is_pinned": new_value}})
    return {"message": "Pinned" if new_value else "Unpinned", "is_pinned": new_value}

@api_router.post("/messages/{message_id}/attachments")
async def upload_message_attachment(message_id: str, data: AttachmentUpload, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message or message["sender_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")

    # Same client-side-base64-encode contract as task proof_files (the only
    # upload mechanism this codebase has) - just stored as its own document
    # instead of inline on the parent, so listing messages never has to pull
    # attachment payloads across the wire.
    raw = data.data.split(",", 1)[-1] if data.data.startswith("data:") else data.data
    try:
        decoded_size = len(base64.b64decode(raw, validate=False))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file data")
    if decoded_size > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds the {MAX_ATTACHMENT_BYTES // (1024*1024)}MB limit")

    attachment_id = str(uuid.uuid4())
    await db.message_attachments.insert_one({
        "id": attachment_id,
        "message_id": message_id,
        "thread_id": message["thread_id"],
        "company_id": current_user["company_id"],
        "filename": data.filename,
        "mime_type": data.mime_type,
        "attachment_type": classify_attachment_type(data.mime_type),
        "data": data.data,
        "size_bytes": decoded_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"id": attachment_id, "filename": data.filename, "attachment_type": classify_attachment_type(data.mime_type), "size_bytes": decoded_size}

@api_router.get("/message-attachments/{attachment_id}")
async def get_message_attachment(attachment_id: str, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    attachment = await db.message_attachments.find_one({"id": attachment_id}, {"_id": 0})
    if not attachment or attachment["company_id"] != current_user["company_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found")
    # Lazy-load boundary: the actual base64 payload is only ever returned
    # from this one dedicated endpoint, never from any list/thread response.
    await get_accessible_message(attachment["message_id"], current_user)
    return attachment

@api_router.post("/messages/{message_id}/reminder")
async def create_reminder(message_id: str, data: ReminderCreate, current_user: dict = Depends(get_current_user)):
    require_company_member(current_user)
    await get_accessible_message(message_id, current_user)

    if data.preset:
        if data.preset not in REMINDER_PRESETS:
            raise HTTPException(status_code=400, detail="Invalid reminder preset")
        if data.preset == "tomorrow":
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            remind_at = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        else:
            remind_at = (datetime.now(timezone.utc) + REMINDER_PRESETS[data.preset]).isoformat()
    elif data.remind_at:
        remind_at = data.remind_at
    else:
        raise HTTPException(status_code=400, detail="Provide a preset or an explicit remind_at")

    reminder_id = str(uuid.uuid4())
    await db.message_reminders.insert_one({
        "id": reminder_id,
        "message_id": message_id,
        "user_id": current_user["id"],
        "company_id": current_user["company_id"],
        "remind_at": remind_at,
        "notified_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Reminders notify only the requesting user - never inserted for anyone
    # else, satisfying "reminder only notifies the current user" by construction.
    return {"id": reminder_id, "remind_at": remind_at}

@api_router.get("/owner/messages/communication-center")
async def get_communication_center(
    current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20,
    reference_number: Optional[str] = None, subject: Optional[str] = None, sender: Optional[str] = None,
    recipient: Optional[str] = None, department: Optional[str] = None, tags: Optional[str] = None,
    priority: Optional[str] = None, status: Optional[str] = None, attachment_type: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    """Full company-wide visibility for the Owner only - every filter in
    Part 'Search & Filters' plus 'Owner Visibility'. Confidentiality is
    stored/displayed but does not restrict this view, matching the explicit
    'for future permission expansion' scope."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")

    # Root threads only - a reply never appears as its own row here, matching
    # every other list view. "Replies" is instead the reply_count column.
    query = {"company_id": current_user["company_id"], "parent_message_id": None}
    if reference_number:
        query["reference_number"] = {"$regex": reference_number, "$options": "i"}
    if department:
        query["recipient_department"] = department
    apply_common_filters(query, subject, priority, tags, date_from, date_to)

    if sender:
        query["sender_name"] = {"$regex": sender, "$options": "i"}

    def intersect_ids(existing_query: dict, candidate_ids: set):
        if "id" in existing_query:
            existing_query["id"]["$in"] = [i for i in existing_query["id"]["$in"] if i in candidate_ids]
        else:
            existing_query["id"] = {"$in": list(candidate_ids)}

    status_filters_recipients = bool(status) and status != "closed"
    if recipient or status_filters_recipients:
        recipient_query = {"company_id": current_user["company_id"], "role": "recipient"}
        if recipient:
            recipient_query["recipient_name"] = {"$regex": recipient, "$options": "i"}
        if status_filters_recipients:
            recipient_query["status"] = status
        matching_rows = await db.message_recipients.find(recipient_query, {"_id": 0, "thread_id": 1}).to_list(10000)
        intersect_ids(query, {r["thread_id"] for r in matching_rows})

    if status == "closed":
        query["closed_at"] = {"$ne": None}

    if attachment_type:
        matching_attachments = await db.message_attachments.find(
            {"company_id": current_user["company_id"], "attachment_type": attachment_type}, {"_id": 0, "thread_id": 1}
        ).to_list(10000)
        intersect_ids(query, {a["thread_id"] for a in matching_attachments})

    return await _list_messages(query, current_user, page, page_size)

@api_router.get("/owner/messages/{message_id}/timeline")
async def get_message_timeline(message_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    message = await db.messages.find_one({"id": message_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    thread_message_ids = await db.messages.find(
        {"thread_id": message["thread_id"]}, {"_id": 0, "id": 1}
    ).to_list(1000)
    activity = await db.message_activity_log.find(
        {"message_id": {"$in": [m["id"] for m in thread_message_ids]}}, {"_id": 0}
    ).to_list(500)
    events = [{"timestamp": a["created_at"], "label": a["verb"], "actor_name": a["actor_name"]} for a in activity]

    # "Delivered" is synthesized from message_recipients rather than logged as
    # its own activity entry per recipient - delivery is immediate/automatic
    # today, so this avoids storing a redundant row for every send. The
    # sender's own row is excluded (it isn't a "delivery" to anyone).
    recipients = await db.message_recipients.find(
        {"thread_id": message["thread_id"], "role": "recipient"}, {"_id": 0}
    ).to_list(1000)
    for r in recipients:
        if r.get("delivered_at"):
            events.append({"timestamp": r["delivered_at"], "label": "delivered", "actor_name": r["recipient_name"]})

    events.sort(key=lambda e: e["timestamp"])
    return {
        "message_id": message_id,
        "reference_number": message["reference_number"],
        "delivery_progress": await compute_delivery_progress(message["thread_id"]),
        "events": events,
    }

# ============ Calendar Routes ============
# Fully independent of Work Messaging Routes above - own collections, own
# helpers (duplicated rather than reused where reuse would mean touching
# messaging code), same conventions (dedicated action endpoints, range-bounded
# queries, company isolation, append-only activity log).

def require_calendar_access(current_user: dict):
    if current_user["role"] not in (UserRole.COMPANY_OWNER, UserRole.EMPLOYEE):
        raise HTTPException(status_code=403, detail="Access denied")

def calendar_paginate(page: int, page_size: int) -> tuple:
    return max(page, 1), min(max(page_size, 1), 200)

COMPLETABLE_CATEGORIES = {
    EventCategory.MEETING, EventCategory.TRAINING, EventCategory.TASK_DEADLINE,
    EventCategory.MAINTENANCE, EventCategory.BUSINESS_TRIP,
}
VALID_CATEGORIES = set(EVENT_CATEGORY_DEFAULT_COLORS.keys())
VALID_PRIORITIES = {EventPriority.LOW, EventPriority.NORMAL, EventPriority.HIGH, EventPriority.CRITICAL}
VALID_VISIBILITIES = {EventVisibility.PRIVATE, EventVisibility.DEPARTMENT, EventVisibility.COMPANY, EventVisibility.OWNER_ONLY}
VALID_RECIPIENT_TYPES = {EventRecipientType.OWNER, EventRecipientType.EMPLOYEE, EventRecipientType.DEPARTMENT,
                          EventRecipientType.OWNER_PLUS_EMPLOYEES, EventRecipientType.COMPANY}
VALID_LOCATION_TYPES = {EventLocationType.OFFICE, EventLocationType.CLIENT_SITE, EventLocationType.ONLINE, EventLocationType.OTHER, None}

def compute_display_status(event: dict, occurrence_start: datetime, occurrence_end: datetime) -> str:
    if event.get("status") == "cancelled":
        return "cancelled"
    now = datetime.now(timezone.utc)
    if now < occurrence_start:
        return "scheduled"
    if occurrence_start <= now <= occurrence_end:
        return "in_progress"
    return "completed" if event["category"] in COMPLETABLE_CATEGORIES else "expired"

def validate_event_fields(category: str, priority: str, visibility: str, recipient_type: str, location_type: Optional[str]):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid priority")
    if visibility not in VALID_VISIBILITIES:
        raise HTTPException(status_code=400, detail="Invalid visibility")
    if recipient_type not in VALID_RECIPIENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid recipient type")
    if location_type not in VALID_LOCATION_TYPES:
        raise HTTPException(status_code=400, detail="Invalid location type")

async def build_event_detail(event: dict, current_user: dict) -> dict:
    participants = await db.calendar_event_participants.find({"event_id": event["id"]}, {"_id": 0}).to_list(1000)
    attachment_count = await db.calendar_attachments.count_documents({"event_id": event["id"]})
    occ_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
    occ_end = combine_event_datetime(event["end_date"], event.get("end_time"), event["all_day"], True)
    mine = next((p for p in participants if p["participant_id"] == current_user["id"]), None)
    responded = [p for p in participants if p["attendance_status"] != AttendanceResponseStatus.NO_RESPONSE]
    return {
        **event,
        "display_status": compute_display_status(event, occ_start, occ_end),
        "color": event.get("custom_color") or event.get("default_color"),
        "participants": participants,
        "response_progress": {"total": len(participants), "responded": len(responded)},
        "attachment_count": attachment_count,
        "my_attendance_status": mine["attendance_status"] if mine else None,
        "is_participant": mine is not None,
    }

@api_router.post("/calendar/events")
async def create_calendar_event(data: CalendarEventCreate, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    validate_event_fields(data.category, data.priority, data.visibility, data.recipient_type, data.location_type)

    company_id = current_user["company_id"]
    start_dt = combine_event_datetime(data.start_date, data.start_time, data.all_day, False)
    end_dt = combine_event_datetime(data.end_date, data.end_time, data.all_day, True)
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="End must be on or after start")

    company = await db.companies.find_one({"id": company_id}, {"_id": 0, "owner_id": 1})
    owner_id = company["owner_id"] if company else None
    participant_ids = await resolve_calendar_recipients(company_id, data.recipient_type, data.recipient_ids, owner_id)

    conflicts = await check_calendar_conflicts(company_id, participant_ids, start_dt, end_dt, data.all_day)
    holiday_conflict = None
    if data.category != EventCategory.COMPANY_HOLIDAY:
        holiday_conflict = await check_holiday_conflict(company_id, start_dt, end_dt)
    if (conflicts or holiday_conflict) and not data.override_conflicts:
        raise HTTPException(status_code=409, detail={"message": "Conflict detected", "conflicts": conflicts, "holiday_conflict": holiday_conflict})

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    event_doc = {
        "id": event_id,
        "reference_number": await next_calendar_reference_number(company_id),
        "company_id": company_id,
        "series_id": event_id,
        "title": data.title, "description": data.description, "category": data.category,
        "default_color": EVENT_CATEGORY_DEFAULT_COLORS.get(data.category, "#A1A1AA"),
        "custom_color": data.custom_color, "priority": data.priority,
        "start_date": data.start_date, "start_time": data.start_time,
        "end_date": data.end_date, "end_time": data.end_time, "all_day": data.all_day,
        "location_type": data.location_type, "location": data.location, "online_link": data.online_link,
        "visibility": data.visibility, "recipient_type": data.recipient_type, "recipient_ids": data.recipient_ids,
        "recurrence_type": data.recurrence_type, "recurrence_interval": data.recurrence_interval,
        "recurrence_end_type": data.recurrence_end_type, "recurrence_end_value": data.recurrence_end_value,
        "status": None,
        "linked_thread_id": None,  # set lazily by /open-conversation, never on create
        "meeting_notes": {"summary": "", "decisions": "", "action_items": ""},
        # Enable/disable toggle - only meaningful for category=company_holiday
        # (see /calendar/events/{id}/deactivate|reactivate) but set on every
        # event for schema consistency, mirroring daily_tasks.is_active.
        "is_active": True,
        "created_by": current_user["id"], "created_by_name": current_user.get("name"),
        "created_at": now, "updated_at": now, "updated_by": current_user["id"], "updated_by_name": current_user.get("name"),
    }
    await db.calendar_events.insert_one(event_doc)
    event_doc.pop("_id", None)  # insert_one mutates the dict in place with a non-serializable ObjectId

    for pid in participant_ids:
        p_user = await db.users.find_one({"id": pid}, {"_id": 0, "name": 1, "department": 1})
        await db.calendar_event_participants.insert_one({
            "id": str(uuid.uuid4()), "event_id": event_id, "company_id": company_id,
            "participant_id": pid, "participant_name": p_user["name"] if p_user else None,
            "department": p_user.get("department") if p_user else None,
            "attendance_status": AttendanceResponseStatus.NO_RESPONSE, "responded_at": None,
            # Separate from the pre-meeting RSVP above - set only after the
            # meeting actually happens, via POST .../participants/{id}/attendance.
            "final_attendance": None, "attendance_marked_at": None,
        })

    await write_calendar_activity(company_id, event_id, current_user, "created", data.title)
    # An employee-created event that includes the Owner as a participant
    # gets the Owner notified immediately - the "Secretary/Employee creates
    # a meeting for the Owner" rule. Owner-created events only notify
    # participants (dedup already strips the actor from the recipient set).
    owner_should_be_notified = current_user["role"] == UserRole.EMPLOYEE and owner_id in participant_ids
    await notify_for_calendar_event(
        event_doc, "owner_created" if owner_should_be_notified else "created",
        participant_ids, actor=current_user,
    )

    return await build_event_detail(event_doc, current_user)

# Registered before /calendar/events/{event_id} - FastAPI matches routes in
# registration order, so this static path must come first or "search" would
# be swallowed by the {event_id} path parameter.
@api_router.get("/calendar/events/search")
async def search_calendar_events(
    current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20,
    reference_number: Optional[str] = None, title: Optional[str] = None, participant: Optional[str] = None,
    department: Optional[str] = None, category: Optional[str] = None, priority: Optional[str] = None,
    created_by: Optional[str] = None, status: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
):
    """Search still respects the range-bounded philosophy: defaults to a
    120-day window around today when no explicit range is given, rather
    than ever scanning the full history."""
    require_calendar_access(current_user)
    page, page_size = calendar_paginate(page, page_size)
    today = datetime.now(timezone.utc).date()
    range_from = date_from or (today - timedelta(days=30)).isoformat()
    range_to = date_to or (today + timedelta(days=90)).isoformat()

    query = {"company_id": current_user["company_id"], "start_date": {"$lte": range_to}, "end_date": {"$gte": range_from}}
    if reference_number:
        query["reference_number"] = {"$regex": reference_number, "$options": "i"}
    if title:
        query["title"] = {"$regex": title, "$options": "i"}
    if category:
        query["category"] = category
    if priority:
        query["priority"] = priority
    if created_by:
        query["created_by_name"] = {"$regex": created_by, "$options": "i"}

    if participant or department:
        p_query = {"company_id": current_user["company_id"]}
        if participant:
            p_query["participant_name"] = {"$regex": participant, "$options": "i"}
        if department:
            p_query["department"] = department
        rows = await db.calendar_event_participants.find(p_query, {"_id": 0, "event_id": 1}).to_list(10000)
        query["id"] = {"$in": list({r["event_id"] for r in rows})}

    candidates = await db.calendar_events.find(query, {"_id": 0}).to_list(2000)

    is_owner = current_user["role"] == UserRole.COMPANY_OWNER
    results = []
    for event in candidates:
        if not is_owner and event["created_by"] != current_user["id"]:
            accessible = await db.calendar_event_participants.find_one(
                {"event_id": event["id"], "participant_id": current_user["id"]}, {"_id": 0, "id": 1}
            )
            visibility = event.get("visibility", EventVisibility.COMPANY)
            if not accessible and visibility not in (EventVisibility.COMPANY,) and not (
                visibility == EventVisibility.DEPARTMENT and event.get("department") == current_user.get("department")
            ):
                continue
        occ_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
        occ_end = combine_event_datetime(event["end_date"], event.get("end_time"), event["all_day"], True)
        display_status = compute_display_status(event, occ_start, occ_end)
        if status and status != display_status:
            continue
        results.append({**event, "display_status": display_status, "color": event.get("custom_color") or event.get("default_color")})

    results.sort(key=lambda e: e["start_date"], reverse=True)
    total = len(results)
    start_idx = (page - 1) * page_size
    return {"items": results[start_idx:start_idx + page_size], "total": total, "page": page, "page_size": page_size}

@api_router.get("/calendar/events/{event_id}")
async def get_calendar_event(event_id: str, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    event = await get_accessible_calendar_event(event_id, current_user)
    return await build_event_detail(event, current_user)

@api_router.get("/calendar/events")
async def list_calendar_events(
    current_user: dict = Depends(get_current_user),
    view_start: str = None, view_end: str = None, page: int = 1, page_size: int = 200,
):
    """The one range query every calendar view (Month/Week/Day/Agenda) uses -
    never loads anything outside [view_start, view_end]. Occurrences of
    recurring events are expanded on the fly for this window only, so a
    yearly series created 5 years ago costs nothing extra to display this
    month."""
    require_calendar_access(current_user)
    if not view_start or not view_end:
        raise HTTPException(status_code=400, detail="view_start and view_end are required")
    page, page_size = calendar_paginate(page, page_size)
    range_start = datetime.fromisoformat(f"{view_start}T00:00:00+00:00")
    range_end = datetime.fromisoformat(f"{view_end}T23:59:59+00:00")

    company_id = current_user["company_id"]
    candidate_query = {
        "company_id": company_id,
        "start_date": {"$lte": view_end},
        "$or": [{"recurrence_end_type": {"$ne": RecurrenceEndType.ON_DATE}}, {"recurrence_end_value": {"$gte": view_start}}, {"recurrence_end_value": None}],
    }
    candidates = await db.calendar_events.find(candidate_query, {"_id": 0}).to_list(2000)

    # Visibility + participation filter, then occurrence expansion per event.
    is_owner = current_user["role"] == UserRole.COMPANY_OWNER
    accessible = []
    if candidates:
        candidate_ids = [c["id"] for c in candidates]
        my_rows = await db.calendar_event_participants.find(
            {"event_id": {"$in": candidate_ids}, "participant_id": current_user["id"]}, {"_id": 0, "event_id": 1}
        ).to_list(2000)
        my_event_ids = {r["event_id"] for r in my_rows}
        my_department = current_user.get("department")
        dept_rows = await db.calendar_event_participants.find(
            {"event_id": {"$in": candidate_ids}, "department": my_department}, {"_id": 0, "event_id": 1}
        ).to_list(2000) if my_department else []
        dept_event_ids = {r["event_id"] for r in dept_rows}

        for c in candidates:
            if is_owner or c["created_by"] == current_user["id"] or c["id"] in my_event_ids:
                accessible.append(c)
                continue
            visibility = c.get("visibility", EventVisibility.COMPANY)
            if visibility == EventVisibility.COMPANY:
                accessible.append(c)
            elif visibility == EventVisibility.DEPARTMENT and c["id"] in dept_event_ids:
                accessible.append(c)

    all_occurrences = []
    for event in accessible:
        for occ in await expand_event_occurrences(event, range_start, range_end):
            # occ already carries event fields merged with any exception's
            # override_fields (see expand_event_occurrences) - spreading the
            # original `event` here instead would silently discard "This
            # Event Only" overrides (title/time/etc for just this occurrence).
            all_occurrences.append({
                **{k: v for k, v in occ.items() if k != "recipient_ids"},
                "color": occ.get("custom_color") or occ.get("default_color"),
                "display_status": compute_display_status(occ, datetime.fromisoformat(occ["occurrence_start"]), datetime.fromisoformat(occ["occurrence_end"])),
            })
    all_occurrences.sort(key=lambda o: o["occurrence_start"])

    total = len(all_occurrences)
    start_idx = (page - 1) * page_size
    page_items = all_occurrences[start_idx:start_idx + page_size]
    working_hours = await get_working_hours(company_id)
    return {"items": page_items, "total": total, "page": page, "page_size": page_size, "working_hours": working_hours}

async def get_event_for_edit(event_id: str, current_user: dict) -> dict:
    event = await db.calendar_events.find_one({"id": event_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not event or (event["created_by"] != current_user["id"] and current_user["role"] != UserRole.COMPANY_OWNER):
        raise HTTPException(status_code=404, detail="Event not found")
    return event

async def participant_ids_for_event(event_id: str) -> List[str]:
    rows = await db.calendar_event_participants.find({"event_id": event_id}, {"_id": 0, "participant_id": 1}).to_list(10000)
    return [r["participant_id"] for r in rows]

@api_router.patch("/calendar/events/{event_id}")
async def update_calendar_event(
    event_id: str, updates: CalendarEventUpdate, current_user: dict = Depends(get_current_user),
    scope: str = EventEditScope.ENTIRE_SERIES, occurrence_date: Optional[str] = None,
):
    require_calendar_access(current_user)
    event = await get_event_for_edit(event_id, current_user)
    changes = updates.model_dump(exclude_none=True, exclude={"override_conflicts"})
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    for field in ("category", "priority", "visibility", "location_type"):
        if field in changes and changes[field] not in (VALID_CATEGORIES if field == "category" else
                                                          VALID_PRIORITIES if field == "priority" else
                                                          VALID_VISIBILITIES if field == "visibility" else VALID_LOCATION_TYPES):
            raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if "category" in changes:
        changes["default_color"] = EVENT_CATEGORY_DEFAULT_COLORS.get(changes["category"], "#A1A1AA")

    time_fields_changed = bool({"start_date", "start_time", "end_date", "end_time", "all_day"} & changes.keys())
    location_changed = bool({"location", "location_type", "online_link"} & changes.keys())

    # Re-check conflicts whenever timing changes, using the merged
    # would-be event so participants/other fields already reflect the edit.
    # For this_event_only/this_and_future, the relevant date is the edited
    # occurrence's own date, not the original series' start_date - checking
    # against the original date would flag (or miss) conflicts that have
    # nothing to do with what's actually being scheduled.
    merged_preview = {**event, **changes}
    if scope in (EventEditScope.THIS_EVENT_ONLY, EventEditScope.THIS_AND_FUTURE) and occurrence_date and "start_date" not in changes:
        merged_preview["start_date"] = occurrence_date
        if "end_date" not in changes:
            try:
                span_days = (date.fromisoformat(event["end_date"]) - date.fromisoformat(event["start_date"])).days
            except ValueError:
                span_days = 0
            merged_preview["end_date"] = (date.fromisoformat(occurrence_date) + timedelta(days=span_days)).isoformat()
    if time_fields_changed:
        start_dt = combine_event_datetime(merged_preview["start_date"], merged_preview.get("start_time"), merged_preview["all_day"], False)
        end_dt = combine_event_datetime(merged_preview["end_date"], merged_preview.get("end_time"), merged_preview["all_day"], True)
        if end_dt < start_dt:
            raise HTTPException(status_code=400, detail="End must be on or after start")
        participant_ids = await participant_ids_for_event(event_id)
        conflicts = await check_calendar_conflicts(event["company_id"], participant_ids, start_dt, end_dt, merged_preview["all_day"], exclude_event_id=event_id)
        holiday_conflict = None
        if merged_preview["category"] != EventCategory.COMPANY_HOLIDAY:
            holiday_conflict = await check_holiday_conflict(event["company_id"], start_dt, end_dt)
        if (conflicts or holiday_conflict) and not updates.override_conflicts:
            raise HTTPException(status_code=409, detail={"message": "Conflict detected", "conflicts": conflicts, "holiday_conflict": holiday_conflict})

    now = datetime.now(timezone.utc).isoformat()

    if scope == EventEditScope.THIS_EVENT_ONLY:
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required for this_event_only edits")
        existing = await db.calendar_event_exceptions.find_one({"event_id": event_id, "occurrence_date": occurrence_date}, {"_id": 0, "id": 1})
        if existing:
            await db.calendar_event_exceptions.update_one({"id": existing["id"]}, {"$set": {"override_fields": changes, "is_cancelled": False}})
        else:
            await db.calendar_event_exceptions.insert_one({
                "id": str(uuid.uuid4()), "event_id": event_id, "occurrence_date": occurrence_date,
                "is_cancelled": False, "override_fields": changes, "created_at": now,
            })
        target_event = event

    elif scope == EventEditScope.THIS_AND_FUTURE:
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required for this_and_future edits")
        day_before = (datetime.fromisoformat(f"{occurrence_date}T00:00:00+00:00") - timedelta(days=1)).date().isoformat()
        await db.calendar_events.update_one(
            {"id": event_id},
            {"$set": {"recurrence_end_type": RecurrenceEndType.ON_DATE, "recurrence_end_value": day_before, "updated_at": now}}
        )
        # Preserve the original event's day-span when the edit didn't
        # explicitly move the dates.
        try:
            span_days = (date.fromisoformat(event["end_date"]) - date.fromisoformat(event["start_date"])).days
        except ValueError:
            span_days = 0
        new_start = changes.get("start_date", occurrence_date)
        new_end = changes.get("end_date", (date.fromisoformat(new_start) + timedelta(days=span_days)).isoformat())

        new_recurrence_end_type = event.get("recurrence_end_type", RecurrenceEndType.NEVER)
        new_recurrence_end_value = event.get("recurrence_end_value")
        if new_recurrence_end_type == RecurrenceEndType.AFTER_COUNT and new_recurrence_end_value:
            # The new series continues the SAME overall count budget, not a
            # fresh one - otherwise splitting at the last occurrence of a
            # 4-occurrence series would let the new series run forever
            # (re-granted 4 more from its own start), silently extending a
            # series beyond what was ever actually scheduled.
            cutoff = datetime.fromisoformat(f"{occurrence_date}T00:00:00+00:00")
            occurrences_before = count_raw_occurrences_before(event, cutoff)
            remaining = max(int(new_recurrence_end_value) - occurrences_before, 1)
            new_recurrence_end_value = str(remaining)

        new_event_id = str(uuid.uuid4())
        new_doc = {
            **event, **changes,
            "id": new_event_id,
            "reference_number": await next_calendar_reference_number(event["company_id"]),
            "series_id": event.get("series_id", event_id),
            "start_date": new_start, "end_date": new_end,
            "recurrence_end_type": new_recurrence_end_type,
            "recurrence_end_value": new_recurrence_end_value,
            "status": None,
            # A new series starting from here is a materially different set
            # of future occurrences - it doesn't inherit the old series'
            # conversation link or meeting notes, both of which were about
            # the OLD schedule.
            "linked_thread_id": None,
            "meeting_notes": {"summary": "", "decisions": "", "action_items": ""},
            "created_at": now, "updated_at": now,
            "updated_by": current_user["id"], "updated_by_name": current_user.get("name"),
        }
        await db.calendar_events.insert_one(new_doc)
        new_doc.pop("_id", None)  # insert_one mutates the dict in place with a non-serializable ObjectId
        # The new series document gets its own fresh participant rows
        # (copied from the original, response state reset) rather than
        # migrating the old rows, since attendance responses to the old
        # series shouldn't silently apply to a materially different future.
        for pid in await participant_ids_for_event(event_id):
            p_user = await db.users.find_one({"id": pid}, {"_id": 0, "name": 1, "department": 1})
            await db.calendar_event_participants.insert_one({
                "id": str(uuid.uuid4()), "event_id": new_event_id, "company_id": event["company_id"],
                "participant_id": pid, "participant_name": p_user["name"] if p_user else None,
                "department": p_user.get("department") if p_user else None,
                "attendance_status": AttendanceResponseStatus.NO_RESPONSE, "responded_at": None,
                "final_attendance": None, "attendance_marked_at": None,
            })
        target_event = new_doc

    else:  # ENTIRE_SERIES
        changes["updated_at"] = now
        changes["updated_by"] = current_user["id"]
        changes["updated_by_name"] = current_user.get("name")
        await db.calendar_events.update_one({"id": event_id}, {"$set": changes})
        target_event = {**event, **changes}

    verb = "time_changed" if time_fields_changed else "location_changed" if location_changed else "updated"
    await write_calendar_activity(event["company_id"], event_id, current_user, verb, event["title"])
    participant_ids = await participant_ids_for_event(event_id)
    if participant_ids:
        await notify_for_calendar_event(target_event, verb, participant_ids, actor=current_user)

    return await build_event_detail(target_event, current_user)

@api_router.post("/calendar/events/{event_id}/cancel")
async def cancel_calendar_event(
    event_id: str, current_user: dict = Depends(get_current_user),
    scope: str = EventEditScope.ENTIRE_SERIES, occurrence_date: Optional[str] = None,
):
    """Soft cancel only - status flips to 'cancelled' (entire series) or a
    single occurrence gets an is_cancelled exception. No delete path exists
    anywhere for calendar_events; history is never removed. Cancellation is
    deliberately binary (This Event Only / Entire Series) - unlike editing,
    'this_and_future' is not a meaningful cancellation scope and is rejected
    explicitly rather than silently falling through to a full-series cancel."""
    require_calendar_access(current_user)
    if scope not in (EventEditScope.THIS_EVENT_ONLY, EventEditScope.ENTIRE_SERIES):
        raise HTTPException(status_code=400, detail="scope must be this_event_only or entire_series")
    event = await get_event_for_edit(event_id, current_user)

    if scope == EventEditScope.THIS_EVENT_ONLY:
        if not occurrence_date:
            raise HTTPException(status_code=400, detail="occurrence_date is required")
        existing = await db.calendar_event_exceptions.find_one({"event_id": event_id, "occurrence_date": occurrence_date}, {"_id": 0, "id": 1})
        if existing:
            await db.calendar_event_exceptions.update_one({"id": existing["id"]}, {"$set": {"is_cancelled": True}})
        else:
            await db.calendar_event_exceptions.insert_one({
                "id": str(uuid.uuid4()), "event_id": event_id, "occurrence_date": occurrence_date,
                "is_cancelled": True, "override_fields": None, "created_at": datetime.now(timezone.utc).isoformat(),
            })
    else:
        await db.calendar_events.update_one({"id": event_id}, {"$set": {"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}})

    await write_calendar_activity(event["company_id"], event_id, current_user, "cancelled", event["title"])
    participant_ids = await participant_ids_for_event(event_id)
    if participant_ids:
        await notify_for_calendar_event(event, "cancelled", participant_ids, actor=current_user)
    return {"message": "Event cancelled"}

@api_router.post("/calendar/events/{event_id}/participants/add")
async def add_calendar_participants(event_id: str, body: ParticipantIdsBody, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    event = await get_event_for_edit(event_id, current_user)
    existing_ids = set(await participant_ids_for_event(event_id))
    new_ids = [pid for pid in set(body.participant_ids) if pid not in existing_ids]
    if not new_ids:
        raise HTTPException(status_code=400, detail="No new participants to add")
    valid = await db.users.count_documents({"id": {"$in": new_ids}, "company_id": event["company_id"]})
    if valid != len(new_ids):
        raise HTTPException(status_code=400, detail="One or more participants do not belong to your company")

    for pid in new_ids:
        p_user = await db.users.find_one({"id": pid}, {"_id": 0, "name": 1, "department": 1})
        await db.calendar_event_participants.insert_one({
            "id": str(uuid.uuid4()), "event_id": event_id, "company_id": event["company_id"],
            "participant_id": pid, "participant_name": p_user["name"] if p_user else None,
            "department": p_user.get("department") if p_user else None,
            "attendance_status": AttendanceResponseStatus.NO_RESPONSE, "responded_at": None,
            "final_attendance": None, "attendance_marked_at": None,
        })
    await write_calendar_activity(event["company_id"], event_id, current_user, "participant_added", event["title"])
    await notify_for_calendar_event(event, "created", new_ids, actor=current_user)
    return {"message": "Participants added", "added": new_ids}

@api_router.post("/calendar/events/{event_id}/participants/remove")
async def remove_calendar_participants(event_id: str, body: ParticipantIdsBody, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    event = await get_event_for_edit(event_id, current_user)
    result = await db.calendar_event_participants.delete_many({"event_id": event_id, "participant_id": {"$in": body.participant_ids}})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No matching participants found")
    await write_calendar_activity(event["company_id"], event_id, current_user, "participant_removed", event["title"])
    await notify_for_calendar_event(event, "cancelled", body.participant_ids, actor=current_user)
    return {"message": "Participants removed"}

@api_router.post("/calendar/events/{event_id}/participants/{participant_id}/attendance")
async def mark_final_attendance(event_id: str, participant_id: str, data: FinalAttendanceUpdate, current_user: dict = Depends(get_current_user)):
    """Post-meeting record (Attended/Absent) - independent of the pre-meeting
    RSVP (Accepted/Declined/Tentative) tracked by attendance_status. Owner or
    the event's creator only, and only once the meeting has actually
    finished, per 'After a meeting finishes'."""
    require_calendar_access(current_user)
    if data.status not in (FinalAttendanceStatus.ATTENDED, FinalAttendanceStatus.ABSENT):
        raise HTTPException(status_code=400, detail="Invalid attendance status")
    event = await get_event_for_edit(event_id, current_user)

    occ_end = combine_event_datetime(event["end_date"], event.get("end_time"), event["all_day"], True)
    if datetime.now(timezone.utc) < occ_end:
        raise HTTPException(status_code=400, detail="Cannot mark attendance before the meeting has finished")

    participant = await db.calendar_event_participants.find_one(
        {"event_id": event_id, "participant_id": participant_id}, {"_id": 0, "id": 1}
    )
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found on this event")

    now = datetime.now(timezone.utc).isoformat()
    await db.calendar_event_participants.update_one(
        {"id": participant["id"]},
        {"$set": {"final_attendance": data.status, "attendance_marked_at": now}}
    )
    verb = "marked_attended" if data.status == FinalAttendanceStatus.ATTENDED else "marked_absent"
    await write_calendar_activity(event["company_id"], event_id, current_user, verb, event["title"])
    # No notification - this is an internal record-keeping action, not
    # something the marked participant needs to be alerted about.
    return {"message": "Attendance recorded", "status": data.status}

@api_router.put("/calendar/events/{event_id}/notes")
async def update_meeting_notes(event_id: str, data: MeetingNotesUpdate, current_user: dict = Depends(get_current_user)):
    """Meeting Notes (Summary/Decisions/Action Items) live only on the event
    itself - never referenced from anywhere else, and this endpoint never
    calls notify_for_calendar_event. The activity log entry is reference-only
    (records that notes were updated, never the note content itself)."""
    require_calendar_access(current_user)
    event = await get_event_for_edit(event_id, current_user)

    notes = {"summary": data.summary, "decisions": data.decisions, "action_items": data.action_items}
    await db.calendar_events.update_one(
        {"id": event_id},
        {"$set": {"meeting_notes": notes, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    await write_calendar_activity(event["company_id"], event_id, current_user, "notes_updated", event["title"])
    return {"message": "Meeting notes updated", "meeting_notes": notes}

@api_router.post("/calendar/events/{event_id}/open-conversation")
async def open_conversation(event_id: str, current_user: dict = Depends(get_current_user)):
    """Links a Calendar event to a Work Messages thread by calling the
    existing POST /messages handler directly (in-process function call, not
    a modification of it) - reused exactly the way 'Convert to Task' already
    reuses POST /owner/tasks. Idempotent: once a thread exists for this
    event, every subsequent call returns that same thread instead of
    creating a new one each time."""
    require_calendar_access(current_user)
    event = await get_accessible_calendar_event(event_id, current_user)

    if event.get("linked_thread_id"):
        return {"thread_id": event["linked_thread_id"], "created": False}

    participants = await db.calendar_event_participants.find(
        {"event_id": event_id}, {"_id": 0, "participant_id": 1}
    ).to_list(1000)
    other_ids = list({p["participant_id"] for p in participants if p["participant_id"] != current_user["id"]})
    if not other_ids:
        raise HTTPException(status_code=400, detail="No other participants to start a conversation with")

    message = MessageCreate(
        subject=f"{event['title']} ({event['reference_number']})",
        body=f"محادثة بخصوص الموعد: {event['title']} - {event['start_date']}",
        recipient_type=MessageRecipientType.EMPLOYEE,
        recipient_ids=other_ids,
    )
    # Direct function call into the existing, unmodified Work Messages
    # handler - current_user's own role (owner or employee) already
    # satisfies its own require_company_member check. Returns a
    # MessageResponse Pydantic instance, same as calling the route over HTTP.
    created = await create_message(message, current_user)
    thread_id = created.thread_id

    await db.calendar_events.update_one({"id": event_id}, {"$set": {"linked_thread_id": thread_id}})
    await write_calendar_activity(event["company_id"], event_id, current_user, "conversation_started", event["title"])
    return {"thread_id": thread_id, "created": True}

@api_router.post("/calendar/events/check-conflicts")
async def check_conflicts_endpoint(data: ConflictCheckRequest, current_user: dict = Depends(get_current_user)):
    """Dry-run used by the compose UI before save - never mutates anything."""
    require_calendar_access(current_user)
    company_id = current_user["company_id"]
    company = await db.companies.find_one({"id": company_id}, {"_id": 0, "owner_id": 1})
    owner_id = company["owner_id"] if company else None
    participant_ids = await resolve_calendar_recipients(company_id, data.recipient_type, data.recipient_ids, owner_id)

    start_dt = combine_event_datetime(data.start_date, data.start_time, data.all_day, False)
    end_dt = combine_event_datetime(data.end_date, data.end_time, data.all_day, True)
    conflicts = await check_calendar_conflicts(company_id, participant_ids, start_dt, end_dt, data.all_day, exclude_event_id=data.exclude_event_id)
    holiday_conflict = await check_holiday_conflict(company_id, start_dt, end_dt)
    return {"conflicts": conflicts, "holiday_conflict": holiday_conflict, "has_conflict": bool(conflicts or holiday_conflict)}

@api_router.post("/calendar/events/{event_id}/respond")
async def respond_to_calendar_event(event_id: str, data: EventResponseUpdate, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    if data.status not in (AttendanceResponseStatus.ACCEPTED, AttendanceResponseStatus.DECLINED, AttendanceResponseStatus.TENTATIVE):
        raise HTTPException(status_code=400, detail="Invalid response status")
    event = await get_accessible_calendar_event(event_id, current_user)
    participant = await db.calendar_event_participants.find_one(
        {"event_id": event_id, "participant_id": current_user["id"]}, {"_id": 0}
    )
    if not participant:
        raise HTTPException(status_code=404, detail="You are not a participant of this event")

    now = datetime.now(timezone.utc).isoformat()
    await db.calendar_event_participants.update_one(
        {"id": participant["id"]}, {"$set": {"attendance_status": data.status, "responded_at": now}}
    )
    verb = {"accepted": "accepted", "declined": "declined", "tentative": "tentative"}[data.status]
    await write_calendar_activity(event["company_id"], event_id, current_user, verb, event["title"])
    # "Notify Owner" for every individual response, mirroring the oversight
    # model Communication Center already established for Work Messages -
    # the Owner is notified company-wide, not just for events they organized.
    await notify_for_calendar_event(event, verb, [], actor=current_user, notify_owner_too=True)

    all_participants = await db.calendar_event_participants.find({"event_id": event_id}, {"_id": 0, "attendance_status": 1}).to_list(1000)
    if all_participants and all(p["attendance_status"] != AttendanceResponseStatus.NO_RESPONSE for p in all_participants):
        await notify_for_calendar_event(event, "all_responded", [], actor=current_user, notify_owner_too=True)

    return {"message": "Response recorded", "status": data.status}

@api_router.post("/calendar/events/{event_id}/reminders")
async def create_calendar_reminder(event_id: str, data: EventReminderCreate, current_user: dict = Depends(get_current_user)):
    """Owner may set a reminder on any company event (oversight role) even
    when not a participant; everyone else must be a participant or the
    creator - checked via get_accessible_calendar_event."""
    require_calendar_access(current_user)
    event = await get_accessible_calendar_event(event_id, current_user)

    if data.preset:
        if data.preset not in CALENDAR_REMINDER_PRESETS:
            raise HTTPException(status_code=400, detail="Invalid reminder preset")
        event_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
        remind_at = (event_start - CALENDAR_REMINDER_PRESETS[data.preset]).isoformat()
    elif data.remind_at:
        remind_at = data.remind_at
    else:
        raise HTTPException(status_code=400, detail="Provide a preset or an explicit remind_at")

    reminder_id = str(uuid.uuid4())
    await db.calendar_event_reminders.insert_one({
        "id": reminder_id, "event_id": event_id, "user_id": current_user["id"], "company_id": event["company_id"],
        "remind_at": remind_at, "notified_at": None, "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"id": reminder_id, "remind_at": remind_at}

@api_router.post("/calendar/events/{event_id}/attachments")
async def upload_calendar_attachment(event_id: str, data: AttachmentUpload, current_user: dict = Depends(get_current_user)):
    """Same base64-by-reference architecture as message_attachments (reusing
    the pure classify_attachment_type/MAX_ATTACHMENT_BYTES helpers directly,
    unmodified) - its own collection, so migrating to object storage later
    only means changing what the 'data' field holds, not this shape."""
    require_calendar_access(current_user)
    event = await get_event_for_edit(event_id, current_user)

    raw = data.data.split(",", 1)[-1] if data.data.startswith("data:") else data.data
    try:
        decoded_size = len(base64.b64decode(raw, validate=False))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file data")
    if decoded_size > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds the {MAX_ATTACHMENT_BYTES // (1024*1024)}MB limit")

    attachment_id = str(uuid.uuid4())
    await db.calendar_attachments.insert_one({
        "id": attachment_id, "event_id": event_id, "company_id": event["company_id"],
        "filename": data.filename, "mime_type": data.mime_type,
        "attachment_type": classify_attachment_type(data.mime_type),
        "data": data.data, "size_bytes": decoded_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    })
    await write_calendar_activity(event["company_id"], event_id, current_user, "attachment_added", event["title"])
    participant_ids = await participant_ids_for_event(event_id)
    if participant_ids:
        await notify_for_calendar_event(event, "attachment_added", participant_ids, actor=current_user)
    return {"id": attachment_id, "filename": data.filename, "attachment_type": classify_attachment_type(data.mime_type), "size_bytes": decoded_size}

@api_router.get("/calendar-attachments/{attachment_id}")
async def get_calendar_attachment(attachment_id: str, current_user: dict = Depends(get_current_user)):
    require_calendar_access(current_user)
    attachment = await db.calendar_attachments.find_one({"id": attachment_id}, {"_id": 0})
    if not attachment or attachment["company_id"] != current_user["company_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await get_accessible_calendar_event(attachment["event_id"], current_user)
    return attachment

@api_router.get("/owner/calendar/working-hours")
async def get_company_working_hours(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in (UserRole.COMPANY_OWNER, UserRole.EMPLOYEE):
        raise HTTPException(status_code=403, detail="Access denied")
    return await get_working_hours(current_user["company_id"])

@api_router.put("/owner/calendar/working-hours")
async def update_company_working_hours(data: WorkingHoursUpdate, current_user: dict = Depends(get_current_user)):
    """Owner-only write; stored as one nested field on companies (same
    additive-nested-settings convention as attendance_settings) so it stays
    configurable later without a schema redesign."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.companies.update_one(
        {"id": current_user["company_id"]},
        {"$set": {"working_hours": data.model_dump()}}
    )
    return {"message": "Working hours updated", "working_hours": data.model_dump()}

@api_router.get("/owner/calendar/monitor")
async def calendar_owner_monitor(current_user: dict = Depends(get_current_user), page: int = 1, page_size: int = 20):
    """Full company-wide visibility, mirroring Communication Center's role -
    every event regardless of visibility level, with per-participant
    response detail for oversight."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    page, page_size = calendar_paginate(page, page_size)
    company_id = current_user["company_id"]

    total = await db.calendar_events.count_documents({"company_id": company_id})
    events = await db.calendar_events.find({"company_id": company_id}, {"_id": 0}) \
        .sort("start_date", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)

    event_ids = [e["id"] for e in events]
    participants = await db.calendar_event_participants.find({"event_id": {"$in": event_ids}}, {"_id": 0}).to_list(5000)
    by_event: Dict[str, list] = {}
    for p in participants:
        by_event.setdefault(p["event_id"], []).append(p)

    items = []
    for event in events:
        occ_start = combine_event_datetime(event["start_date"], event.get("start_time"), event["all_day"], False)
        occ_end = combine_event_datetime(event["end_date"], event.get("end_time"), event["all_day"], True)
        items.append({
            **event,
            "display_status": compute_display_status(event, occ_start, occ_end),
            "participants": by_event.get(event["id"], []),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@api_router.get("/owner/calendar/events/{event_id}/activity")
async def get_calendar_event_activity(event_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    event = await db.calendar_events.find_one({"id": event_id, "company_id": current_user["company_id"]}, {"_id": 0, "id": 1})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    activity = await db.calendar_activity_log.find({"event_id": event_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return activity

# ============ Company Holiday Management ============
# Holidays are calendar_events with category=company_holiday - not a
# parallel collection/API. This is a thin, holiday-specific layer over the
# existing event machinery (creation, recurrence, occurrence expansion, the
# activity log) plus the is_active enable/disable toggle.

@api_router.get("/calendar/holidays")
async def list_company_holidays(current_user: dict = Depends(get_current_user)):
    """Flat list of holiday base records (not occurrence-expanded), for the
    Owner's Company Holidays management page - distinct from GET
    /calendar/events, which is bounded to a view range and expands
    recurrence for calendar rendering."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    holidays = await db.calendar_events.find(
        {"company_id": current_user["company_id"], "category": EventCategory.COMPANY_HOLIDAY},
        {"_id": 0}
    ).sort("start_date", -1).to_list(1000)
    return holidays

@api_router.post("/calendar/events/{event_id}/deactivate")
async def deactivate_company_holiday(event_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    event = await db.calendar_events.find_one({"id": event_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["category"] != EventCategory.COMPANY_HOLIDAY:
        raise HTTPException(status_code=400, detail="Only company holidays can be deactivated")
    await db.calendar_events.update_one(
        {"id": event_id},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat(),
                   "updated_by": current_user["id"], "updated_by_name": current_user.get("name")}}
    )
    await write_calendar_activity(event["company_id"], event_id, current_user, "holiday_deactivated", event["title"])
    return {"message": "Holiday deactivated", "is_active": False}

@api_router.post("/calendar/events/{event_id}/reactivate")
async def reactivate_company_holiday(event_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    event = await db.calendar_events.find_one({"id": event_id, "company_id": current_user["company_id"]}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["category"] != EventCategory.COMPANY_HOLIDAY:
        raise HTTPException(status_code=400, detail="Only company holidays can be reactivated")
    await db.calendar_events.update_one(
        {"id": event_id},
        {"$set": {"is_active": True, "updated_at": datetime.now(timezone.utc).isoformat(),
                   "updated_by": current_user["id"], "updated_by_name": current_user.get("name")}}
    )
    await write_calendar_activity(event["company_id"], event_id, current_user, "holiday_reactivated", event["title"])
    return {"message": "Holiday reactivated", "is_active": True}

@api_router.post("/calendar/holidays/weekly-pattern")
async def create_weekly_holiday_pattern(data: WeeklyHolidayPatternCreate, current_user: dict = Depends(get_current_user)):
    """Creates one permanent (never-ending) weekly-recurring company_holiday
    event per selected weekday - e.g. Friday-only, or Friday+Saturday. Reuses
    create_calendar_event directly (in-process function call, same pattern as
    /open-conversation calling into Work Messages) instead of duplicating its
    reference-number/participant/notification/activity-log logic."""
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    weekdays = sorted(set(data.weekdays))
    if not weekdays or any(wd < 0 or wd > 6 for wd in weekdays):
        raise HTTPException(status_code=400, detail="weekdays must be 0-6 (0=Sunday..6=Saturday)")

    created = []
    for wd in weekdays:
        start_date = next_date_for_weekday(wd)
        event_data = CalendarEventCreate(
            title=data.title, description=data.description,
            category=EventCategory.COMPANY_HOLIDAY,
            start_date=start_date, end_date=start_date, all_day=True,
            visibility=EventVisibility.COMPANY, recipient_type=EventRecipientType.COMPANY,
            recurrence_type=RecurrenceType.WEEKLY, recurrence_interval=1,
            recurrence_end_type=RecurrenceEndType.NEVER,
            # A permanent, company-wide weekly holiday is an admin-level
            # schedule decision - it shouldn't be blocked by any one
            # employee's pre-existing meeting on that weekday, unlike a
            # single ad-hoc holiday where overriding a conflict is an
            # explicit, per-event opt-in.
            override_conflicts=True,
        )
        created.append(await create_calendar_event(event_data, current_user))
    return {"message": "Weekly holiday pattern created", "created": created}

# ============ Seed Data (For Testing) ============

@api_router.post("/seed")
async def seed_data(pg: AsyncSession = Depends(get_db)):
    # Postgres-backed (Auth module, migrated): super admin, demo plan, demo
    # company+owner, demo employees. Idempotent via business keys (email /
    # plan name) - see services/seed.py.
    await seed_service.seed_identity_data(pg)

    # Everything below (departments/tasks) is still Mongo-backed and will
    # stay that way until the Employees/Tasks modules migrate. MongoDB has
    # no live instance right now (see migration plan) - this block is
    # expected to hit the existing ServerSelectionTimeoutError -> 503
    # handler on first call. Harmless: it only runs once (guarded by the
    # company_id existence check below), the Postgres portion above already
    # committed by this point, and the test suite's seed fixture doesn't
    # assert on this endpoint's response.
    company_id = "company-001"
    company = await db.companies.find_one({"id": company_id})
    if not company:
        owner_id = "owner-001"

        # Create demo tasks
        tasks = [
            {
                "id": "task-001",
                "company_id": company_id,
                "assigned_to": "emp-001",
                "title": "متابعة العملاء الجدد",
                "description": "التواصل مع قائمة العملاء المحتملين وإرسال العروض",
                "priority": TaskPriority.HIGH,
                "status": TaskStatus.NEW,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
                "requires_proof": False,
                "proof_files": [],
                "created_by": owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None
            },
            {
                "id": "task-002",
                "company_id": company_id,
                "assigned_to": "emp-002",
                "title": "تصميم حملة إعلانية",
                "description": "إنشاء محتوى إبداعي للحملة الإعلانية القادمة",
                "priority": TaskPriority.MEDIUM,
                "status": TaskStatus.IN_PROGRESS,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat(),
                "requires_proof": True,
                "proof_files": [],
                "created_by": owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None
            }
        ]
        await db.tasks.insert_many(tasks)
        
        # Create demo department
        dept_doc = {
            "id": "dept-001",
            "company_id": company_id,
            "name": "المبيعات",
            "head_id": "emp-001",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.departments.insert_one(dept_doc)
    
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