"""The lead activity timeline: recording and reading events.

Same guarantees as the staff audit trail (services/audit.py), applied to a lead's history:

  * SAME TRANSACTION. record() inserts through the caller's own session, so an event commits or rolls back with the
    change it describes. If the insert fails the exception propagates and the whole request - change included -
    fails (core's get_db rolls back). A lead can never change without its event.
  * IMMUTABLE. The table rejects UPDATE / DELETE / TRUNCATE (triggers); this module only ever inserts.
  * NO SECRETS. before/after/metadata are built from lead-field allowlists and screened by the audit trail's
    secret-key check; passwords, hashes and tokens can never reach the table. String values are clipped so a
    timeline row stays small however long the underlying note or description is.
  * ONE CORRELATION ID PER REQUEST (AuditContext), shared with the staff audit events of the same request.

Who, when, what: actor_user_id + occurred_at (database clock) + event_type + before/after (only what changed).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Collection, Dict, FrozenSet, Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.repositories import activities as activities_repo
from sales.services import work_sessions
from sales.services.access import StaffContext
from sales.services.audit import AuditContext, _assert_no_secrets

# ---- event types (the timeline vocabulary; adding one is a code change, not a migration) ----
EVENT_LEAD_CREATED = "lead_created"
EVENT_LEAD_UPDATED = "lead_updated"
EVENT_LEAD_ASSIGNED = "lead_assigned"
EVENT_LEAD_UNASSIGNED = "lead_unassigned"
EVENT_LEAD_REASSIGNED = "lead_reassigned"
EVENT_STAGE_CHANGED = "stage_changed"
EVENT_LEAD_MARKED_WON = "lead_marked_won"
EVENT_LEAD_MARKED_LOST = "lead_marked_lost"
EVENT_LEAD_ARCHIVED = "lead_archived"
EVENT_LEAD_RESTORED = "lead_restored"
# f2b6d8a1c4e9: the salesperson put the lead on the wait list (a timestamp and a pending status - nothing else happens)
EVENT_LEAD_WAIT_LISTED = "lead_wait_listed"

# ---- Phase 3: the work done on a lead. Each event names its item in metadata (call_id / followup_id / demo_id / trial_id).
EVENT_CALL_CREATED = "call_created"
EVENT_CALL_UPDATED = "call_updated"
EVENT_FOLLOWUP_CREATED = "followup_created"
EVENT_FOLLOWUP_UPDATED = "followup_updated"
EVENT_FOLLOWUP_COMPLETED = "followup_completed"
EVENT_FOLLOWUP_CANCELLED = "followup_cancelled"
EVENT_DEMO_SCHEDULED = "demo_scheduled"
EVENT_DEMO_RESCHEDULED = "demo_rescheduled"
EVENT_DEMO_UPDATED = "demo_updated"
EVENT_DEMO_COMPLETED = "demo_completed"
EVENT_DEMO_CANCELLED = "demo_cancelled"
EVENT_DEMO_NO_SHOW = "demo_no_show"
EVENT_TRIAL_STARTED = "trial_started"
EVENT_TRIAL_UPDATED = "trial_updated"
EVENT_TRIAL_COMPLETED = "trial_completed"
EVENT_TRIAL_CANCELLED = "trial_cancelled"

# ---- Phase 4: conversion and onboarding. The conversion is recorded on the LEAD's timeline (it is what happened to the
# lead); the onboarding events are recorded on the same lead's timeline, keyed by the onboarding id in metadata.
EVENT_LEAD_CONVERTED = "lead_converted"
EVENT_ONBOARDING_ASSIGNED = "onboarding_assigned"
EVENT_ONBOARDING_STAGE_CHANGED = "onboarding_stage_changed"
EVENT_ONBOARDING_NOTES_UPDATED = "onboarding_notes_updated"
ONBOARDING_EVENT_TYPES: FrozenSet[str] = frozenset({
    EVENT_ONBOARDING_ASSIGNED, EVENT_ONBOARDING_STAGE_CHANGED, EVENT_ONBOARDING_NOTES_UPDATED,
})

# Who may READ which events. The timeline belongs to the lead: anybody who can see the lead sees the lead's own
# events (created, updated, assigned, stage changes, ...). The events of the work done on it carry the substance of
# that work (a call's notes, a demo's outcome), so each kind is shown only to a caller who may view that kind of
# work - a Lead Data Entry user can open the lead they created without reading its calls. An ALLOW-list on purpose:
# an event type nobody has classified is shown to nobody.
LEAD_EVENT_TYPES: FrozenSet[str] = frozenset({
    EVENT_LEAD_CREATED, EVENT_LEAD_UPDATED, EVENT_LEAD_ASSIGNED, EVENT_LEAD_UNASSIGNED, EVENT_LEAD_REASSIGNED,
    EVENT_STAGE_CHANGED, EVENT_LEAD_MARKED_WON, EVENT_LEAD_MARKED_LOST, EVENT_LEAD_ARCHIVED, EVENT_LEAD_RESTORED,
})
WORK_EVENT_TYPES: Dict[str, FrozenSet[str]] = {     # view permission -> the events it unlocks
    # the wait list is the salesperson's own working state: whoever decides on leads reads it; Lead Data Entry, who may open a lead they
    # entered, never does
    perms.PERM_LEADS_CHANGE_STAGE: frozenset({EVENT_LEAD_WAIT_LISTED}),
    perms.PERM_CALLS_VIEW: frozenset({EVENT_CALL_CREATED, EVENT_CALL_UPDATED}),
    perms.PERM_FOLLOWUPS_VIEW: frozenset({
        EVENT_FOLLOWUP_CREATED, EVENT_FOLLOWUP_UPDATED, EVENT_FOLLOWUP_COMPLETED, EVENT_FOLLOWUP_CANCELLED,
    }),
    perms.PERM_DEMOS_VIEW: frozenset({
        EVENT_DEMO_SCHEDULED, EVENT_DEMO_RESCHEDULED, EVENT_DEMO_UPDATED, EVENT_DEMO_COMPLETED, EVENT_DEMO_CANCELLED,
        EVENT_DEMO_NO_SHOW,
    }),
    perms.PERM_TRIALS_VIEW: frozenset({EVENT_TRIAL_STARTED, EVENT_TRIAL_UPDATED, EVENT_TRIAL_COMPLETED, EVENT_TRIAL_CANCELLED}),
    # Phase 4: the conversion belongs to whoever may view customers; the onboarding events to whoever may view onboarding
    # (an Onboarding Employee reads theirs on the onboarding page, never on a lead - they cannot open one).
    perms.PERM_CUSTOMERS_VIEW: frozenset({EVENT_LEAD_CONVERTED}),
    perms.PERM_ONBOARDING_VIEW: ONBOARDING_EVENT_TYPES,
}


def visible_event_types(actor: StaffContext) -> FrozenSet[str]:
    """The event types this reader may see on a lead's timeline (see the comment above)."""
    allowed = set(LEAD_EVENT_TYPES)
    for permission, types in WORK_EVENT_TYPES.items():
        if actor.has(permission):
            allowed |= types
    return frozenset(allowed)

# Longest string kept in a before/after value (a note may be thousands of characters; the timeline needs the gist).
MAX_VALUE_CHARS = 300


def json_safe(value: Any) -> Any:
    """A JSON-storable copy of a payload: UUIDs, datetimes and Decimals turned into strings/numbers, long strings clipped."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    return str(value)


def snapshot(lead, fields: Iterable[str]) -> Dict[str, Any]:
    """Allowlisted values of a lead - the only way a lead row enters an event payload."""
    return {field: json_safe(getattr(lead, field)) for field in fields}


async def record(
    db: AsyncSession,
    actor: StaffContext,
    audit: AuditContext,
    lead_id: uuid.UUID,
    event_type: str,
    *,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    note: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Insert one event in the caller's transaction (see the module docstring)."""
    before, after, metadata = json_safe(before), json_safe(after), json_safe(metadata or {})
    _assert_no_secrets(before, "before_data")
    _assert_no_secrets(after, "after_data")
    _assert_no_secrets(metadata, "metadata")
    await activities_repo.insert_event(
        db,
        lead_id=lead_id,
        event_type=event_type,
        actor_user_id=actor.user_id,
        before=before,
        after=after,
        note=note,
        # under what authority the actor acted (their roles can change or be revoked later)
        metadata={"actor_roles": [role["key"] for role in actor.roles], **metadata},
        correlation_id=audit.correlation_id,
    )
    # every recorded work action is also a moment of work: the persisted work sessions behind "work hours"
    await work_sessions.touch(db, actor.user_id)


async def list_events(
    db: AsyncSession, lead_id: uuid.UUID, limit: int, offset: int, event_types: Optional[Collection[str]] = None
) -> dict:
    rows, total = await activities_repo.list_for_lead(db, lead_id, limit, offset, event_types)
    items: List[dict] = [
        {
            "id": str(event.id),
            "seq": event.seq,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "actor": {"id": str(event.actor_user_id), "name": actor_name},
            "before": event.before_data,
            "after": event.after_data,
            "note": event.note,
            "metadata": event.event_metadata,
        }
        for event, actor_name in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
