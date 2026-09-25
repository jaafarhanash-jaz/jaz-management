"""Persistence helpers shared by the Phase-3 work items (calls, follow-ups, demos, trials).

Every list/detail read of a work item joins the lead and the people it names in ONE query, so a page of items costs
one query for the rows + one for the total - never one per item. Lists apply the caller's lead scope
(services/lead_access.lead_visibility) to the joined lead, which is what makes "an item is visible exactly when its
lead is" a single predicate rather than a second permission system.
"""
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales import permissions as perms
from sales.models import SalesLead
from sales.timezone import day_start


@dataclass(frozen=True)
class LeadInfo:
    """The few columns of a lead that a work item's list row carries: what it is called (for the link), where it
    stands, whether it is archived, and the two facts the scope check (lead_access.can_see) needs."""

    id: uuid.UUID
    business_name: str
    pipeline_stage: str
    archived: bool
    created_by: uuid.UUID
    assigned_to: Optional[uuid.UUID]


# Selected right after the work item's own entity, in this order (LeadInfo(*row[1:1 + len(LEAD_COLUMNS)])).
LEAD_COLUMNS = (
    SalesLead.id,
    SalesLead.business_name,
    SalesLead.pipeline_stage,
    SalesLead.archived_at.is_not(None),
    SalesLead.created_by,
    SalesLead.assigned_to,
)


def lead_info(row) -> LeadInfo:
    return LeadInfo(*row[1:1 + len(LEAD_COLUMNS)])


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def day_bounds(start: Optional[date], end: Optional[date]):
    """(inclusive lower bound, exclusive upper bound) for two inclusive calendar dates in the application timezone (Baghdad,
    sales/timezone.py); either may be None."""
    return (day_start(start) if start is not None else None), (day_start(end + timedelta(days=1)) if end is not None else None)


def search_pattern(q: Optional[str]) -> Optional[str]:
    """A LIKE pattern for a free-text search box, or None for "no search". NUL cannot be stored, so it can never match
    and must not reach the driver."""
    needle = (q or "").replace("\x00", "").strip()
    return f"%{escape_like(needle)}%" if needle else None


async def get_lead_shared(db: AsyncSession, lead_id: uuid.UUID) -> Optional[SalesLead]:
    """The lead, read under a SHARE lock. Creating (or changing) a work item reads the lead, checks that it is in the
    caller's scope and not archived, and only then writes; the lock keeps a concurrent archive / restore /
    reassignment - which need the row exclusively - from slipping in between the check and the write. Several work
    items can still be logged on one lead at the same time (SHARE locks do not conflict with each other)."""
    stmt = select(SalesLead).where(SalesLead.id == lead_id).with_for_update(read=True, of=SalesLead)
    return (await db.execute(stmt, execution_options={"populate_existing": True})).scalar_one_or_none()


async def get_usable_staff_user(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> Optional[User]:
    """The user, if they are an ACTIVE `jaz_staff` account that is not deleted. lock=True takes a shared row lock, so
    the account cannot be deactivated by a concurrent request between this check and the write committing."""
    stmt = select(User).where(
        User.id == user_id,
        User.role == perms.PLATFORM_ROLE_STAFF,
        User.status == "active",
        User.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update(read=True, of=User)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
