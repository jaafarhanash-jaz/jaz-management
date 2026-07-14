import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.subscription_plans as plans_repo
import repositories.users as users_repo
from services.qr import generate_qr_code
from services.auth import (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_SUSPENDED,
    hash_password,
    resolve_subscription_status,
)

EXPIRING_SOON_DAYS = 7
# A user counts as online if their last heartbeat arrived within this window.
# Same self-heal-on-read presence convention as before - no scheduler.
PRESENCE_TIMEOUT_SECONDS = 60


def parse_uuid(value) -> Optional[uuid.UUID]:
    """Path params arrive as strings; a malformed id must behave like an
    unknown id (404 downstream), never a 500."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def parse_dateish(value: str) -> datetime:
    """Accepts both 'YYYY-MM-DD' and full ISO datetimes (both shapes were
    written historically - admin flow vs. Stripe flow). Naive values are
    treated as UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def plan_config_for_company(plan) -> dict:
    """A company mirrors its assigned plan's full configuration whenever the
    plan is assigned, changed, or renewed - relational equivalent of the old
    generic plan->company field mapping."""
    return {
        "max_employees": plan.max_employees,
        "subscription_price": plan.price,
        "subscription_duration_months": plan.duration_months,
        "subscription_features": list(plan.features or []),
    }


async def _get_active_plan_or_400(db: AsyncSession, plan_id):
    plan = await plans_repo.get_by_id(db, parse_uuid(plan_id)) if parse_uuid(plan_id) else None
    if not plan or not plan.is_active:
        raise HTTPException(status_code=400, detail="Selected subscription plan is not available")
    return plan


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def company_to_response(company, *, employee_count: int = 0, owner=None, presence: Optional[dict] = None) -> dict:
    data = {
        "id": str(company.id),
        "name": company.name,
        "owner_id": str(company.owner_id),
        "qr_code": company.qr_code,
        "subscription_status": company.subscription_status,
        "subscription_plan_id": str(company.subscription_plan_id) if company.subscription_plan_id else None,
        "subscription_start_date": _iso(company.subscription_start_date),
        "subscription_end_date": _iso(company.subscription_end_date),
        "subscription_price": float(company.subscription_price or 0),
        "max_employees": company.max_employees,
        "subscription_duration_months": company.subscription_duration_months,
        "subscription_features": company.subscription_features,
        "address": company.address,
        "created_at": _iso(company.created_at),
        "employee_count": employee_count,
    }
    if owner is not None:
        data["owner_name"] = owner.name
        data["owner_email"] = owner.email
        data["owner_phone"] = owner.phone
    if presence is not None:
        data.update(presence)
    return data


async def _presence_by_company(db: AsyncSession, companies) -> dict:
    """Batched presence for a set of companies: one employee GROUP BY + one
    owner lookup, replacing the old per-company find loops."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=PRESENCE_TIMEOUT_SECONDS)
    company_ids = [c.id for c in companies]
    stats = await users_repo.employee_stats_by_company(db, company_ids, cutoff)
    owners = await users_repo.get_by_ids(db, [c.owner_id for c in companies])

    presence = {}
    for company in companies:
        owner = owners.get(str(company.owner_id))
        owner_last_seen = owner.last_seen_at if owner else None
        owner_online = bool(owner_last_seen and owner_last_seen >= cutoff)
        company_stats = stats.get(str(company.id), {})
        employees_online = company_stats.get("employees_online", 0)
        last_seens = [ls for ls in (owner_last_seen, company_stats.get("max_last_seen")) if ls]
        presence[str(company.id)] = {
            "owner_online": owner_online,
            "employees_online": employees_online,
            "company_online": owner_online or employees_online > 0,
            "last_seen": max(last_seens).isoformat() if last_seens else None,
            "_owner": owner,
            "_employee_count": company_stats.get("employee_count", 0),
        }
    return presence


async def get_statistics(db: AsyncSession) -> dict:
    companies = await companies_repo.list_all(db)
    total_companies = len(companies)
    total_employees = await users_repo.count_employees(db)
    presence_map = await _presence_by_company(db, companies)

    today = datetime.now(timezone.utc).date()
    soon_cutoff = today + timedelta(days=EXPIRING_SOON_DAYS)

    active_count = 0
    expired_count = 0
    total_revenue = 0.0
    expiring_soon_companies = []
    expired_companies_list = []
    companies_online_count = 0

    for company in companies:
        effective_status = await resolve_subscription_status(db, company)

        if presence_map[str(company.id)]["company_online"]:
            companies_online_count += 1

        if effective_status == SUBSCRIPTION_ACTIVE:
            # Revenue only counts Active companies - expired/suspended contribute nothing
            total_revenue += float(company.subscription_price or 0)
            active_count += 1
            end_date = company.subscription_end_date.date() if company.subscription_end_date else None
            if end_date and today <= end_date <= soon_cutoff:
                expiring_soon_companies.append({
                    "id": str(company.id),
                    "name": company.name,
                    "end_date": end_date.isoformat(),
                    "remaining_days": (end_date - today).days,
                })
        elif effective_status == SUBSCRIPTION_EXPIRED:
            expired_count += 1
            expired_companies_list.append({
                "id": str(company.id),
                "name": company.name,
                "end_date": company.subscription_end_date.date().isoformat() if company.subscription_end_date else "",
                "status": effective_status,
            })

    return {
        "total_companies": total_companies,
        "active_companies": active_count,
        "inactive_companies": total_companies - active_count,
        "total_employees": total_employees,
        "total_revenue": total_revenue,
        "expired_companies": expired_count,
        "expiring_soon_count": len(expiring_soon_companies),
        "expiring_soon_companies": expiring_soon_companies,
        "expired_companies_list": expired_companies_list,
        "companies_online": companies_online_count,
        "companies_offline": total_companies - companies_online_count,
    }


async def list_companies(db: AsyncSession) -> List[dict]:
    companies = await companies_repo.list_all(db)
    presence_map = await _presence_by_company(db, companies)

    results = []
    for company in companies:
        await resolve_subscription_status(db, company)  # self-heal before display
        entry = presence_map[str(company.id)]
        presence = {k: v for k, v in entry.items() if not k.startswith("_")}
        results.append(company_to_response(
            company,
            employee_count=entry["_employee_count"],
            owner=entry["_owner"],
            presence=presence,
        ))
    return results


async def create_company(db: AsyncSession, data) -> dict:
    if await users_repo.email_taken(db, data.owner_email):
        raise HTTPException(status_code=400, detail="Email already registered")
    # The old implementation never checked phone uniqueness (silently allowed
    # duplicates); the schema now enforces it, so check up front for a clear
    # 400 instead of a raw constraint error. Same shape as update_company's.
    if await users_repo.phone_taken(db, data.owner_phone):
        raise HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number already registered"})

    plan = await _get_active_plan_or_400(db, data.subscription_plan_id)

    company_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    # The attendance QR encodes only an opaque random token - never company data.
    qr_token = uuid.uuid4().hex
    qr_code = generate_qr_code(qr_token)

    await users_repo.create(
        db,
        id=owner_id,
        email=data.owner_email,
        phone=data.owner_phone,
        password=hash_password(data.owner_password),
        name=data.owner_name,
        role="company_owner",
        company_id=company_id,
        status="active",
    )
    company = await companies_repo.create(
        db,
        id=company_id,
        name=data.name,
        owner_id=owner_id,
        qr_code=qr_code,
        qr_token=qr_token,
        subscription_status="active",
        subscription_plan_id=plan.id,
        subscription_start_date=None,
        subscription_end_date=None,
        address=data.address,
        **plan_config_for_company(plan),
    )
    await db.flush()
    # Refresh server-generated timestamps before building the response.
    await db.refresh(company)
    return company_to_response(company, employee_count=0)


async def update_company(db: AsyncSession, company_id: str, updates) -> dict:
    company = await companies_repo.get_by_id(db, parse_uuid(company_id)) if parse_uuid(company_id) else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    owner = (await users_repo.get_by_ids(db, [company.owner_id])).get(str(company.owner_id))

    # Whitelist allowed company fields. subscription_status/dates/price are
    # intentionally excluded: status and dates only change via the dedicated
    # activate/suspend/reactivate/renew actions, and price is always derived
    # from the assigned plan, never client-set directly.
    company_changes = {}
    if updates.name is not None:
        company_changes["name"] = updates.name
    if updates.address is not None:
        company_changes["address"] = updates.address

    if updates.subscription_plan_id is not None:
        plan = await _get_active_plan_or_400(db, updates.subscription_plan_id)
        company_changes["subscription_plan_id"] = plan.id
        company_changes.update(plan_config_for_company(plan))

    # Validate every owner change before writing anything, so the company and
    # owner updates either both apply or neither does - now additionally
    # guaranteed by the surrounding transaction (unavailable on the old
    # standalone-MongoDB deployment).
    owner_changes = {}
    if updates.owner_name is not None:
        if not updates.owner_name.strip():
            raise HTTPException(status_code=400, detail={"field": "owner_name", "message": "Owner name is required"})
        owner_changes["name"] = updates.owner_name

    if updates.owner_email is not None:
        if await users_repo.email_taken(db, updates.owner_email, exclude_id=company.owner_id):
            raise HTTPException(status_code=400, detail={"field": "owner_email", "message": "Email already registered"})
        owner_changes["email"] = updates.owner_email

    if updates.owner_phone is not None:
        if not updates.owner_phone.strip():
            raise HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number is required"})
        if await users_repo.phone_taken(db, updates.owner_phone, exclude_id=company.owner_id):
            raise HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number already registered"})
        owner_changes["phone"] = updates.owner_phone

    if updates.owner_password or updates.owner_password_confirm:
        if updates.owner_password != updates.owner_password_confirm:
            raise HTTPException(status_code=400, detail={"field": "owner_password_confirm", "message": "Passwords do not match"})
        owner_changes["password"] = hash_password(updates.owner_password)

    if not company_changes and not owner_changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    for field, value in company_changes.items():
        setattr(company, field, value)
    if owner_changes and owner is not None and owner.role == "company_owner":
        for field, value in owner_changes.items():
            setattr(owner, field, value)
    await db.flush()

    return {"message": "Company updated successfully"}


async def activate_subscription(db: AsyncSession, company_id: str, data) -> dict:
    company = await companies_repo.get_by_id(db, parse_uuid(company_id)) if parse_uuid(company_id) else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    try:
        start = parse_dateish(data.subscription_start_date)
        end = parse_dateish(data.subscription_end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    company.subscription_status = SUBSCRIPTION_ACTIVE
    company.subscription_start_date = start
    company.subscription_end_date = end
    await db.flush()
    return {"message": "Subscription activated successfully"}


async def suspend_subscription(db: AsyncSession, company_id: str) -> dict:
    company = await companies_repo.get_by_id(db, parse_uuid(company_id)) if parse_uuid(company_id) else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.subscription_status = SUBSCRIPTION_SUSPENDED
    await db.flush()
    return {"message": "Subscription suspended successfully"}


async def reactivate_subscription(db: AsyncSession, company_id: str) -> dict:
    company = await companies_repo.get_by_id(db, parse_uuid(company_id)) if parse_uuid(company_id) else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.subscription_status != SUBSCRIPTION_SUSPENDED:
        raise HTTPException(status_code=400, detail="Company is not suspended")

    if company.subscription_end_date and company.subscription_end_date.date() < datetime.now(timezone.utc).date():
        raise HTTPException(
            status_code=400,
            detail="Subscription has expired and must be renewed by updating the subscription dates first",
        )

    company.subscription_status = SUBSCRIPTION_ACTIVE
    await db.flush()
    return {"message": "Subscription reactivated successfully"}


async def renew_subscription(db: AsyncSession, company_id: str, data) -> dict:
    company = await companies_repo.get_by_id(db, parse_uuid(company_id)) if parse_uuid(company_id) else None
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Renew on the current plan when no plan_id is given, otherwise switch.
    plan_id = data.subscription_plan_id or company.subscription_plan_id
    if not plan_id:
        raise HTTPException(status_code=400, detail="No subscription plan selected")
    plan = await _get_active_plan_or_400(db, plan_id)

    # Extend from the later of the current end date or today, so renewing
    # before expiry doesn't discard remaining time. Same 30-days-per-month
    # approximation as the Stripe success handler.
    today = datetime.now(timezone.utc).date()
    base_date = today
    if company.subscription_end_date:
        base_date = max(today, company.subscription_end_date.date())
    new_end = datetime.combine(base_date + timedelta(days=30 * plan.duration_months), datetime.min.time(), tzinfo=timezone.utc)

    company.subscription_plan_id = plan.id
    for field, value in plan_config_for_company(plan).items():
        setattr(company, field, value)
    company.subscription_status = SUBSCRIPTION_ACTIVE
    company.subscription_end_date = new_end
    await db.flush()
    return {"message": "Subscription renewed successfully"}


async def delete_company(db: AsyncSession, company_id: str) -> dict:
    # Soft delete (deleted_at) cascading to users/tasks/departments in one
    # transaction. Idempotent and quietly tolerant of unknown ids, exactly
    # like the old hard delete_one/delete_many sequence.
    parsed = parse_uuid(company_id)
    if parsed:
        await companies_repo.soft_delete_cascade(db, parsed, datetime.now(timezone.utc))
    return {"message": "Company deleted successfully"}


# ---- Subscription plans ----

def plan_to_response(plan) -> dict:
    return {
        "id": str(plan.id),
        "name": plan.name,
        "max_employees": plan.max_employees,
        "price": float(plan.price),
        "duration_months": plan.duration_months,
        "features": list(plan.features or []),
        "is_active": plan.is_active,
    }


async def list_plans(db: AsyncSession) -> List[dict]:
    return [plan_to_response(p) for p in await plans_repo.list_all(db)]


async def create_plan(db: AsyncSession, data) -> dict:
    try:
        plan = await plans_repo.create(db, **data.model_dump(), is_active=True)
    except IntegrityError:
        await db.rollback()
        # New DB-level rule (approved refinement): plan names are unique.
        raise HTTPException(status_code=400, detail="A plan with this name already exists")
    return plan_to_response(plan)


async def update_plan(db: AsyncSession, plan_id: str, updates) -> dict:
    changes = updates.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    plan = await plans_repo.get_by_id(db, parse_uuid(plan_id)) if parse_uuid(plan_id) else None
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    for field, value in changes.items():
        setattr(plan, field, value)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="A plan with this name already exists")
    return {"message": "Plan updated successfully"}


async def delete_plan(db: AsyncSession, plan_id: str) -> dict:
    parsed = parse_uuid(plan_id)
    if parsed:
        companies_using_plan = await companies_repo.count_by_plan(db, parsed)
        if companies_using_plan > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete this plan: {companies_using_plan} company(ies) are currently assigned to it",
            )
        # Only soft-deleted companies can still reference the plan at this
        # point; clear those dangling pointers so the FK doesn't block.
        await companies_repo.detach_plan_from_deleted_companies(db, parsed)
    if not parsed or not await plans_repo.delete_by_id(db, parsed):
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"message": "Plan deleted successfully"}
