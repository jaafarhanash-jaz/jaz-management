"""Security/admin audit trail writer (staff_audit_events).

What every caller gets from record():

  * SAME TRANSACTION. The event is inserted through the caller's own session, so it commits or rolls back
    together with the change it describes. If the insert fails the exception propagates and the whole
    request - change included - fails (core's get_db rolls the session back). There is no fire-and-forget
    path, so a change can never succeed without its event.
  * NO SECRETS. before/after/metadata are built from the allowlists below, and record() refuses (raises) a
    payload holding a secret-looking key. Passwords, password hashes and tokens never reach the table.
  * ONE CORRELATION ID PER REQUEST. get_audit_context (deps.py) makes one AuditContext per request; every
    event a request emits carries its correlation_id, so a compound action (create + N role grants) is
    recognisable as one.
  * NO IP. `ip_address` is never populated by this code. request.client.host is the immediate TCP peer -
    the reverse proxy in the documented nginx deployment, not the user - and X-Forwarded-For is client
    controlled, so recording either would be misleading or forgeable. The column stays NULL until proxy
    trust is configured for the app (out of scope for this task).

Only "success" events for staff-account and role changes are written today. Denied-attempt and login/
logout auditing, an audit UI/API, retention and PII erasure are intentionally not implemented yet.
"""
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sales.models import StaffAuditEvent
from sales.services.access import StaffContext

MODULE_SALES = "sales"

# ---- event names (the audit vocabulary; adding one is a code change, not a migration) ----
ACTION_STAFF_CREATED = "staff_created"
ACTION_STAFF_UPDATED = "staff_updated"
ACTION_STAFF_PASSWORD_RESET = "staff_password_reset"
ACTION_STAFF_DEACTIVATED = "staff_deactivated"
ACTION_STAFF_REACTIVATED = "staff_reactivated"
ACTION_STAFF_ROLE_GRANTED = "staff_role_granted"
ACTION_STAFF_ROLE_REVOKED = "staff_role_revoked"
# Phase 4: converting a lead creates a real JAZ company and its owner ACCOUNT, which is security-relevant on its own.
ACTION_CUSTOMER_CONVERTED = "customer_converted"
# Simplified workflow: the customer setup (company + owner + optional employee ACCOUNTS), who changed how leads are handed
# out, and every lead export (personal data leaving the system as a file).
ACTION_CUSTOMER_SETUP_COMPLETED = "customer_setup_completed"
ACTION_SALES_SETTINGS_UPDATED = "sales_settings_updated"
ACTION_LEADS_EXPORTED = "leads_exported"
# f2b6d8a1c4e9: the Sales Manager handed a completed Sales Work Batch to another salesperson (who gets which leads)
ACTION_WORK_BATCH_REASSIGNED = "work_batch_reassigned"

TARGET_STAFF_USER = "staff_user"
TARGET_STAFF_ROLE_GRANT = "staff_role_grant"
TARGET_SALES_CUSTOMER = "sales_customer"
TARGET_SALES_SETTINGS = "sales_settings"
TARGET_SALES_LEADS = "sales_leads"
TARGET_SALES_WORK_BATCH = "sales_work_batch"

# ---- what may appear in before/after: the account fields an investigator needs, nothing else ----
STAFF_USER_FIELDS = ("name", "email", "phone", "status")
STAFF_PROFILE_FIELDS = ("name", "email", "phone")  # status changes are their own events

# Keys that must never appear, at any depth, in before_data / after_data / metadata (compared
# case-insensitively, exactly - `refresh_tokens_revoked` is fine, `refresh_token` is not).
_SECRET_KEYS = frozenset({
    "password", "new_password", "current_password", "old_password", "owner_password", "password_hash", "hashed_password", "hash",
    "token", "access_token", "refresh_token", "id_token", "secret", "client_secret", "api_key", "authorization",
})
_MAX_USER_AGENT = 512


@dataclass(frozen=True)
class AuditContext:
    """Per-request audit facts: created once per request (deps.get_audit_context)."""

    correlation_id: uuid.UUID
    user_agent: Optional[str] = None


def new_audit_context(user_agent: Optional[str] = None) -> AuditContext:
    ua = None
    if user_agent:
        # Client-supplied text: stored as data only, capped, and NUL-stripped (PostgreSQL text can't hold NUL).
        ua = user_agent.replace("\x00", "")[:_MAX_USER_AGENT] or None
    return AuditContext(correlation_id=uuid.uuid4(), user_agent=ua)


def user_fields(user, fields: Iterable[str] = STAFF_USER_FIELDS) -> dict:
    """Allowlisted snapshot of an account - the only way a user row enters an audit payload."""
    return {field: getattr(user, field) for field in fields}


def _assert_no_secrets(payload: Any, where: str) -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).lower() in _SECRET_KEYS:
                raise ValueError(f"refusing to audit a secret-looking key {key!r} in {where}")
            _assert_no_secrets(value, where)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_secrets(item, where)


async def record(
    db: AsyncSession,
    actor: StaffContext,
    audit: AuditContext,
    *,
    action: str,
    target_type: str,
    target_id: Optional[uuid.UUID] = None,
    target_user_id: Optional[uuid.UUID] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    metadata: Optional[dict] = None,
    reason: Optional[str] = None,
) -> None:
    """Insert one success event in the caller's transaction (see the module docstring)."""
    _assert_no_secrets(before, "before_data")
    _assert_no_secrets(after, "after_data")
    _assert_no_secrets(metadata, "metadata")

    await db.execute(
        insert(StaffAuditEvent).values(
            id=uuid.uuid4(),
            module=MODULE_SALES,
            action=action,
            outcome="success",
            actor_type="user",
            actor_user_id=actor.user_id,
            actor_platform_role=actor.user.get("role"),
            target_type=target_type,
            target_id=target_id,
            target_user_id=target_user_id,
            before_data=before,
            after_data=after,
            reason=reason,
            # under what authority the actor acted (their roles can change or be revoked later)
            event_metadata={"actor_roles": [role["key"] for role in actor.roles], **(metadata or {})},
            correlation_id=audit.correlation_id,
            ip_address=None,
            user_agent=audit.user_agent,
        )
    )
