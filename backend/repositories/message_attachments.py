import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import MessageAttachment


async def create(db: AsyncSession, **fields) -> MessageAttachment:
    fields.setdefault("id", uuid.uuid4())
    attachment = MessageAttachment(**fields)
    db.add(attachment)
    await db.flush()
    return attachment


async def get_by_id(db: AsyncSession, attachment_id):
    result = await db.execute(select(MessageAttachment).where(MessageAttachment.id == attachment_id))
    return result.scalar_one_or_none()


async def list_by_thread(db: AsyncSession, thread_id) -> List[MessageAttachment]:
    result = await db.execute(select(MessageAttachment).where(MessageAttachment.thread_id == thread_id))
    return list(result.scalars().all())


async def list_by_threads_counts(db: AsyncSession, thread_ids) -> dict:
    if not thread_ids:
        return {}
    from sqlalchemy import func
    result = await db.execute(
        select(MessageAttachment.thread_id, func.count())
        .where(MessageAttachment.thread_id.in_(list(thread_ids)))
        .group_by(MessageAttachment.thread_id)
    )
    return {str(tid): count for tid, count in result.all()}


async def list_by_message(db: AsyncSession, message_id) -> List[MessageAttachment]:
    result = await db.execute(select(MessageAttachment).where(MessageAttachment.message_id == message_id))
    return list(result.scalars().all())


async def thread_ids_by_company_and_type(db: AsyncSession, company_id, attachment_type: str) -> List:
    result = await db.execute(
        select(MessageAttachment.thread_id).where(
            MessageAttachment.company_id == company_id, MessageAttachment.attachment_type == attachment_type
        )
    )
    return [row[0] for row in result.all()]
