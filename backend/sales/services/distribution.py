"""Automatic lead distribution (simplified workflow): the Sales Manager's ON/OFF setting and the round-robin that hands out
new leads.

  * WHAT. When automatic distribution is ON, a lead created WITHOUT a chosen owner goes to the next person in the rotation:
    everyone who may receive leads right now (repositories/leads.next_in_rotation - an active jaz_staff account holding
    sales.leads.scope_assigned through an active grant of an active role), in account-creation order, one lead each in turn
    ("Equal distribution" - the only mode). A deactivated account or a revoked role drops out of the rotation at once; a new
    Sales Employee joins at its end. Nobody eligible -> the lead simply stays unassigned (`new`), exactly as with the switch OFF.
  * NOT WHAT. No territory, country, skill or workload rules. Assigning by hand (at creation, or later: assign / reassign /
    bulk assign) is untouched and does not move the rotation.
  * DETERMINISTIC AND RACE-FREE. The settings row is locked while a lead picks its owner, so two leads created at the same
    moment take consecutive turns; the chosen account is share-locked exactly like a manual assignee, so it cannot be
    deactivated between being picked and the lead committing.
"""
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.repositories import leads as leads_repo
from sales.repositories import settings as settings_repo
from sales.services import audit as audit_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext

logger = logging.getLogger("sales.distribution")

# A person picked from the rotation can lose eligibility in the instant before their account is locked; the next one is
# tried. Bounded so a pathological race can never loop.
_MAX_PICK_ATTEMPTS = 5

_SETTING_FIELDS = ("auto_distribution_enabled", "distribution_mode")


def _person(user: Optional[User]) -> Optional[dict]:
    return {"id": str(user.id), "name": user.name} if user is not None else None


async def pick_assignee(db: AsyncSession) -> Optional[User]:
    """The owner automatic distribution gives the lead being created, or None (switched off / nobody eligible). Advances the
    rotation. Called inside the lead-creating transaction, after every validation, right before the lead is written."""
    current = await settings_repo.get(db)                   # plain read: no lock at all while the switch is OFF
    if current is None or not current.auto_distribution_enabled:
        return None
    settings = await settings_repo.get_for_update(db)       # take turns with every concurrent lead creation
    if not settings.auto_distribution_enabled:              # switched off meanwhile
        return None

    after = await leads_repo.rotation_key(db, settings.last_assigned_user_id) if settings.last_assigned_user_id else None
    for _ in range(_MAX_PICK_ATTEMPTS):
        candidate = await leads_repo.next_in_rotation(db, after)
        if candidate is None:
            return None
        user = await leads_repo.get_assignable_user(db, candidate.id, lock=True)   # re-checked under the account's lock
        if user is not None:
            settings.last_assigned_user_id = user.id
            await db.flush()
            return user
        after = (candidate.created_at, candidate.id)        # lost eligibility just now: the next one gets the turn
    return None


async def get_distribution(db: AsyncSession) -> dict:
    """The setting, and the rotation it would use right now (in order, with each person's open workload, and who is next)."""
    settings = await settings_repo.get(db)
    rotation = await leads_repo.list_rotation(db)
    after = None
    if settings is not None and settings.last_assigned_user_id is not None:
        after = await leads_repo.rotation_key(db, settings.last_assigned_user_id)
    nxt = await leads_repo.next_in_rotation(db, after) if rotation else None
    updated_by = await db.get(User, settings.updated_by) if settings is not None and settings.updated_by else None
    return {
        "auto_distribution_enabled": bool(settings and settings.auto_distribution_enabled),
        "distribution_mode": settings.distribution_mode if settings is not None else "equal",
        "updated_at": settings.updated_at if settings is not None else None,
        "updated_by": _person(updated_by),
        "rotation": [
            {"id": str(user.id), "name": user.name, "email": user.email, "open_leads": open_leads}
            for user, open_leads in rotation
        ],
        "next_assignee": _person(nxt),
    }


async def update_distribution(db: AsyncSession, ctx: StaffContext, body, audit: AuditContext) -> dict:
    settings = await settings_repo.get_for_update(db)
    before = {field: getattr(settings, field) for field in _SETTING_FIELDS}
    after = {"auto_distribution_enabled": body.auto_distribution_enabled, "distribution_mode": body.distribution_mode}
    if before != after:
        settings.auto_distribution_enabled = body.auto_distribution_enabled
        settings.distribution_mode = body.distribution_mode
        settings.updated_by = ctx.user_id
        settings.updated_at = func.clock_timestamp()
        await db.flush()
        await audit_service.record(
            db, ctx, audit,
            action=audit_service.ACTION_SALES_SETTINGS_UPDATED, target_type=audit_service.TARGET_SALES_SETTINGS,
            before=before, after=after,
        )
        logger.info("sales_settings_updated actor=%s auto_distribution=%s mode=%s", ctx.user_id,
                    body.auto_distribution_enabled, body.distribution_mode)
    return await get_distribution(db)
