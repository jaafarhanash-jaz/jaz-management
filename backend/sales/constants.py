"""JAZ Sales (Phases 2-4) - the lead vocabulary, the pipeline rules, the work-item (call / follow-up / demo /
trial) vocabulary and the customer / onboarding vocabulary.

Pure Python, no database or framework imports, so it is unit-testable on its own.

The database enforces the same closed sets: stages, priorities, lost reasons and campaign statuses with CHECK
constraints, lead sources with the seeded reference table `sales_lead_sources` (see migration
d764d5f44e91_sales_leads_foundation.py); call results and the follow-up / demo / trial statuses with CHECK
constraints (see migration e3a9c5b07d12_sales_activities.py); customer statuses and onboarding stages with CHECK
constraints (see migration f4c8a1d92b63_sales_customers_onboarding.py). Keep the two in step: a value added here
needs a migration.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Optional, Tuple

# ---- lead sources ------------------------------------------------------------------
# (key, name_en, name_ar). The migration seeds exactly these rows; the API exposes them read-only.
LEAD_SOURCES: Tuple[Tuple[str, str, str], ...] = (
    ("google_maps", "Google Maps", "خرائط جوجل"),
    ("website", "Website", "الموقع الإلكتروني"),
    ("facebook", "Facebook", "فيسبوك"),
    ("instagram", "Instagram", "إنستغرام"),
    ("tiktok", "TikTok", "تيك توك"),
    ("referral", "Referral", "إحالة"),
    ("cold_call", "Cold Call", "اتصال مباشر"),
    ("inbound", "Inbound", "وارد"),
    ("advertisement", "Advertisement", "إعلان"),
    ("manual_entry", "Manual Entry", "إدخال يدوي"),
    ("import", "Import", "استيراد"),
)
SOURCE_KEYS: Tuple[str, ...] = tuple(key for key, _, _ in LEAD_SOURCES)
DEFAULT_SOURCE = "manual_entry"

# ---- priorities --------------------------------------------------------------------
PRIORITIES: Tuple[str, ...] = ("low", "medium", "high")
DEFAULT_PRIORITY = "medium"
PRIORITY_RANK: Dict[str, int] = {"low": 1, "medium": 2, "high": 3}

# ---- lost reasons ------------------------------------------------------------------
LOST_REASONS: Tuple[str, ...] = (
    "too_expensive",
    "not_interested",
    "already_using_another_system",
    "no_response",
    "wrong_number",
    "business_closed",
    "not_suitable",
    "delayed_decision",
    "competitor",
    "other",
)

# ---- campaigns ---------------------------------------------------------------------
CAMPAIGN_STATUSES: Tuple[str, ...] = ("active", "paused", "completed")
DEFAULT_CAMPAIGN_STATUS = "active"

# ---- pipeline ----------------------------------------------------------------------
STAGE_NEW = "new"
STAGE_ASSIGNED = "assigned"
STAGE_CONTACTED = "contacted"
STAGE_INTERESTED = "interested"
STAGE_DEMO_SCHEDULED = "demo_scheduled"
STAGE_DEMO_COMPLETED = "demo_completed"
STAGE_TRIAL = "trial"
STAGE_NEGOTIATION = "negotiation"
STAGE_WON = "won"
STAGE_LOST = "lost"

# Board / display order.
PIPELINE_STAGES: Tuple[str, ...] = (
    STAGE_NEW,
    STAGE_ASSIGNED,
    STAGE_CONTACTED,
    STAGE_INTERESTED,
    STAGE_DEMO_SCHEDULED,
    STAGE_DEMO_COMPLETED,
    STAGE_TRIAL,
    STAGE_NEGOTIATION,
    STAGE_WON,
    STAGE_LOST,
)
STAGE_RANK: Dict[str, int] = {stage: i for i, stage in enumerate(PIPELINE_STAGES)}
DEFAULT_STAGE = STAGE_NEW

# The stages a salesperson moves a lead through once they own it.
WORKING_STAGES: Tuple[str, ...] = (
    STAGE_CONTACTED,
    STAGE_INTERESTED,
    STAGE_DEMO_SCHEDULED,
    STAGE_DEMO_COMPLETED,
    STAGE_TRIAL,
    STAGE_NEGOTIATION,
)
CLOSED_STAGES: FrozenSet[str] = frozenset({STAGE_WON, STAGE_LOST})
# "Open workload": leads somebody still has to work (drives the per-employee workload count).
OPEN_STAGES: Tuple[str, ...] = tuple(s for s in PIPELINE_STAGES if s not in CLOSED_STAGES)

# `new` and `assigned` are managed by assignment, not by hand: a lead is `new` exactly while nobody owns it
# and moves to `assigned` the moment it gets an owner (and back when the owner is removed). So the only manual
# ways out of `new` are closing it as lost, and the only manual way INTO new/assigned is reopening a lost lead.
_WORKING = frozenset(WORKING_STAGES)
STAGE_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    STAGE_NEW: frozenset({STAGE_LOST}),
    STAGE_ASSIGNED: _WORKING | {STAGE_LOST},
    # forward (skipping allowed) or back among the working stages, straight to won once contact was made, or lost
    **{stage: (_WORKING - {stage}) | {STAGE_WON, STAGE_LOST} for stage in WORKING_STAGES},
    STAGE_WON: frozenset(),  # terminal in Phase 2 (conversion / reopening a won lead belongs to a later phase)
    STAGE_LOST: frozenset({STAGE_NEW, STAGE_ASSIGNED}),  # reopen: `new` if nobody owns it, else `assigned`
}

# Assignment moves the stage only at the two edges of the pipeline.
AUTO_STAGE_ON_ASSIGN: Dict[str, str] = {STAGE_NEW: STAGE_ASSIGNED}
AUTO_STAGE_ON_UNASSIGN: Dict[str, str] = {STAGE_ASSIGNED: STAGE_NEW}

# stages that need an owner: working stages, won, and `assigned` itself
_NEEDS_ASSIGNEE = frozenset(WORKING_STAGES) | {STAGE_WON, STAGE_ASSIGNED}


def transition_error(
    current: str, target: str, *, has_assignee: bool, lost_reason: Optional[str]
) -> Optional[Tuple[str, str]]:
    """Why a manual stage change is not allowed, as (code, message) - or None when it is.

    Server-side authority for the pipeline: the API calls this before touching a lead and the UI only mirrors
    it (the lead payload carries `allowed_stages`). Codes are stable identifiers for clients and tests.
    """
    if current not in STAGE_TRANSITIONS or target not in STAGE_RANK:
        return "invalid_stage", "Unknown pipeline stage"
    if target == current:
        return "same_stage", f"The lead is already in the '{current}' stage"
    if target not in STAGE_TRANSITIONS[current]:
        return "transition_not_allowed", f"A lead cannot move from '{current}' to '{target}'"
    if target == STAGE_LOST:
        if lost_reason is None:
            return "lost_reason_required", "A lost reason is required when marking a lead as lost"
        if lost_reason not in LOST_REASONS:
            return "invalid_lost_reason", "Unknown lost reason"
    elif lost_reason is not None:
        return "lost_reason_not_allowed", "A lost reason can only be given when marking a lead as lost"
    if target in _NEEDS_ASSIGNEE and not has_assignee:
        return "assignee_required", "Assign the lead to a Sales Employee before moving it to this stage"
    if target == STAGE_NEW and has_assignee:
        return "unassign_required", "Unassign the lead before returning it to the new stage"
    return None


def allowed_stages(current: str, *, has_assignee: bool) -> Tuple[str, ...]:
    """Every stage a manual change could target right now (a lost reason, where needed, is supplied with the
    request). Preserves pipeline order."""
    return tuple(
        target
        for target in PIPELINE_STAGES
        if transition_error(
            current, target, has_assignee=has_assignee, lost_reason="other" if target == STAGE_LOST else None
        )
        is None
    )


# =============================================================================
# Phase 3 - calls, follow-ups, demos, trials
# =============================================================================
# ---- calls ---------------------------------------------------------------------------
CALL_RESULTS: Tuple[str, ...] = (
    "answered", "no_answer", "busy", "wrong_number", "interested", "not_interested", "call_back",
)
MAX_CALL_SECONDS = 24 * 60 * 60      # a call longer than a day is a typo, not a call

# ---- statuses (each item has exactly one OPEN status; every other status is terminal) -----
FOLLOWUP_STATUSES: Tuple[str, ...] = ("pending", "completed", "cancelled")
FOLLOWUP_OPEN = "pending"
DEMO_STATUSES: Tuple[str, ...] = ("scheduled", "completed", "cancelled", "no_show")
DEMO_OPEN = "scheduled"
TRIAL_STATUSES: Tuple[str, ...] = ("active", "completed", "cancelled")
TRIAL_OPEN = "active"

# ---- time rules ------------------------------------------------------------------------
# Something that "already happened" (a call, the start of a trial, the end of one) may be at most this far ahead of
# the server clock: browsers and phones drift a little, and refusing a call logged a second "early" would be absurd.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
# Sanity window for every client-supplied timestamp. It only keeps garbage (year 0001, year 9999) out of the
# database and the UI; the real business rules are above and in the services.
MIN_TIMESTAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
MAX_TIMESTAMP = datetime(2100, 1, 1, tzinfo=timezone.utc)
# "Ending soon" for a Sales trial: the expected end is at most this many days away (or already behind us).
DEFAULT_ENDING_SOON_DAYS = 3
MAX_ENDING_SOON_DAYS = 30


# =============================================================================
# Phase 4 - customers and onboarding
# =============================================================================
# A customer is a won lead that has become a JAZ company. Its status is NOT edited by hand: it follows the customer's
# onboarding stage (customer_status_for_stage), so there is exactly one source of truth for "where is this customer".
CUSTOMER_STATUSES: Tuple[str, ...] = ("active", "onboarding", "activated")

# The onboarding workflow. `won` is the starting point: the customer exists but nobody owns the onboarding yet.
# Assigning an onboarding employee is what moves it on (to `assigned`); from there the stages are worked by hand.
ONBOARDING_STAGES: Tuple[str, ...] = (
    "won", "assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated",
)
ONBOARDING_START = "won"
ONBOARDING_ASSIGNED = "assigned"
ONBOARDING_DONE = "activated"
ONBOARDING_RANK: Dict[str, int] = {stage: i for i, stage in enumerate(ONBOARDING_STAGES)}


def customer_status_for_stage(stage: str) -> str:
    """won -> active (converted, onboarding not started) | assigned .. training -> onboarding | activated -> activated."""
    if stage == ONBOARDING_START:
        return "active"
    if stage == ONBOARDING_DONE:
        return "activated"
    return "onboarding"


def onboarding_transition_error(current: str, target: str, *, has_assignee: bool) -> Optional[Tuple[str, str]]:
    """Why a MANUAL onboarding stage change is not allowed, as (code, message) - or None when it is. Server-side
    authority (the API calls it before touching a record; the UI only mirrors it through `allowed_stages`).

    Any working stage may follow any other (forward or back - a customer can be sent back for another training
    session), and a finished (`activated`) record can be reopened. What can never be chosen by hand is `won`
    (the starting point), and nothing can move until somebody owns the onboarding."""
    if current not in ONBOARDING_RANK or target not in ONBOARDING_RANK:
        return "onboarding_invalid_stage", "Unknown onboarding stage"
    if target == current:
        return "onboarding_same_stage", f"The onboarding is already in the '{current}' stage"
    if target == ONBOARDING_START:
        return "onboarding_transition_not_allowed", "An onboarding cannot return to its starting stage"
    if not has_assignee:
        return "onboarding_assignee_required", "Assign an onboarding employee before moving the onboarding forward"
    return None


def onboarding_allowed_stages(current: str, *, has_assignee: bool) -> Tuple[str, ...]:
    return tuple(t for t in ONBOARDING_STAGES if onboarding_transition_error(current, t, has_assignee=has_assignee) is None)
