"""Whose numbers is the caller asking for - and are they allowed to see them?

The rule that keeps aggregates from leaking (see sales/metrics.py, WHO SEES WHAT): an aggregate obeys the same scope as
the records it counts, and comparing people is a team privilege.

  team      the caller holds sales.leads.scope_all (Sales Manager, Super Admin). Everyone's numbers; may narrow to one
            employee with `employee_id`.
  personal  anybody else. Lead numbers follow their lead scope (assigned to them / created by them + unassigned); work
            numbers (calls, follow-ups, demos, trials) are the work THEY did or own. Asking about another employee is a 403
            - even a one-person aggregate is that person's private performance.

Two different "who" questions, which must not be confused:
  lead_owner  whose LEADS - leads are counted by who owns them (assigned_to). None = no narrowing beyond the scope.
  actor       whose WORK - activities are counted by who did / owns them. A personal caller is always their own actor.
Only permissions are consulted, never role names.
"""
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from sales import permissions as perms
from sales.activity_params import _person
from sales.metrics import ACTIVITY_KINDS, Period
from sales.services.access import StaffContext
from sales.services.common import access_denied
from sales.services.lead_access import lead_visibility
from sales.services.onboarding_access import onboarding_visibility

_KIND_VIEW = {
    "calls": perms.PERM_CALLS_VIEW,
    "followups": perms.PERM_FOLLOWUPS_VIEW,
    "demos": perms.PERM_DEMOS_VIEW,
    "trials": perms.PERM_TRIALS_VIEW,
}


def viewable_kinds(ctx: StaffContext) -> Tuple[str, ...]:
    """The kinds of work whose numbers the caller may see (the ones whose records they may open)."""
    return tuple(kind for kind in ACTIVITY_KINDS if ctx.has(_KIND_VIEW[kind]))


def has_onboarding_scope(ctx: StaffContext) -> bool:
    return ctx.has(perms.PERM_ONBOARDING_VIEW) and (
        ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL) or ctx.has(perms.PERM_ONBOARDING_SCOPE_ASSIGNED)
    )


@dataclass(frozen=True)
class ReportScope:
    ctx: StaffContext
    period: Period
    team: bool
    subject: Optional[uuid.UUID]        # the one employee a TEAM caller narrowed to (never set for a personal caller)

    @property
    def name(self) -> str:
        return "team" if self.team else "personal"

    @property
    def visibility(self):
        """SQL predicate over SalesLead: the leads the caller may open - what every lead / work-item aggregate is ANDed with."""
        return lead_visibility(self.ctx)

    @property
    def onboarding_visibility(self):
        return onboarding_visibility(self.ctx)

    @property
    def lead_owner(self) -> Optional[uuid.UUID]:
        return self.subject

    @property
    def actor(self) -> Optional[uuid.UUID]:
        """Whose work is counted: the narrowed employee; else, for a personal caller, the caller; else everybody."""
        return self.subject if self.team else self.ctx.user_id


def build_scope(ctx: StaffContext, period: Period, employee_id: Optional[str]) -> ReportScope:
    """The caller's scope for one request. Raises 400 for a malformed employee id and 403 when a non-team caller asks
    about anybody but themself."""
    requested = _person(employee_id, ctx, "employee_id")
    team = ctx.has(perms.PERM_LEADS_SCOPE_ALL)
    if not team and requested is not None and requested != ctx.user_id:
        raise access_denied()
    return ReportScope(ctx=ctx, period=period, team=team, subject=requested if team else None)
