import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.models import SalesCampaign, SalesLead


@dataclass(frozen=True)
class CampaignRow:
    campaign: SalesCampaign
    lead_count: int
    creator_name: Optional[str]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _counts_subquery(visibility):
    """Leads per campaign THAT THE CALLER MAY SEE (archived ones included: they still belong to it). `visibility` is the
    caller's lead-scope predicate (lead_access.lead_visibility) and is required on purpose: a campaign is visible to everyone
    with sales.campaigns.view, but how many leads it holds is an aggregate of leads, and an aggregate obeys the scope of the
    records it counts - an employee must not learn the team's lead volume from the campaign list."""
    return (
        select(SalesLead.campaign_id.label("cid"), func.count().label("n"))
        .where(SalesLead.campaign_id.is_not(None), visibility)
        .group_by(SalesLead.campaign_id)
        .subquery()
    )


async def get(db: AsyncSession, campaign_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesCampaign]:
    stmt = select(SalesCampaign).where(SalesCampaign.id == campaign_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def _row_select(counts):
    return (
        select(SalesCampaign, func.coalesce(counts.c.n, 0), User.name)
        .select_from(SalesCampaign)
        .outerjoin(counts, counts.c.cid == SalesCampaign.id)
        .outerjoin(User, User.id == SalesCampaign.created_by)
    )


async def get_row(db: AsyncSession, campaign_id: uuid.UUID, *, visibility) -> Optional[CampaignRow]:
    # populate_existing: re-read what the database now holds (server-side updated_at) after a flush
    row = (
        await db.execute(
            _row_select(_counts_subquery(visibility)).where(SalesCampaign.id == campaign_id),
            execution_options={"populate_existing": True},
        )
    ).first()
    return CampaignRow(row[0], int(row[1]), row[2]) if row else None


async def list_campaigns(
    db: AsyncSession,
    *,
    visibility,
    status: Optional[str],
    source: Optional[str],
    q: Optional[str],
    limit: int,
    offset: int,
) -> Tuple[List[CampaignRow], int]:
    conds = []
    if status:
        conds.append(SalesCampaign.status == status)
    if source:
        conds.append(SalesCampaign.source == source)
    q = q.replace("\x00", "") if q else q   # NUL cannot be stored, so it can never match; and must not reach the driver
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        conds.append(or_(SalesCampaign.name.ilike(pattern, escape="\\"), SalesCampaign.description.ilike(pattern, escape="\\")))

    total = (await db.execute(select(func.count()).select_from(SalesCampaign).where(*conds))).scalar_one()
    result = await db.execute(
        _row_select(_counts_subquery(visibility))
        .where(*conds)
        .order_by(SalesCampaign.created_at.desc(), SalesCampaign.id)
        .limit(limit)
        .offset(offset)
    )
    return [CampaignRow(campaign, int(n), creator) for campaign, n, creator in result.all()], total


async def name_taken(db: AsyncSession, name: str, *, exclude_id: Optional[uuid.UUID] = None) -> bool:
    """Same normalization as the unique index uq_sales_campaigns_name: lower(btrim(name))."""
    # lower/btrim on BOTH sides, in SQL, so the check and the unique index always agree (even on non-ASCII names)
    stmt = select(SalesCampaign.id).where(func.lower(func.btrim(SalesCampaign.name)) == func.lower(func.btrim(name))).limit(1)
    if exclude_id is not None:
        stmt = stmt.where(SalesCampaign.id != exclude_id)
    return (await db.execute(stmt)).first() is not None


async def lead_count(db: AsyncSession, campaign_id: uuid.UUID, *, visibility=None) -> int:
    """Leads of the campaign - ALL of them (archived and out-of-scope included) unless `visibility` narrows it. The all-leads
    form is for the delete guard only: the foreign key protects every lead, whoever may see it."""
    stmt = select(func.count()).select_from(SalesLead).where(SalesLead.campaign_id == campaign_id)
    if visibility is not None:
        stmt = stmt.where(visibility)
    return (await db.execute(stmt)).scalar_one()
