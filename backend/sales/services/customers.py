"""Customers (Phase 4): what a won lead became - a JAZ company and an onboarding to run.

Authorization is layered exactly like the rest of JAZ Sales:
  1. the router's require_permission(...) decides whether the caller may perform the ACTION at all
     (sales.customers.view / sales.customers.convert);
  2. lead_access decides on WHICH customers: a customer is visible exactly when the lead it came from is - there is no
     second scope system. A customer whose lead is outside the caller's scope answers 403 "Access denied", an unknown
     or malformed id answers 404;
  3. the services enforce the business rules (services/conversion.py, services/onboarding.py).

A customer's `status` is never edited here: it follows the onboarding stage and is written only by the onboarding
service, in the same transaction as the stage change.
"""
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.repositories import customers as customers_repo
from sales.repositories.customers import CustomerFilters, CustomerRow
from sales.services import work
from sales.services.access import StaffContext
from sales.services.lead_access import lead_visibility
from sales.services.onboarding_access import can_see_onboarding
from services.admin import parse_uuid


def customer_out(row: CustomerRow, ctx: StaffContext, *, employee_count: Optional[int] = None) -> dict:
    """The API shape of a customer for THIS caller. The onboarding summary is shown only to a caller who may view that
    onboarding record (customer visibility does not imply onboarding visibility)."""
    c = row.customer
    sees_onboarding = (
        row.onboarding_id is not None
        and ctx.has(perms.PERM_ONBOARDING_VIEW)
        and can_see_onboarding(ctx, assigned_to=row.onboarding_assigned_to)
    )
    return {
        "id": str(c.id),
        "status": c.status,
        "converted_at": c.converted_at,
        "converted_by": work.user_ref(c.converted_by, row.converter_name),
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "lead": {
            **work.lead_ref(row.lead),
            "contact_name": row.contact_name, "phone": row.phone, "email": row.email, "city": row.city,
        },
        "company": {
            "id": str(c.company_id), "name": row.company_name, "subscription_status": row.company_subscription_status,
            "deleted": row.company_deleted, "owner_name": row.owner_name, "owner_email": row.owner_email,
            "owner_phone": row.owner_phone, "employee_count": employee_count,
        },
        "onboarding": {
            "id": str(row.onboarding_id), "stage": row.onboarding_stage,
            "assigned_to": work.user_ref(row.onboarding_assigned_to, row.onboarding_assignee_name, row.onboarding_assignee_status),
        } if sees_onboarding else None,
        "can": {"view_lead": ctx.has(perms.PERM_LEADS_VIEW), "view_onboarding": sees_onboarding},
    }


async def list_customers(
    db: AsyncSession, ctx: StaffContext, filters: CustomerFilters, *, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    rows, total = await customers_repo.list_rows(
        db, visibility=lead_visibility(ctx), filters=filters, sort=sort, descending=descending, limit=limit, offset=offset
    )
    return {"items": [customer_out(row, ctx) for row in rows], "total": total, "limit": limit, "offset": offset}


async def status_counts(db: AsyncSession, ctx: StaffContext, filters: CustomerFilters) -> dict:
    return await customers_repo.status_counts(db, visibility=lead_visibility(ctx), filters=filters)


async def get_customer(db: AsyncSession, ctx: StaffContext, customer_id: str) -> dict:
    parsed = parse_uuid(customer_id)
    row = await customers_repo.get_row(db, parsed) if parsed else None
    if row is None:
        raise work.not_found("Customer")
    work.ensure_visible(ctx, row.lead.created_by, row.lead.assigned_to)
    return customer_out(row, ctx, employee_count=await customers_repo.company_employee_count(db, row.customer.company_id))


async def one(db: AsyncSession, ctx: StaffContext, customer_id: uuid.UUID) -> dict:
    """The customer just written, in the API shape (no permission logic: the caller has already passed it)."""
    return customer_out(await customers_repo.get_row(db, customer_id), ctx)
