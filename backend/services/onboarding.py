from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# First-Time Company Setup Wizard (Part 1) - Role checks happen in the route
# handlers (server.py), matching services/work_locations.py's own
# convention - this layer trusts it was already checked. State lives on
# Company (account-scoped), never on the device - see models.py's
# onboarding_* columns for why the default is fail-closed ('completed').
# ---------------------------------------------------------------------------

ONBOARDING_STEPS = [
    "welcome",
    "branches",
    "branch_locations",
    "attendance_qr",
    "work_shifts",
    "employees",
    "employee_tasks",
    "completion",
]


def onboarding_response(company) -> dict:
    return {
        "status": company.onboarding_status,
        "current_step": company.onboarding_current_step,
        "completed_at": company.onboarding_completed_at.isoformat() if company.onboarding_completed_at else None,
    }


async def _get_owned_company_or_404(db: AsyncSession, current_user: dict):
    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


async def get_status(db: AsyncSession, current_user: dict) -> dict:
    company = await _get_owned_company_or_404(db, current_user)
    return onboarding_response(company)


async def set_step(db: AsyncSession, current_user: dict, step: str) -> dict:
    if step not in ONBOARDING_STEPS:
        raise HTTPException(status_code=400, detail="Unknown onboarding step")
    company = await _get_owned_company_or_404(db, current_user)
    # Only the dedicated, confirmed restart() path may reopen a company
    # that already finished setup - otherwise a stray/late call (a race,
    # a client bug, a cached request replayed) would silently pop the
    # wizard gate back open on a graduated owner.
    if company.onboarding_status == "completed":
        raise HTTPException(status_code=400, detail="Onboarding already completed")

    await companies_repo.set_onboarding_step(db, company.id, status="in_progress", current_step=step)
    await db.flush()
    await db.refresh(company)
    return onboarding_response(company)


async def complete(db: AsyncSession, current_user: dict) -> dict:
    company = await _get_owned_company_or_404(db, current_user)
    await companies_repo.complete_onboarding(db, company.id, completed_at=datetime.now(timezone.utc))
    await db.flush()
    await db.refresh(company)
    return onboarding_response(company)


async def restart(db: AsyncSession, current_user: dict, confirm: bool) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="Restart requires confirmation")
    company = await _get_owned_company_or_404(db, current_user)
    await companies_repo.restart_onboarding(
        db, company.id, status="in_progress", current_step=ONBOARDING_STEPS[0],
    )
    await db.flush()
    await db.refresh(company)
    return onboarding_response(company)
