"""Row-level lead visibility: WHICH leads may this caller see and act on?

Sits on top of the Phase-1 RBAC and adds no second system: the endpoint's `require_permission(...)` decides
whether the caller may perform an action at all, and the three lead-scope permissions decide on which rows.

    sales.leads.scope_all       every lead                                        (Sales Manager, Super Admin)
    sales.leads.scope_assigned  leads assigned to the caller                      (Sales Employee)
    sales.leads.scope_intake    leads the caller created + unassigned leads       (Lead Data Entry)

A caller's scope is the UNION of the scopes they hold; holding none means no lead at all (fail closed). Only
permissions are consulted - never role names - so a custom role assembled from the same keys behaves the same.

The SQL predicate (lead_visibility) and its Python mirror (can_see) must stay equivalent: listings filter with
the former, while rows fetched by other queries (duplicate matches) are judged with the latter.
tests/test_sales_leads_unit.py checks them against each other over every scope combination.
"""
import uuid
from typing import FrozenSet, Optional

from sqlalchemy import false, or_, true

from sales import permissions as perms
from sales.models import SalesLead
from sales.services.access import StaffContext


def lead_visibility(ctx: StaffContext):
    """SQL predicate over SalesLead: the rows this caller may see."""
    if ctx.has(perms.PERM_LEADS_SCOPE_ALL):
        return true()
    clauses = []
    if ctx.has(perms.PERM_LEADS_SCOPE_ASSIGNED):
        clauses.append(SalesLead.assigned_to == ctx.user_id)
    if ctx.has(perms.PERM_LEADS_SCOPE_INTAKE):
        clauses.append(or_(SalesLead.created_by == ctx.user_id, SalesLead.assigned_to.is_(None)))
    return or_(*clauses) if clauses else false()


def can_see(ctx: StaffContext, *, created_by: Optional[uuid.UUID], assigned_to: Optional[uuid.UUID]) -> bool:
    """Python mirror of lead_visibility for a lead already in hand."""
    if ctx.has(perms.PERM_LEADS_SCOPE_ALL):
        return True
    if ctx.has(perms.PERM_LEADS_SCOPE_ASSIGNED) and assigned_to is not None and assigned_to == ctx.user_id:
        return True
    if ctx.has(perms.PERM_LEADS_SCOPE_INTAKE) and (assigned_to is None or created_by == ctx.user_id):
        return True
    return False


# ---- the edit window (simplified workflow) -----------------------------------------------------------------------------
# Seeing a lead is not the same as being allowed to CHANGE it (edit its details, archive it, restore it). Every scope still
# reaches the leads it reached before, but through the intake scope only the caller's own most recent leads - and only while
# they are still UNASSIGNED - may be changed:
#
#     sales.leads.scope_all        every lead it sees                                   (Sales Manager: the override)
#     sales.leads.scope_assigned   the leads assigned to the caller                     (Sales Employee)
#     sales.leads.scope_intake     ONLY the caller's own RECENT_EDIT_WINDOW latest leads that nobody owns yet (Lead Data Entry)
#
# The moment a lead is handed to Sales it is Sales' to work: Data Entry keeps seeing it (read-only) but can no longer edit,
# archive, delete or restore it - which also means Data Entry can never touch a lead a salesperson has put on their wait list.
# Older leads, leads somebody else created and assigned leads are the Sales Manager's to change. Permissions only, never role
# names.

# ---- there is no way to win a lead by hand ---------------------------------------------------------------------------------
# A lead is WON in exactly one way: the Customer Setup (the salesperson's "Agreed"), which wins the lead AND creates the customer,
# the JAZ company and its subscription in one transaction. The pipeline's own `won` move (POST /leads/{id}/stage) would win a lead
# with no customer at all (and count it as an accepted result), so it is closed to EVERY caller - Sales Employee, Sales Manager and
# Super Admin alike: services/leads.change_stage refuses it (403 `won_requires_customer_setup`) and no lead ever lists `won` among
# its allowed stages. Nothing here depends on a permission or a role for that: it is a rule of the pipeline itself.

def needs_recent_leads(ctx: StaffContext) -> bool:
    """Whether deciding may_modify for this caller needs their latest created lead ids (only the intake route does)."""
    return ctx.has(perms.PERM_LEADS_SCOPE_INTAKE) and not ctx.has(perms.PERM_LEADS_SCOPE_ALL)


def may_modify(
    ctx: StaffContext,
    *,
    lead_id: uuid.UUID,
    created_by: Optional[uuid.UUID],
    assigned_to: Optional[uuid.UUID],
    recent_ids: FrozenSet[uuid.UUID] = frozenset(),
) -> bool:
    """May this caller change this lead's details / archive state? `recent_ids` = the ids of the caller's latest
    RECENT_EDIT_WINDOW created leads (see needs_recent_leads). Through the intake scope the lead must also still be
    UNASSIGNED. The action permission itself is checked separately."""
    if ctx.has(perms.PERM_LEADS_SCOPE_ALL):
        return True
    if ctx.has(perms.PERM_LEADS_SCOPE_ASSIGNED) and assigned_to is not None and assigned_to == ctx.user_id:
        return True
    if ctx.has(perms.PERM_LEADS_SCOPE_INTAKE) and created_by == ctx.user_id and assigned_to is None and lead_id in recent_ids:
        return True
    return False
