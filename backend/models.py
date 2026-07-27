import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def uuid_pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk_uuid(target: str, nullable: bool = False, deferrable: bool = False, ondelete: str = None, index: bool = False):
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(target, deferrable=deferrable, initially="DEFERRED" if deferrable else None, ondelete=ondelete),
        nullable=nullable,
        index=index,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ============ Identity / tenancy ============

class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    password: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    company_id: Mapped[Optional[uuid.UUID]] = fk_uuid("companies.id", nullable=True, deferrable=True, index=True)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Work Schedule assignment - null means "use the company's default
    # schedule" (see services/schedule_resolution.py). Deliberately not
    # gated by WorkSchedule.is_active: an assigned schedule keeps working
    # for whoever it's assigned to even if it's later hidden from
    # assignment pickers - see WorkSchedule.is_active's own comment.
    schedule_id: Mapped[Optional[uuid.UUID]] = fk_uuid("work_schedules.id", nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    avatar: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Employee CV (Part 1): a single optional attachment per employee - flat
    # columns rather than a child table, since it's strictly 1:1 (unlike
    # task/message/calendar attachments, which are 1:many). Same object
    # storage architecture as every other attachment in this app (metadata
    # here, bytes in the S3-compatible bucket via services/storage.py).
    cv_storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cv_original_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cv_mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cv_file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cv_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cv_uploaded_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    # Profile photo (User Profile & Account Settings) - same object-storage
    # pattern as the CV fields above, deliberately kept separate from the
    # pre-existing, never-populated `avatar` column rather than repurposing
    # its contract for existing/future consumers.
    avatar_storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar_mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('super_admin','company_owner','employee')", name="ck_users_role"),
        # Partial unique: only LIVE rows contend. A soft-deleted user frees
        # their email/phone for reuse, exactly matching the old hard-delete
        # behavior (delete employee -> recreate with the same email works).
        Index("uq_users_email_active", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("uq_users_phone_active", "phone", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )


class Company(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[uuid.UUID] = fk_uuid("users.id", deferrable=True)
    qr_code: Mapped[str] = mapped_column(Text, nullable=False)
    qr_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subscription_status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    subscription_plan_id: Mapped[Optional[uuid.UUID]] = fk_uuid("subscription_plans.id", nullable=True)
    # Historically both plain YYYY-MM-DD and full ISO-datetime strings were
    # written here (Stripe flow vs. admin-set flow) - timestamptz represents
    # both uniformly (a date-only value normalizes to midnight UTC).
    subscription_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    subscription_duration_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subscription_features: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    max_employees: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # attendance_settings, flattened per plan decision #5
    attendance_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    attendance_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    attendance_radius_meters: Mapped[float] = mapped_column(Float, nullable=False, server_default="50.0")
    attendance_qr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    attendance_settings_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attendance_settings_updated_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    attendance_settings_updated_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Deliberately separate from attendance_settings_updated_at above, which
    # is also bumped by plain location/radius/qr_enabled edits - conflating
    # the two would make a radius tweak falsely look like a QR regeneration.
    qr_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # working_hours, flattened per plan decision #5
    working_days: Mapped[List[int]] = mapped_column(ARRAY(Integer), nullable=False, server_default="{0,1,2,3,4}")
    working_hours_start_time: Mapped[str] = mapped_column(String(5), nullable=False, server_default="08:00")
    working_hours_end_time: Mapped[str] = mapped_column(String(5), nullable=False, server_default="17:00")

    __table_args__ = (
        CheckConstraint(
            "subscription_status IN ('active','expired','suspended')", name="ck_companies_subscription_status"
        ),
    )


class SubscriptionPlan(Base, TimestampMixin):
    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    max_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("max_employees > 0", name="ck_plans_max_employees_positive"),
        CheckConstraint("price >= 0", name="ck_plans_price_nonneg"),
        CheckConstraint("duration_months > 0", name="ck_plans_duration_positive"),
    )


# ============ Org structure ============

class Department(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    head_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)

    __table_args__ = (
        # Partial unique (live rows only) - same soft-delete rationale as users.
        Index(
            "uq_departments_company_name_active", "company_id", "name",
            unique=True, postgresql_where=text("deleted_at IS NULL"),
        ),
    )


# ============ Tasks ============

class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    assigned_to: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    due_date: Mapped[Optional[str]] = mapped_column(Date, nullable=True)
    due_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    requires_proof: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    proof_files: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    task_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    alert_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # ON DELETE SET NULL: deleting a daily-task template must leave its
    # already-materialized occurrences in place (existing "delete template
    # only, past occurrences untouched" behavior) - the default RESTRICT
    # would instead make the template undeletable once any occurrence
    # exists, which is not what the old Mongo implementation did (there,
    # occurrences held a plain, unenforced id reference).
    daily_task_id: Mapped[Optional[uuid.UUID]] = fk_uuid("daily_tasks.id", nullable=True, ondelete="SET NULL")
    occurrence_date: Mapped[Optional[str]] = mapped_column(Date, nullable=True)
    execution_date: Mapped[Optional[str]] = mapped_column(Date, nullable=True)
    execution_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Future-activation ("Scheduled Task"): a task created today that stays
    # invisible to its assignee (status='scheduled') until this timestamp,
    # at which point self-heal-on-read flips it to 'new' - same house
    # pattern as message/calendar reminders, no scheduler process. NULL for
    # every ordinary task.
    scheduled_activation_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sequential Workflow: N task rows share one `batch_id`, each carrying
    # its position here (0-based). Only the row at the current position is
    # ever visible to its assignee (status='new'/etc); every later row sits
    # at status='pending_sequence' until the row before it completes, at
    # which point the service layer flips it to 'new' and notifies. NULL
    # for every task not part of a sequential workflow (including the
    # existing parallel "urgent task to N employees" use of batch_id, which
    # never sets this column).
    sequence_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("priority IN ('critical','high','medium','low')", name="ck_tasks_priority"),
        CheckConstraint(
            "status IN ('new','received','seen','in_progress','pending_review','completed','rejected','overdue','cancelled','scheduled','pending_sequence')",
            name="ck_tasks_status",
        ),
        CheckConstraint("task_category IN ('urgent','daily') OR task_category IS NULL", name="ck_tasks_category"),
        # Defense in depth, Layer 4 (see the proof-upload incident report,
        # migration f587f273218b) - `array_position` is Postgres's
        # NULL-aware array search; this only ever rejects a real NULL
        # element, never an empty array.
        CheckConstraint("array_position(proof_files, NULL) IS NULL", name="ck_tasks_proof_files_no_null"),
    )


class DailyTask(Base, TimestampMixin):
    __tablename__ = "daily_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    execution_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    requires_proof: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    recurrence_type: Mapped[str] = mapped_column(String, nullable=False, server_default="daily")
    # Dead field in the current app (always {}), ported as-is - flagged, not removed.
    recurrence_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")


class DailyTaskAssignee(Base):
    """New join table - today assigned_to is an embedded array on daily_tasks."""

    __tablename__ = "daily_task_assignees"

    id: Mapped[uuid.UUID] = uuid_pk()
    # ON DELETE CASCADE: an assignee row has no independent meaning once its
    # template is gone (unlike task occurrences, which stay meaningful on
    # their own) - deleting the template should delete these automatically
    # rather than leaving the delete blocked or requiring the caller to
    # clean them up first.
    daily_task_id: Mapped[uuid.UUID] = fk_uuid("daily_tasks.id", ondelete="CASCADE")
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("daily_task_id", "employee_id", name="uq_daily_task_assignees_task_employee"),
    )


class TaskAttachment(Base):
    """Metadata only - file bytes live in object storage (MinIO/S3)."""

    __tablename__ = "task_attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = fk_uuid("tasks.id")
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attachment_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============ Work Schedules ============

class WorkSchedule(Base, TimestampMixin):
    __tablename__ = "work_schedules"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Already indexed in the DB (ix_work_schedules_company / a partial
    # unique on (company_id, is_default) - see __table_args__ below) - not
    # `index=True` here to avoid a duplicate, differently-named index.
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id")
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    break_start_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    break_end_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    required_hours: Mapped[float] = mapped_column(Float, nullable=False)
    # 0=Sunday..6=Saturday, same convention as Company.working_days.
    working_days: Mapped[List[int]] = mapped_column(ARRAY(Integer), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Gates only future assignment/default pickers - does NOT gate
    # schedule_resolution.get_effective_schedule_for_employee, so an
    # employee already on this schedule (directly or via company default)
    # keeps calculating normally after it's deactivated. The service layer
    # additionally blocks deactivating a schedule that is still the
    # company default or still assigned to any employee (see
    # services/work_schedules.py) - is_active alone is not the enforcement
    # mechanism for "nobody's using this schedule anymore".
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("required_hours > 0", name="ck_work_schedules_required_hours"),
        CheckConstraint("array_length(working_days, 1) > 0", name="ck_work_schedules_working_days_nonempty"),
        # Both already exist in the DB (created outside the declarative
        # model originally) - declared here so autogenerate stops proposing
        # to drop them just because the model didn't know about them.
        Index("ix_work_schedules_company", "company_id"),
        Index("uq_work_schedules_company_default", "company_id", unique=True, postgresql_where=text("is_default = true")),
    )


# ============ Attendance ============

class Attendance(Base, TimestampMixin):
    __tablename__ = "attendance"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    # Denormalized snapshot, intentionally not live-joined - matches existing semantics.
    employee_department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    employee_position: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    date: Mapped[str] = mapped_column(Date, nullable=False)
    check_in_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # check_in_location / check_out_location, flattened (small fixed shape,
    # same rationale as decision #5). accuracy is optional in the request
    # (AttendanceCheckIn/CheckOut.accuracy) and, when present, part of the
    # nested location dict in AttendanceResponse - must be stored to
    # reconstruct that dict on read.
    check_in_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_in_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_in_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_out_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_out_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_out_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    distance_from_company_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_out_distance_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    working_duration_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    device_info: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)

    # Work Schedule snapshot - denormalized at check-in time (same rationale
    # as employee_department above): editing or deleting a schedule later
    # must never rewrite the calculated history of a past attendance
    # record. schedule_id itself is kept only for traceability/debugging,
    # never live-joined for calculation - see services/attendance_calc_engine.py.
    schedule_id: Mapped[Optional[uuid.UUID]] = fk_uuid("work_schedules.id", nullable=True)
    scheduled_start_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    scheduled_end_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    scheduled_break_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    required_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Computed at check-in - knowable immediately from check-in time vs.
    # the snapshot above.
    late_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    early_arrival_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Computed at check-out only - stay null until then, exactly like
    # working_duration_minutes above (never fabricate a "missing hours"
    # figure for a shift still in progress).
    overtime_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    missing_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    early_leave_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('present','late','absent')", name="ck_attendance_status"),
    )


class AttendanceEvent(Base):
    """Audit trail of every QR-scan attempt - success AND failure - kept
    separate from Attendance (which stores only the resolved daily record).
    For troubleshooting: "why didn't my check-in work" needs the failed
    attempts, not just the successful ones. Never live-joined for
    calculation - see services/attendance_calc_engine.py."""
    __tablename__ = "attendance_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Null on a failed attempt - no Attendance row is created/updated then.
    attendance_record_id: Mapped[Optional[uuid.UUID]] = fk_uuid("attendance.id", nullable=True)
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    # Not `index=True` here - already covered by the composite
    # ix_attendance_events_company_date below (company_id is its leftmost
    # column).
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id")
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    event_date: Mapped[str] = mapped_column(Date, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Nullable: None means "not evaluated" (e.g. GPS is never checked once
    # the QR itself has already failed) - a real third state, distinct
    # from True/False.
    qr_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    gps_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("action_type IN ('check_in','check_out')", name="ck_attendance_events_action_type"),
        # Both already exist in the DB (created outside the declarative
        # model originally) - declared here so autogenerate stops proposing
        # to drop them just because the model didn't know about them.
        Index("ix_attendance_events_company_date", "company_id", "event_date"),
        Index("ix_attendance_events_record", "attendance_record_id"),
    )


# ============ Reports ============

class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    employee_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    files: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    images: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")


# ============ Notifications ============

class Notification(Base, TimestampMixin):
    """Single reusable table for every notification in the platform, across
    every role and module (present and future) - every module writes through
    services.notifications.publish(), never this table directly. `category`
    is the broad, extensible grouping used for icon/color/filtering (e.g.
    "tasks", "attendance", "payments"); `type` is the existing fine-grained
    subtype used for per-event copy. `entity_type`/`entity_id` point at the
    record this is about (nullable); `action_url` is the frontend route to
    navigate to on click. `sender_id`/`sender_name` are an optional
    denormalized snapshot of who triggered it, same precedent as
    Report.employee_name / Attendance.employee_department."""
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    company_id: Mapped[Optional[uuid.UUID]] = fk_uuid("companies.id", nullable=True, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, server_default="system")
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read_status: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    entity_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    action_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sender_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", "read_status"),
    )


# ============ Work Messaging ============

class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    reference_number: Mapped[str] = mapped_column(String, nullable=False)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_message_id: Mapped[Optional[uuid.UUID]] = fk_uuid("messages.id", nullable=True)
    forwarded_from_id: Mapped[Optional[uuid.UUID]] = fk_uuid("messages.id", nullable=True)
    sender_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default="normal")
    confidentiality: Mapped[str] = mapped_column(String, nullable=False, server_default="internal")
    # PG_ARRAY (not generic ARRAY) - the postgres-specific overlap()/contains()
    # comparators used by MessageQuery.tags_overlap() only exist on this type.
    tags: Mapped[List[str]] = mapped_column(PG_ARRAY(String), nullable=False, server_default="{}")
    requires_acknowledgement: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    completion_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recipient_type: Mapped[str] = mapped_column(String, nullable=False)
    # Polymorphic by design (matches the original request contract): a list
    # of employee-id strings when recipient_type='employee', but a single
    # department NAME string when recipient_type='department' - send_draft
    # re-resolves recipients from this later using recipient_type as the
    # discriminator. A UUID-typed array (the original decision here) cannot
    # hold a department name, so this is a plain string array - the request/
    # response contract never exposes this field directly either way.
    recipient_ids: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    recipient_department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_forward: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("company_id", "reference_number", name="uq_messages_company_reference"),
        CheckConstraint("priority IN ('normal','important','urgent')", name="ck_messages_priority"),
        CheckConstraint(
            "confidentiality IN ('public','internal','confidential','highly_confidential')",
            name="ck_messages_confidentiality",
        ),
        CheckConstraint("recipient_type IN ('employee','department')", name="ck_messages_recipient_type"),
    )


class MessageRecipient(Base):
    __tablename__ = "message_recipients"

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    recipient_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    recipient_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="delivered")
    is_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_starred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("thread_id", "recipient_id", name="uq_message_recipients_thread_recipient"),
        CheckConstraint("role IN ('sender','recipient')", name="ck_message_recipients_role"),
        CheckConstraint(
            "status IN ('delivered','seen','accepted','completed','archived')", name="ck_message_recipients_status"
        ),
    )


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = fk_uuid("messages.id")
    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attachment_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageReminder(Base):
    __tablename__ = "message_reminders"

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = fk_uuid("messages.id")
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============ Calendar ============

class CalendarEvent(Base, TimestampMixin):
    __tablename__ = "calendar_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    reference_number: Mapped[str] = mapped_column(String, nullable=False)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    series_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    category: Mapped[str] = mapped_column(String, nullable=False)
    default_color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    custom_color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default="normal")
    start_date: Mapped[str] = mapped_column(Date, nullable=False)
    start_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    end_date: Mapped[str] = mapped_column(Date, nullable=False)
    end_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    location_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    online_link: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    visibility: Mapped[str] = mapped_column(String, nullable=False, server_default="company")
    recipient_type: Mapped[str] = mapped_column(String, nullable=False, server_default="owner")
    # Polymorphic by design (same pattern as Message.recipient_ids, see
    # Module 8's migration): a list of employee-id strings for
    # employee/owner_plus_employees, or department-name strings for
    # department - never a UUID-only column.
    recipient_ids: Mapped[Optional[List[str]]] = mapped_column(PG_ARRAY(String), nullable=True)
    recurrence_type: Mapped[str] = mapped_column(String, nullable=False, server_default="none")
    recurrence_interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    recurrence_end_type: Mapped[str] = mapped_column(String, nullable=False, server_default="never")
    # Genuinely polymorphic (ISO date string OR stringified count) - flagged, not split. See decision #9.
    recurrence_end_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    linked_thread_id: Mapped[Optional[uuid.UUID]] = fk_uuid("messages.id", nullable=True)

    # meeting_notes, flattened per plan decision #5
    meeting_notes_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meeting_notes_decisions: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meeting_notes_action_items: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    created_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    updated_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("company_id", "reference_number", name="uq_calendar_events_company_reference"),
        CheckConstraint("priority IN ('low','normal','high','critical')", name="ck_calendar_events_priority"),
        CheckConstraint(
            "visibility IN ('private','department','company','owner_only')", name="ck_calendar_events_visibility"
        ),
        CheckConstraint(
            "recipient_type IN ('owner','employee','department','owner_plus_employees','company')",
            name="ck_calendar_events_recipient_type",
        ),
        CheckConstraint(
            "recurrence_type IN ('none','daily','weekly','monthly','yearly','custom')",
            name="ck_calendar_events_recurrence_type",
        ),
        CheckConstraint(
            "recurrence_end_type IN ('never','after_count','on_date')", name="ck_calendar_events_recurrence_end_type"
        ),
    )


class CalendarEventParticipant(Base):
    __tablename__ = "calendar_event_participants"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = fk_uuid("calendar_events.id")
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    participant_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    participant_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attendance_status: Mapped[str] = mapped_column(String, nullable=False, server_default="no_response")
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    final_attendance: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attendance_marked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "participant_id", name="uq_calendar_event_participants_event_participant"),
        CheckConstraint(
            "attendance_status IN ('no_response','accepted','declined','tentative')",
            name="ck_calendar_participants_attendance_status",
        ),
        CheckConstraint(
            "final_attendance IN ('attended','absent') OR final_attendance IS NULL",
            name="ck_calendar_participants_final_attendance",
        ),
    )


class CalendarEventException(Base):
    __tablename__ = "calendar_event_exceptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = fk_uuid("calendar_events.id")
    occurrence_date: Mapped[str] = mapped_column(Date, nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    override_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("event_id", "occurrence_date", name="uq_calendar_exceptions_event_date"),
    )


class CalendarAttachment(Base):
    __tablename__ = "calendar_attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = fk_uuid("calendar_events.id")
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attachment_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalendarEventReminder(Base):
    __tablename__ = "calendar_event_reminders"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = fk_uuid("calendar_events.id")
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============ Sequences ============

class Counter(Base):
    """Atomic per-key reference-number sequences (message_seq:<company_id>,
    calendar_seq:<company_id>). Incremented via UPDATE ... RETURNING inside a
    transaction, giving the same atomicity as Mongo's find_one_and_update."""

    __tablename__ = "counters"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


# ============ Payments ============

class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", index=True)
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    plan_id: Mapped[uuid.UUID] = fk_uuid("subscription_plans.id")
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    transaction_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    payment_status: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)


# ============ Unified audit log ============

class AuditLog(Base):
    """Replaces attendance_audit_log / message_activity_log /
    calendar_activity_log as physical storage. Their existing response
    endpoints reconstruct their exact original shape from this table at the
    response-building layer (see repositories/audit.py) - see plan decision
    on refinement #3 for the field-vs-action granularity reconciliation."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    user_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    old_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============ Dashboard customization ============

class DashboardLayout(Base, TimestampMixin):
    """One row per user. `layout` is a JSONB array of
    {"key": str, "visible": bool, "order": int} - one entry per widget the
    user has ever seen. Deliberately schemaless on the widget-identity side
    (no FK, no CHECK, no fixed enum of keys): the frontend owns the set of
    valid widget keys in its own registry, so shipping a brand-new widget is
    a frontend-only change - any key not yet present in a user's saved
    layout is simply appended as visible/default-ordered by the frontend at
    render time, no migration or backend change required."""

    __tablename__ = "dashboard_layouts"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", nullable=False)
    layout: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_dashboard_layouts_user"),
    )


# ============ Push notifications ============

class DeviceToken(Base, TimestampMixin):
    """One row per physical app install, not per user - `token` (the FCM
    registration token) is unique platform-wide, so a device re-registering
    under a different account (logout/login as someone else on the same
    phone) reassigns `user_id` on the existing row via upsert-by-token
    rather than creating a duplicate (see repositories/device_tokens.py's
    `upsert()`). Deliberately hard-deleted (no SoftDeleteMixin) on explicit
    unregister and on FCM reporting the token as unregistered/invalid -
    unlike Tasks/Companies/etc., a dead push token has no audit-trail value
    once it can no longer receive anything. `last_seen_at` is bumped on
    every register/refresh call, independent of `updated_at` (which only
    changes when a mapped column's value actually changes), so it stays a
    reliable "last confirmed live" signal even when nothing else changed."""

    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    app_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
        Index("ix_device_tokens_user", "user_id"),
        CheckConstraint("platform IN ('ios', 'android', 'web')", name="ck_device_tokens_platform"),
    )


class RefreshToken(Base):
    """One row per issued refresh token - only the sha256 hash is stored,
    never the raw token, so a DB leak alone can't be used to mint
    sessions. Rotated on every use (`services/auth.rotate_refresh_token`):
    presenting a token marks it `revoked_at` and issues a new row linked
    via `replaced_by_id`, so the chain is auditable. Presenting an
    already-revoked token is treated as a signal of token theft/reuse -
    the whole chain for that user is revoked defensively."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id", ondelete="CASCADE", index=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[Optional[uuid.UUID]] = fk_uuid("refresh_tokens.id", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )


# ============ Announcements ============

class Announcement(Base, TimestampMixin):
    """Deliberately minimal - a company-wide broadcast message, not a full
    CMS entry (no attachments, no scheduling, no per-employee read state).
    Publishing one fans out a real notification (category='announcements')
    to every employee in the company via the existing notification
    framework - see services/announcements.py."""

    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", index=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    created_by_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
