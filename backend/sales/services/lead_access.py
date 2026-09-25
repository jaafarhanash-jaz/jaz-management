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
from typing import Optional

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
