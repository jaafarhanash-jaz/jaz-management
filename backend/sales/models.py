"""Tables of the internal JAZ Sales module.

Phase 1 - RBAC for internal staff (staff_permissions, staff_roles, staff_role_permissions, staff_user_roles) and
the append-only staff_audit_events trail. `module` is a column, not part of the table names, so later internal
modules (e.g. Support) can reuse the same role/permission machinery without a redesign.
Mirrors migrations/versions/bb595f6d8dae_sales_rbac_foundation.py (the four RBAC tables) and
2d916a1a993e_sales_audit_trail_and_revocation_integrity.py (the revocation CHECK and the audit table).

Phase 2 - leads (sales_lead_sources, sales_campaigns, sales_leads, sales_lead_activities), mirroring
migrations/versions/d764d5f44e91_sales_leads_foundation.py.

Phase 3 - the work done on a lead (sales_calls, sales_followups, sales_demos, sales_trials), mirroring
migrations/versions/e3a9c5b07d12_sales_activities.py. Their history lives in the Phase-2 timeline
(sales_lead_activities), which gains new event types but no new columns.

Phase 4 - what a won lead becomes (sales_customers: lead <-> JAZ company) and the onboarding workflow of that
customer (sales_onboarding), mirroring migrations/versions/f4c8a1d92b63_sales_customers_onboarding.py. Again the
history lives in the Phase-2 timeline.

Phase 5 - dashboard and reports add no table: they aggregate the tables above. Only three reporting indexes (marked "Phase 5"
below) and two permissions were added, by migrations/versions/b6d1f3a8c294_sales_dashboard_reports.py.

Simplified workflow - the Sales Manager's settings (sales_settings: automatic lead distribution), what a customer setup
started the company on (sales_customers.subscription_type) and the "latest leads I created" index, mirroring
migrations/versions/a3f8c2d7e915_sales_simplified_workflow.py.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models import TimestampMixin, fk_uuid, uuid_pk  # core helpers, imported read-only
from sales.constants import (
    ATTEMPT_RESULTS,
    BATCH_ATTEMPT_KINDS,
    CALL_RESULTS,
    CAMPAIGN_STATUSES,
    CUSTOMER_STATUSES,
    DATA_BATCH_SIZE,
    DATA_BATCH_STATUSES,
    DEMO_STATUSES,
    DISTRIBUTION_MODES,
    FIRST_OUTCOMES,
    FOLLOWUP_STATUSES,
    LOST_REASONS,
    MAX_CALL_SECONDS,
    ONBOARDING_STAGES,
    PIPELINE_STAGES,
    PRIORITIES,
    SUBSCRIPTION_TYPES,
    TRIAL_STATUSES,
    WORK_BATCH_STATUSES,
)


class StaffPermission(Base):
    """Catalog of permission keys (e.g. 'sales.leads.assign'). The canonical
    list also lives in code (sales/permissions.py); this table gives role
    grants referential integrity."""

    __tablename__ = "staff_permissions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    module: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffRole(Base, TimestampMixin):
    """A named bundle of permissions. Never hard-deleted by the app - retire
    with is_active=False. Roles are data: nothing is hard-coded per person."""

    __tablename__ = "staff_roles"

    id: Mapped[uuid.UUID] = uuid_pk()
    module: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    name_en: Mapped[str] = mapped_column(String, nullable=False)
    name_ar: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (UniqueConstraint("key", name="uq_staff_roles_key"),)


class StaffRolePermission(Base):
    __tablename__ = "staff_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("staff_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_key: Mapped[str] = mapped_column(
        String, ForeignKey("staff_permissions.key", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_staff_role_permissions_permission_key", "permission_key"),)


class StaffUserRole(Base):
    """One row per grant. Revoking sets revoked_at/revoked_by - rows are never
    deleted, so the full grant history is preserved. The partial unique index
    allows one ACTIVE grant per (user, role) and makes granting idempotent and
    race-safe (INSERT ... ON CONFLICT DO NOTHING). Both timestamps are written
    by the database (granted_at = now(), revoked_at = clock_timestamp()) so
    app/DB clock skew - or a revoke whose transaction started before the grant
    committed - can never violate the CHECK."""

    __tablename__ = "staff_user_roles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    role_id: Mapped[uuid.UUID] = fk_uuid("staff_roles.id", ondelete="RESTRICT")
    granted_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)

    __table_args__ = (
        CheckConstraint("revoked_at IS NULL OR revoked_at >= granted_at", name="ck_staff_user_roles_revoked_after_granted"),
        # A revocation always says who did it: the two columns are set together or not at all.
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL) OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_staff_user_roles_revoked_pair",
        ),
        Index("uq_staff_user_roles_active", "user_id", "role_id", unique=True, postgresql_where=text("revoked_at IS NULL")),
        Index("ix_staff_user_roles_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
        Index("ix_staff_user_roles_role_id", "role_id"),
    )


class StaffAuditEvent(Base):
    """Append-only security/admin audit trail for internal staff (mirrors migration 2d916a1a993e).

    One row per security-relevant action: who (actor_*), did what (action/outcome), to which entity
    (target_*), with before/after values drawn from per-action ALLOWLISTS - never a password, hash or token
    (see sales/services/audit.py). Written in the SAME transaction as the change it records, so the two
    commit or roll back together. UPDATE, DELETE and TRUNCATE are rejected by triggers.

    Deliberately separate from the company-bound core `audit_logs` (its company_id is NOT NULL and its rows
    are tenant data); `module` lets later internal modules share the table. `seq` is a monotonic cursor for
    keyset pagination / export watermarks; `correlation_id` groups the events of one request."""

    __tablename__ = "staff_audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False)
    module: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'success'"))
    actor_type: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'user'"))
    actor_user_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    actor_platform_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)  # polymorphic soft reference
    target_user_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    before_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # `metadata` is reserved on declarative classes, hence the attribute name.
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    correlation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("seq", name="uq_staff_audit_events_seq"),
        CheckConstraint("module ~ '^[a-z][a-z0-9_]{1,31}$'", name="ck_staff_audit_events_module"),
        CheckConstraint("action ~ '^[a-z][a-z0-9_]{2,63}$'", name="ck_staff_audit_events_action"),
        CheckConstraint("target_type ~ '^[a-z][a-z0-9_]{1,63}$'", name="ck_staff_audit_events_target_type"),
        CheckConstraint("outcome IN ('success','denied','failed')", name="ck_staff_audit_events_outcome"),
        CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL) OR (actor_type = 'system' AND actor_user_id IS NULL)",
            name="ck_staff_audit_events_actor",
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object' AND pg_column_size(metadata) <= 8192", name="ck_staff_audit_events_metadata"),
        CheckConstraint("user_agent IS NULL OR length(user_agent) <= 512", name="ck_staff_audit_events_ua_len"),
        Index("ix_staff_audit_events_target_user", "target_user_id", text("seq DESC"), postgresql_where=text("target_user_id IS NOT NULL")),
        Index("ix_staff_audit_events_actor", "actor_user_id", text("seq DESC"), postgresql_where=text("actor_user_id IS NOT NULL")),
        Index("ix_staff_audit_events_target", "target_type", "target_id", text("seq DESC"), postgresql_where=text("target_id IS NOT NULL")),
        Index("ix_staff_audit_events_action", "module", "action", text("seq DESC")),
        Index("ix_staff_audit_events_occurred_at", "occurred_at"),
    )


# =============================================================================
# Phase 2 - leads
# =============================================================================
def _in_list(values) -> str:
    """SQL literal list for a CHECK constraint: ('a','b',...)  (values are code constants, never user input)."""
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class SalesLeadSource(Base):
    """The standard lead sources. Reference data: seeded by the migration, read-only through the API.
    A lead (and a campaign) stores the source KEY, so values stay consistent and a typo cannot become a new
    source. `is_active` lets a source be retired later without deleting rows that reference it."""

    __tablename__ = "sales_lead_sources"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    name_en: Mapped[str] = mapped_column(String, nullable=False)
    name_ar: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalesCampaign(Base, TimestampMixin):
    """A sales campaign. Leads may optionally belong to one. The FK from sales_leads is RESTRICT, so a campaign
    that still has leads can never be deleted (the API answers 409) - deleting a campaign cannot take leads
    with it, silently or otherwise."""

    __tablename__ = "sales_campaigns"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("sales_lead_sources.key", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'active'"))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")

    __table_args__ = (
        CheckConstraint(f"status IN {_in_list(CAMPAIGN_STATUSES)}", name="ck_sales_campaigns_status"),
        CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date", name="ck_sales_campaigns_dates"
        ),
        CheckConstraint("btrim(name) <> ''", name="ck_sales_campaigns_name"),
        # Case/whitespace-insensitive: "Ramadan Promo" and " ramadan promo" are the same campaign.
        Index("uq_sales_campaigns_name", text("lower(btrim(name))"), unique=True),
        Index("ix_sales_campaigns_status", "status"),
        Index("ix_sales_campaigns_source", "source", postgresql_where=text("source IS NOT NULL")),
        Index("ix_sales_campaigns_created_by", "created_by"),
    )


class SalesLead(Base, TimestampMixin):
    """A sales lead. Never hard-deleted by the application: `archived_at` hides it, and its activity history
    (sales_lead_activities, FK RESTRICT) can never be orphaned.

    The *_norm columns are derived from the raw values by sales/services/normalize.py and maintained ONLY by
    sales/repositories/leads.py::apply_fields - they exist so duplicate detection is an indexed equality
    lookup. The raw values are what is displayed and edited; the *_norm values are never shown."""

    __tablename__ = "sales_leads"

    id: Mapped[uuid.UUID] = uuid_pk()

    # identity / business
    business_name: Mapped[str] = mapped_column(String, nullable=False)
    business_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # contact
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_position: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    whatsapp: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # location
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # sales
    source: Mapped[str] = mapped_column(
        String, ForeignKey("sales_lead_sources.key", ondelete="RESTRICT"), nullable=False,
        server_default=text("'manual_entry'"),
    )
    campaign_id: Mapped[Optional[uuid.UUID]] = fk_uuid("sales_campaigns.id", nullable=True, ondelete="RESTRICT")
    pipeline_stage: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'new'"))
    lost_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    assigned_to: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'medium'"))
    estimated_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # archive (the application's "delete")
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)

    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    # The Data Batch this lead entered (f2b6d8a1c4e9): set once when Data Entry staff create it, never moved (a database trigger
    # refuses any change, and any change of created_by). NULL for a lead nobody entered through Data Entry. Invisible to every
    # role but the Sales Manager (never part of a lead's API shape).
    data_batch_id: Mapped[Optional[uuid.UUID]] = fk_uuid("sales_data_batches.id", nullable=True, ondelete="RESTRICT")

    # normalized copies for duplicate detection (see the class docstring)
    name_norm: Mapped[str] = mapped_column(String, nullable=False)
    city_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    whatsapp_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    website_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        # closed sets - the migration freezes the same values as literals (sales/constants.py is the code-side copy)
        CheckConstraint(f"pipeline_stage IN {_in_list(PIPELINE_STAGES)}", name="ck_sales_leads_stage"),
        CheckConstraint(f"priority IN {_in_list(PRIORITIES)}", name="ck_sales_leads_priority"),
        CheckConstraint(f"lost_reason IS NULL OR lost_reason IN {_in_list(LOST_REASONS)}", name="ck_sales_leads_lost_reason"),
        # A lost lead always says why, and nothing else carries a lost reason.
        CheckConstraint("(pipeline_stage = 'lost') = (lost_reason IS NOT NULL)", name="ck_sales_leads_lost_reason_pair"),
        # closed_at is set exactly while the lead is won or lost.
        CheckConstraint("(pipeline_stage IN ('won','lost')) = (closed_at IS NOT NULL)", name="ck_sales_leads_closed_at_pair"),
        # An owner always has an assignment time, and vice versa; an archive always says who did it.
        CheckConstraint("(assigned_to IS NULL) = (assigned_at IS NULL)", name="ck_sales_leads_assigned_pair"),
        CheckConstraint("(archived_at IS NULL) = (archived_by IS NULL)", name="ck_sales_leads_archived_pair"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_sales_leads_coords_pair"),
        CheckConstraint(
            "(latitude IS NULL OR latitude BETWEEN -90 AND 90) AND (longitude IS NULL OR longitude BETWEEN -180 AND 180)",
            name="ck_sales_leads_coords_range",
        ),
        CheckConstraint("estimated_value IS NULL OR estimated_value >= 0", name="ck_sales_leads_value"),
        CheckConstraint("btrim(business_name) <> ''", name="ck_sales_leads_name"),
        # -- listing: default order, per-owner / per-stage views, the unassigned intake pool
        Index("ix_sales_leads_created_at", text("created_at DESC"), text("id DESC"), postgresql_where=text("archived_at IS NULL")),
        Index("ix_sales_leads_assigned_stage", "assigned_to", "pipeline_stage", postgresql_where=text("archived_at IS NULL")),
        Index("ix_sales_leads_stage", "pipeline_stage", postgresql_where=text("archived_at IS NULL")),
        Index("ix_sales_leads_unassigned", "created_at", postgresql_where=text("assigned_to IS NULL AND archived_at IS NULL")),
        Index("ix_sales_leads_created_by", "created_by"),
        Index("ix_sales_leads_data_batch", "data_batch_id", postgresql_where=text("data_batch_id IS NOT NULL")),
        # simplified workflow (a3f8c2d7e915): "the latest N leads this person created" (the Lead Data Entry edit window)
        Index("ix_sales_leads_creator_recent", "created_by", text("created_at DESC"), text("id DESC")),
        Index("ix_sales_leads_campaign_id", "campaign_id", postgresql_where=text("campaign_id IS NOT NULL")),
        Index("ix_sales_leads_source", "source"),
        Index("ix_sales_leads_archived_by", "archived_by", postgresql_where=text("archived_by IS NOT NULL")),
        # Phase 5 (b6d1f3a8c294): "leads closed in a period" (the conversion report's event view)
        Index("ix_sales_leads_closed_at", "closed_at", postgresql_where=text("closed_at IS NOT NULL")),
        # -- duplicate detection: one equality lookup per identity key (+ the phone digit-tail lookups)
        Index("ix_sales_leads_phone_norm", "phone_norm", postgresql_where=text("phone_norm IS NOT NULL")),
        Index("ix_sales_leads_whatsapp_norm", "whatsapp_norm", postgresql_where=text("whatsapp_norm IS NOT NULL")),
        Index("ix_sales_leads_phone_tail", text("right(phone_norm, 9)"), postgresql_where=text("length(phone_norm) >= 9")),
        Index("ix_sales_leads_whatsapp_tail", text("right(whatsapp_norm, 9)"), postgresql_where=text("length(whatsapp_norm) >= 9")),
        Index("ix_sales_leads_email_norm", "email_norm", postgresql_where=text("email_norm IS NOT NULL")),
        Index("ix_sales_leads_website_norm", "website_norm", postgresql_where=text("website_norm IS NOT NULL")),
        Index("ix_sales_leads_name_city", "name_norm", "city_norm", postgresql_where=text("city_norm IS NOT NULL")),
    )


class SalesLeadActivity(Base):
    """One immutable event on a lead's timeline: what happened (event_type), who did it (actor_user_id), when
    (occurred_at, plus the monotonic `seq` for a stable order) and the relevant before/after values.

    Append-only: UPDATE, DELETE and TRUNCATE are rejected by triggers (same mechanism as staff_audit_events), and
    the FK to sales_leads is RESTRICT, so history can neither be edited nor orphaned. Payloads are built from
    field allowlists and screened for secret-looking keys (sales/services/activity.py); no password, token or
    authentication data is ever stored. Written in the same transaction as the change it records."""

    __tablename__ = "sales_lead_activities"

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_user_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False)
    # none_as_null: "no before/after value" is SQL NULL, not the JSON value null (which the CHECKs below would reject).
    before_data: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    after_data: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # `metadata` is reserved on declarative classes, hence the attribute name.
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    correlation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("seq", name="uq_sales_lead_activities_seq"),
        CheckConstraint("event_type ~ '^[a-z][a-z0-9_]{2,63}$'", name="ck_sales_lead_activities_type"),
        CheckConstraint(
            "before_data IS NULL OR (jsonb_typeof(before_data) = 'object' AND pg_column_size(before_data) <= 16384)",
            name="ck_sales_lead_activities_before",
        ),
        CheckConstraint(
            "after_data IS NULL OR (jsonb_typeof(after_data) = 'object' AND pg_column_size(after_data) <= 16384)",
            name="ck_sales_lead_activities_after",
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object' AND pg_column_size(metadata) <= 8192", name="ck_sales_lead_activities_metadata"),
        # the timeline query: one lead, newest first
        Index("ix_sales_lead_activities_lead", "lead_id", text("seq DESC")),
        Index("ix_sales_lead_activities_actor", "actor_user_id", text("seq DESC")),
    )


# =============================================================================
# Phase 3 - calls, follow-ups, demos, trials
#
# Four independent tables, each pointing at a lead (FK RESTRICT: a lead with work history can never be hard-deleted,
# same as its timeline). Nothing here is ever deleted by the application - calls are records, and follow-ups, demos
# and trials end in a terminal status - so every change is an UPDATE on a row plus an immutable timeline event.
# `lead_id` (and a call's `employee_id`) never change after creation.
# =============================================================================
class SalesCall(Base, TimestampMixin):
    """A call made to a lead. `employee_id` is who made it (the caller by default; a manager may log a call for
    somebody else). `duration_seconds` NULL means "not recorded" - a call of zero seconds is a different fact."""

    __tablename__ = "sales_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(f"result IN {_in_list(CALL_RESULTS)}", name="ck_sales_calls_result"),
        CheckConstraint(
            f"duration_seconds IS NULL OR (duration_seconds >= 0 AND duration_seconds <= {MAX_CALL_SECONDS})",
            name="ck_sales_calls_duration",
        ),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 5000", name="ck_sales_calls_notes"),
        # one lead's calls, newest first / one employee's calls / the recent-calls list
        Index("ix_sales_calls_lead_time", "lead_id", text("called_at DESC")),
        Index("ix_sales_calls_employee_time", "employee_id", text("called_at DESC")),
        Index("ix_sales_calls_called_at", text("called_at DESC")),
    )


class SalesFollowup(Base, TimestampMixin):
    """Something to do about a lead by `due_at`, given to one employee. Pending until completed or cancelled;
    "overdue" is not a status but a reading of the clock: pending AND due_at < now()."""

    __tablename__ = "sales_followups"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    assigned_to: Mapped[uuid.UUID] = fk_uuid("users.id")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'pending'"))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")

    __table_args__ = (
        CheckConstraint(f"status IN {_in_list(FOLLOWUP_STATUSES)}", name="ck_sales_followups_status"),
        # completed_at is set exactly while the follow-up is completed
        CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name="ck_sales_followups_completed_pair"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 5000", name="ck_sales_followups_notes"),
        Index("ix_sales_followups_lead_due", "lead_id", "due_at"),
        # the hot paths: "my follow-ups" and the overdue / upcoming lists read only OPEN rows
        Index("ix_sales_followups_assignee_open", "assigned_to", "due_at", postgresql_where=text("status = 'pending'")),
        Index("ix_sales_followups_open_due", "due_at", postgresql_where=text("status = 'pending'")),
        Index("ix_sales_followups_completed_at", text("completed_at DESC"), postgresql_where=text("status = 'completed'")),
        Index("ix_sales_followups_created_by", "created_by"),
        # Phase 5 (b6d1f3a8c294): follow-ups falling in a period, whatever their status
        Index("ix_sales_followups_due_at", "due_at"),
    )


class SalesDemo(Base, TimestampMixin):
    """A demo of the product for a lead, given to one employee. `scheduled` until completed, cancelled, or marked
    no-show; a scheduled demo whose time has passed is simply still waiting for its outcome."""

    __tablename__ = "sales_demos"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    assigned_to: Mapped[uuid.UUID] = fk_uuid("users.id")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'scheduled'"))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")

    __table_args__ = (
        CheckConstraint(f"status IN {_in_list(DEMO_STATUSES)}", name="ck_sales_demos_status"),
        CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name="ck_sales_demos_completed_pair"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 5000", name="ck_sales_demos_notes"),
        Index("ix_sales_demos_lead_time", "lead_id", text("scheduled_at DESC")),
        Index("ix_sales_demos_assignee_open", "assigned_to", "scheduled_at", postgresql_where=text("status = 'scheduled'")),
        Index("ix_sales_demos_open_time", "scheduled_at", postgresql_where=text("status = 'scheduled'")),
        Index("ix_sales_demos_scheduled_at", text("scheduled_at DESC")),
        Index("ix_sales_demos_created_by", "created_by"),
    )


class SalesTrial(Base, TimestampMixin):
    """Sales-side tracking of a trial period offered to a lead. NOT the platform's subscription / trial system:
    nothing here creates, extends or ends a company's access to JAZ.

    `actual_end_at` is set exactly when the trial is no longer active (completed OR cancelled - a cancelled trial
    ended early, and that is when). A lead has at most one ACTIVE trial, enforced by a partial unique index."""

    __tablename__ = "sales_trials"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expected_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'active'"))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = fk_uuid("users.id")

    __table_args__ = (
        CheckConstraint(f"status IN {_in_list(TRIAL_STATUSES)}", name="ck_sales_trials_status"),
        CheckConstraint("(status = 'active') = (actual_end_at IS NULL)", name="ck_sales_trials_end_pair"),
        CheckConstraint("expected_end_at > started_at", name="ck_sales_trials_expected_end"),
        CheckConstraint("actual_end_at IS NULL OR actual_end_at >= started_at", name="ck_sales_trials_actual_end"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 5000", name="ck_sales_trials_notes"),
        # "prevent multiple active trials for the same lead" - the database is the real guarantee
        Index("uq_sales_trials_one_active", "lead_id", unique=True, postgresql_where=text("status = 'active'")),
        Index("ix_sales_trials_lead_started", "lead_id", text("started_at DESC")),
        Index("ix_sales_trials_active_end", "expected_end_at", postgresql_where=text("status = 'active'")),
        Index("ix_sales_trials_actual_end", text("actual_end_at DESC"), postgresql_where=text("status <> 'active'")),
        Index("ix_sales_trials_created_by", "created_by"),
        # Phase 5 (b6d1f3a8c294): trials started in a period
        Index("ix_sales_trials_started_at", text("started_at DESC")),
    )


# =============================================================================
# Phase 4 - customers and onboarding
#
# A customer is the record that a WON lead became a JAZ company: one row per lead and one per company (both UNIQUE, so
# a repeated or racing conversion can never produce a second customer or a second company for the same lead). Neither
# the lead nor the company is changed by it - the lead is preserved as it was, and the company is an ordinary JAZ
# company created by the existing company-creation service. Nothing here is ever deleted by the application.
# =============================================================================
class SalesCustomer(Base, TimestampMixin):
    """A converted lead. `status` is derived from the customer's onboarding stage (constants.customer_status_for_stage)
    and maintained only by the onboarding service, in the same transaction as the stage change."""

    __tablename__ = "sales_customers"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    company_id: Mapped[uuid.UUID] = fk_uuid("companies.id", ondelete="RESTRICT")
    converted_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    converted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'active'"))
    # What the customer setup started the company on (a3f8c2d7e915). NULL for customers converted by the Phase-4 flow.
    subscription_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {_in_list(CUSTOMER_STATUSES)}", name="ck_sales_customers_status"),
        CheckConstraint(
            f"subscription_type IS NULL OR subscription_type IN {_in_list(SUBSCRIPTION_TYPES)}",
            name="ck_sales_customers_subscription_type",
        ),
        # "repeating the same conversion must not create another company/customer" - the database is the real guarantee
        Index("uq_sales_customers_lead", "lead_id", unique=True),
        Index("uq_sales_customers_company", "company_id", unique=True),
        Index("ix_sales_customers_status", "status", text("converted_at DESC")),
        Index("ix_sales_customers_converted_at", text("converted_at DESC")),
        Index("ix_sales_customers_converted_by", "converted_by"),
    )


class SalesOnboarding(Base, TimestampMixin):
    """The onboarding workflow of one customer (exactly one per customer). It starts in stage `won`, unowned; assigning
    an onboarding employee moves it to `assigned`, after which the stages are worked by hand up to `activated`.

    Two integrity rules live in the database as well as in the service: an onboarding has an owner exactly when it
    has left `won`, and `completed_at` is set exactly while it is `activated`."""

    __tablename__ = "sales_onboarding"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = fk_uuid("sales_customers.id", ondelete="RESTRICT")
    assigned_to: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'won'"))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"stage IN {_in_list(ONBOARDING_STAGES)}", name="ck_sales_onboarding_stage"),
        CheckConstraint("(stage = 'won') = (assigned_to IS NULL)", name="ck_sales_onboarding_owner_pair"),
        CheckConstraint("(stage = 'activated') = (completed_at IS NOT NULL)", name="ck_sales_onboarding_completed_pair"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="ck_sales_onboarding_completed_after_start"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 5000", name="ck_sales_onboarding_notes"),
        Index("uq_sales_onboarding_customer", "customer_id", unique=True),
        # "my onboarding" (the assigned scope) and the per-stage board
        Index("ix_sales_onboarding_assignee_stage", "assigned_to", "stage", postgresql_where=text("assigned_to IS NOT NULL")),
        Index("ix_sales_onboarding_stage", "stage", text("started_at DESC")),
        Index("ix_sales_onboarding_started_at", text("started_at DESC")),
    )


# =============================================================================
# Simplified workflow - the Sales Manager's settings
# =============================================================================
class SalesSettings(Base):
    """The Sales workspace settings: exactly ONE row (id = 1, CHECK), created by the migration. Automatic lead distribution
    gives every new lead that nobody chose an owner for to the next person in a round-robin over the staff who may receive
    leads (sales/services/distribution.py); `last_assigned_user_id` is where the rotation stands. The row is locked
    (SELECT ... FOR UPDATE) while a lead picks its owner, so concurrent lead creations take turns instead of racing."""

    __tablename__ = "sales_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    auto_distribution_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    distribution_mode: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'equal'"))
    last_assigned_user_id: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = fk_uuid("users.id", nullable=True)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_sales_settings_singleton"),
        CheckConstraint(f"distribution_mode IN {_in_list(DISTRIBUTION_MODES)}", name="ck_sales_settings_distribution_mode"),
    )


# =============================================================================
# Batches, attempts and work sessions (migration f2b6d8a1c4e9)
# =============================================================================
class SalesMasterBatch(Base):
    """Ten full Data Batches (oldest first, each in at most one master) - an internal Sales Manager organisation layer. The batches
    underneath stay intact; nothing else in Sales reads a master batch."""

    __tablename__ = "sales_master_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    formed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("seq", name="uq_sales_master_batches_seq"),)


class SalesDataBatch(Base):
    """Every DATA_BATCH_SIZE leads entered by ALL Data Entry staff combined. Owned by nobody: each lead keeps its own created_by /
    created_at. `open` while it fills (at most one is open, enforced by a partial unique index), `full` at 100."""

    __tablename__ = "sales_data_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'open'"))
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    master_batch_id: Mapped[Optional[uuid.UUID]] = fk_uuid("sales_master_batches.id", nullable=True, ondelete="RESTRICT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("seq", name="uq_sales_data_batches_seq"),
        CheckConstraint(f"status IN {_in_list(DATA_BATCH_STATUSES)}", name="ck_sales_data_batches_status"),
        CheckConstraint(f"lead_count >= 0 AND lead_count <= {DATA_BATCH_SIZE}", name="ck_sales_data_batches_count"),
        CheckConstraint("(status = 'full') = (filled_at IS NOT NULL)", name="ck_sales_data_batches_filled_pair"),
        CheckConstraint(f"status <> 'full' OR lead_count = {DATA_BATCH_SIZE}", name="ck_sales_data_batches_full_is_100"),
        CheckConstraint("master_batch_id IS NULL OR status = 'full'", name="ck_sales_data_batches_master_needs_full"),
        Index("uq_sales_data_batches_one_open", "status", unique=True, postgresql_where=text("status = 'open'")),
        Index("ix_sales_data_batches_master", "master_batch_id", postgresql_where=text("master_batch_id IS NOT NULL")),
        Index("ix_sales_data_batches_unmastered", "seq", postgresql_where=text("status = 'full' AND master_batch_id IS NULL")),
    )


class SalesWorkBatch(Base):
    """A salesperson's 100 worked leads. `employee_id` = whose leads formed it; who works it NOW is its latest attempt. Open while
    any lead is pending, closed when every lead has a final result."""

    __tablename__ = "sales_work_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'open'"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("seq", name="uq_sales_work_batches_seq"),
        CheckConstraint(f"status IN {_in_list(WORK_BATCH_STATUSES)}", name="ck_sales_work_batches_status"),
        CheckConstraint("lead_count > 0", name="ck_sales_work_batches_count"),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="ck_sales_work_batches_closed_pair"),
        Index("ix_sales_work_batches_employee", "employee_id", text("created_at DESC")),
        Index("ix_sales_work_batches_status", "status", text("created_at DESC")),
    )


class SalesBatchAttempt(Base):
    """One salesperson's pass over a Work Batch's leads: #1 is the initial one (formed from their own work), every later one a
    manager's reassignment over the SAME leads. At most one attempt of a batch is open."""

    __tablename__ = "sales_batch_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    work_batch_id: Mapped[uuid.UUID] = fk_uuid("sales_work_batches.id", ondelete="RESTRICT")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'open'"))
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("work_batch_id", "attempt_no", name="uq_sales_batch_attempts_no"),
        CheckConstraint("attempt_no >= 1", name="ck_sales_batch_attempts_no"),
        CheckConstraint(f"kind IN {_in_list(BATCH_ATTEMPT_KINDS)}", name="ck_sales_batch_attempts_kind"),
        CheckConstraint(f"status IN {_in_list(WORK_BATCH_STATUSES)}", name="ck_sales_batch_attempts_status"),
        CheckConstraint("lead_count > 0", name="ck_sales_batch_attempts_count"),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="ck_sales_batch_attempts_closed_pair"),
        CheckConstraint("(attempt_no = 1) = (kind = 'initial')", name="ck_sales_batch_attempts_first_is_initial"),
        Index("uq_sales_batch_attempts_one_open", "work_batch_id", unique=True, postgresql_where=text("status = 'open'")),
        Index("ix_sales_batch_attempts_employee", "employee_id", "status"),
    )


class SalesBatchAttemptLead(Base):
    """Membership: the leads a batch attempt covers. A lead is referenced, never copied - the same lead is a member of every attempt
    of its batch that covers it."""

    __tablename__ = "sales_batch_attempt_leads"

    batch_attempt_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_batch_attempts.id", ondelete="RESTRICT"), primary_key=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sales_leads.id", ondelete="RESTRICT"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_sales_batch_attempt_leads_ordinal"),
        Index("ix_sales_batch_attempt_leads_lead", "lead_id"),
    )


class SalesLeadAttempt(Base):
    """One salesperson's try at one lead - the persistent per-lead history behind every batch statistic and every performance
    number. `first_outcome` is what they decided first; `result` how it ended (NULL = pending on the wait list). Never deleted."""

    __tablename__ = "sales_lead_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk_uuid("sales_leads.id", ondelete="RESTRICT")
    employee_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    first_outcome: Mapped[str] = mapped_column(String, nullable=False)
    outcome_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    wait_listed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_by: Mapped[uuid.UUID] = fk_uuid("users.id")
    batch_attempt_id: Mapped[Optional[uuid.UUID]] = fk_uuid("sales_batch_attempts.id", nullable=True, ondelete="RESTRICT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="ck_sales_lead_attempts_no"),
        CheckConstraint(f"first_outcome IN {_in_list(FIRST_OUTCOMES)}", name="ck_sales_lead_attempts_outcome"),
        CheckConstraint(f"result IS NULL OR result IN {_in_list(ATTEMPT_RESULTS)}", name="ck_sales_lead_attempts_result"),
        CheckConstraint("(first_outcome = 'wait_list') = (wait_listed_at IS NOT NULL)", name="ck_sales_lead_attempts_wait_pair"),
        CheckConstraint("(result IS NULL) = (result_at IS NULL)", name="ck_sales_lead_attempts_result_pair"),
        CheckConstraint("first_outcome = 'wait_list' OR (result IS NOT NULL AND result = first_outcome)", name="ck_sales_lead_attempts_direct_is_final"),
        CheckConstraint("result IS DISTINCT FROM 'released' OR first_outcome = 'wait_list'", name="ck_sales_lead_attempts_released"),
        Index("uq_sales_lead_attempts_no", "lead_id", "attempt_no", unique=True),
        Index("uq_sales_lead_attempts_one_pending", "lead_id", unique=True, postgresql_where=text("result IS NULL")),
        Index("ix_sales_lead_attempts_employee", "employee_id", text("outcome_at DESC")),
        Index("ix_sales_lead_attempts_unbatched", "employee_id", "outcome_at", "id", postgresql_where=text("batch_attempt_id IS NULL")),
        Index("ix_sales_lead_attempts_batch_attempt", "batch_attempt_id", postgresql_where=text("batch_attempt_id IS NOT NULL")),
        Index("ix_sales_lead_attempts_outcome_at", text("outcome_at DESC")),
    )


class SalesWorkSession(Base):
    """A persisted stretch of work: consecutive recorded work actions of one person less than SESSION_GAP apart (constants.py).
    The source of the "work hours" numbers; never deleted."""

    __tablename__ = "sales_work_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("ended_at >= started_at", name="ck_sales_work_sessions_order"),
        CheckConstraint("actions >= 1", name="ck_sales_work_sessions_actions"),
        Index("ix_sales_work_sessions_user_end", "user_id", text("ended_at DESC")),
        Index("ix_sales_work_sessions_user_start", "user_id", "started_at"),
    )
