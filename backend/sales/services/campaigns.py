"""Sales campaigns (Phase 2): plain CRUD for authorized Sales users.

A campaign is a label that leads may optionally belong to. Deleting one can never take leads with it: the
foreign key from sales_leads is RESTRICT, and the API refuses (409) while any lead - archived ones included -
still belongs to the campaign. To retire a campaign that has leads, mark it `completed`.
"""
import logging
import uuid
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sales.models import SalesCampaign
from sales.repositories import campaigns as campaigns_repo
from sales.repositories import leads as leads_repo
from sales.repositories.campaigns import CampaignRow
from sales.services.access import StaffContext
from sales.services.common import field_error
from sales.services.lead_access import lead_visibility
from services.admin import parse_uuid

logger = logging.getLogger("sales.campaigns")

_NOT_CLEARABLE = ("name", "status")


def campaign_out(row: CampaignRow) -> dict:
    c = row.campaign
    return {
        "id": str(c.id),
        "name": c.name,
        "description": c.description,
        "source": c.source,
        "status": c.status,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "created_by": {"id": str(c.created_by), "name": row.creator_name or ""},
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "lead_count": row.lead_count,
    }


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Campaign not found")


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """The unique index / FKs are the real guarantee behind the pre-checks (a concurrent request can win the race)."""
    text = str(getattr(exc, "orig", exc))
    if "uq_sales_campaigns_name" in text:
        return field_error("name", "A campaign with this name already exists")
    if "sales_campaigns_source_fkey" in text:
        return field_error("source", "Unknown lead source")
    if "sales_leads_campaign_id_fkey" in text:
        return field_error("campaign", "This campaign still has leads and cannot be deleted", 409, code="campaign_has_leads")
    raise exc


async def _validate(db: AsyncSession, values: Dict[str, Any], *, current: SalesCampaign = None) -> None:
    if values.get("source") is not None and not await leads_repo.is_active_source(db, values["source"]):
        raise field_error("source", "Unknown lead source")
    start = values.get("start_date", current.start_date if current else None)
    end = values.get("end_date", current.end_date if current else None)
    if start is not None and end is not None and end < start:
        raise field_error("end_date", "The end date cannot be before the start date")
    if "name" in values and await campaigns_repo.name_taken(db, values["name"], exclude_id=current.id if current else None):
        raise field_error("name", "A campaign with this name already exists")


async def list_campaigns(db: AsyncSession, ctx: StaffContext, *, status, source, q, limit: int, offset: int) -> dict:
    rows, total = await campaigns_repo.list_campaigns(
        db, visibility=lead_visibility(ctx), status=status, source=source, q=q, limit=limit, offset=offset
    )
    return {"items": [campaign_out(r) for r in rows], "total": total, "limit": limit, "offset": offset}


async def get_campaign(db: AsyncSession, ctx: StaffContext, campaign_id: str) -> dict:
    parsed = parse_uuid(campaign_id)
    row = await campaigns_repo.get_row(db, parsed, visibility=lead_visibility(ctx)) if parsed else None
    if row is None:
        raise _not_found()
    return campaign_out(row)


async def create_campaign(db: AsyncSession, ctx: StaffContext, body) -> dict:
    values = body.model_dump()
    await _validate(db, values)
    campaign = SalesCampaign(id=uuid.uuid4(), created_by=ctx.user_id, **values)
    try:
        async with db.begin_nested():  # savepoint: a lost race on the unique name must not poison the transaction
            db.add(campaign)
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)
    logger.info("campaign_created actor=%s campaign=%s", ctx.user_id, campaign.id)
    return campaign_out(await campaigns_repo.get_row(db, campaign.id, visibility=lead_visibility(ctx)))


async def update_campaign(db: AsyncSession, ctx: StaffContext, campaign_id: str, body) -> dict:
    parsed = parse_uuid(campaign_id)
    campaign = await campaigns_repo.get(db, parsed, for_update=True) if parsed else None
    if campaign is None:
        raise _not_found()
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    for field in _NOT_CLEARABLE:
        if field in values and values[field] is None:
            raise field_error(field, "This field cannot be empty")
    changes = {f: v for f, v in values.items() if getattr(campaign, f) != v}
    if changes:
        await _validate(db, changes, current=campaign)
        for field, value in changes.items():
            setattr(campaign, field, value)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc)
        logger.info("campaign_updated actor=%s campaign=%s fields=%s", ctx.user_id, campaign.id, sorted(changes))
    return campaign_out(await campaigns_repo.get_row(db, campaign.id, visibility=lead_visibility(ctx)))


async def delete_campaign(db: AsyncSession, ctx: StaffContext, campaign_id: str) -> dict:
    parsed = parse_uuid(campaign_id)
    campaign = await campaigns_repo.get(db, parsed, for_update=True) if parsed else None
    if campaign is None:
        raise _not_found()
    count = await campaigns_repo.lead_count(db, campaign.id)            # every lead: the foreign key protects them all
    if count:
        # The number is only given to a caller who can see every one of those leads; anybody else learns that there are
        # leads, not how many (an out-of-scope count is an aggregate they may not have).
        if await campaigns_repo.lead_count(db, campaign.id, visibility=lead_visibility(ctx)) == count:
            raise field_error(
                "campaign",
                f"This campaign still has {count} lead(s) and cannot be deleted. Mark it as completed instead.",
                409, code="campaign_has_leads", lead_count=count,
            )
        raise field_error(
            "campaign", "This campaign still has leads and cannot be deleted. Mark it as completed instead.",
            409, code="campaign_has_leads",
        )
    try:
        async with db.begin_nested():
            await db.delete(campaign)
            await db.flush()
    except IntegrityError as exc:  # a lead was attached a moment ago: the FK stops it, and so do we
        raise _translate_integrity_error(exc)
    logger.info("campaign_deleted actor=%s campaign=%s", ctx.user_id, parsed)
    return {"deleted": True}
