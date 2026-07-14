import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarAttachment


async def create(db: AsyncSession, **fields) -> CalendarAttachment:
    fields.setdefault("id", uuid.uuid4())
    attachment = CalendarAttachment(**fields)
    db.add(attachment)
    await db.flush()
    return attachment


async def get_by_id(db: AsyncSession, attachment_id) -> Optional[CalendarAttachment]:
    result = await db.execute(select(CalendarAttachment).where(CalendarAttachment.id == attachment_id))
    return result.scalar_one_or_none()


async def count_for_event(db: AsyncSession, event_id) -> int:
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(CalendarAttachment).where(CalendarAttachment.event_id == event_id)
    )
    return result.scalar_one()
