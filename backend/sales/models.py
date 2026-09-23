"""RBAC tables for internal JAZ staff (Phase 1 of JAZ Sales).

`module` is a column, not part of the table names, so later internal modules
(e.g. Support) can reuse the same role/permission machinery without a redesign.
Mirrors migrations/versions/bb595f6d8dae_sales_rbac_foundation.py (the four RBAC tables) and
2d916a1a993e_sales_audit_trail_and_revocation_integrity.py (the revocation CHECK and the audit table).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
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
