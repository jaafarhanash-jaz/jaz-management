"""Row-level onboarding visibility: WHICH onboarding records may this caller see and work?

The same two layers as the lead scope (services/lead_access.py), for the one place where the people who do the work are
NOT salespeople: the endpoint's `require_permission(...)` decides whether the caller may perform an action at all, and
the two onboarding-scope permissions decide on which records.

    sales.onboarding.scope_all       every onboarding record                        (Sales Manager, Super Admin)
    sales.onboarding.scope_assigned  the records assigned to the caller             (Onboarding Employee)

The scope is the UNION of the scopes a caller holds; holding none means no record at all (fail closed). Only permissions
are consulted - never role names. Deliberately independent of the lead scope: an Onboarding Employee reaches their
assigned records without reaching the lead (or the company) behind them.

The SQL predicate (onboarding_visibility) and its Python mirror (can_see_onboarding) must stay equivalent.
"""
import uuid
from typing import Optional

from sqlalchemy import false, true

from sales import permissions as perms
from sales.models import SalesOnboarding
from sales.services.access import StaffContext


def onboarding_visibility(ctx: StaffContext):
    """SQL predicate over SalesOnboarding: the rows this caller may see."""
    if ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL):
        return true()
    if ctx.has(perms.PERM_ONBOARDING_SCOPE_ASSIGNED):
        return SalesOnboarding.assigned_to == ctx.user_id
    return false()


def can_see_onboarding(ctx: StaffContext, *, assigned_to: Optional[uuid.UUID]) -> bool:
    """Python mirror of onboarding_visibility for a record already in hand."""
    if ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL):
        return True
    return ctx.has(perms.PERM_ONBOARDING_SCOPE_ASSIGNED) and assigned_to is not None and assigned_to == ctx.user_id
