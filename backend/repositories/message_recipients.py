import uuid
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import MessageRecipient


async def create(db: AsyncSession, **fields) -> MessageRecipient:
    fields.setdefault("id", uuid.uuid4())
    row = MessageRecipient(**fields)
    db.add(row)
    await db.flush()
    return row


async def get_participant(db: AsyncSession, thread_id, recipient_id) -> Optional[MessageRecipient]:
    result = await db.execute(
        select(MessageRecipient).where(
            MessageRecipient.thread_id == thread_id, MessageRecipient.recipient_id == recipient_id
        )
    )
    return result.scalar_one_or_none()


async def get_recipient_row(db: AsyncSession, thread_id, recipient_id) -> Optional[MessageRecipient]:
    result = await db.execute(
        select(MessageRecipient).where(
            MessageRecipient.thread_id == thread_id,
            MessageRecipient.recipient_id == recipient_id,
            MessageRecipient.role == "recipient",
        )
    )
    return result.scalar_one_or_none()


async def list_for_threads(db: AsyncSession, thread_ids) -> List[MessageRecipient]:
    if not thread_ids:
        return []
    result = await db.execute(select(MessageRecipient).where(MessageRecipient.thread_id.in_(list(thread_ids))))
    return list(result.scalars().all())


async def list_for_thread(db: AsyncSession, thread_id) -> List[MessageRecipient]:
    result = await db.execute(select(MessageRecipient).where(MessageRecipient.thread_id == thread_id))
    return list(result.scalars().all())


async def list_recipient_only_for_thread(db: AsyncSession, thread_id) -> List[MessageRecipient]:
    result = await db.execute(
        select(MessageRecipient).where(MessageRecipient.thread_id == thread_id, MessageRecipient.role == "recipient")
    )
    return list(result.scalars().all())


async def mark_unread_bulk(db: AsyncSession, thread_id, participant_ids) -> None:
    if not participant_ids:
        return
    await db.execute(
        update(MessageRecipient)
        .where(MessageRecipient.thread_id == thread_id, MessageRecipient.recipient_id.in_(list(participant_ids)))
        .values(is_unread=True)
    )


async def thread_ids_for_recipient(db: AsyncSession, recipient_id, *, exclude_archived=False,
                                    unread_only=False, role=None, status=None, starred=None) -> List:
    conditions = [MessageRecipient.recipient_id == recipient_id]
    if status:
        # An explicit status filter replaces (not ANDs with) the
        # exclude-archived default - matches the old Mongo query-dict
        # behavior where setting "status" overwrote the earlier {"$ne":
        # "archived"} key rather than combining with it.
        conditions.append(MessageRecipient.status == status)
    elif exclude_archived:
        conditions.append(MessageRecipient.status != "archived")
    if unread_only:
        conditions.append(MessageRecipient.is_unread.is_(True))
    if role:
        conditions.append(MessageRecipient.role == role)
    if starred is not None:
        conditions.append(MessageRecipient.is_starred.is_(starred))
    result = await db.execute(select(MessageRecipient.thread_id).where(*conditions))
    return [row[0] for row in result.all()]


async def thread_ids_matching(db: AsyncSession, company_id, *, recipient_name=None, status=None) -> List:
    conditions = [MessageRecipient.company_id == company_id, MessageRecipient.role == "recipient"]
    if recipient_name:
        conditions.append(MessageRecipient.recipient_name.ilike(f"%{recipient_name}%"))
    if status:
        conditions.append(MessageRecipient.status == status)
    result = await db.execute(select(MessageRecipient.thread_id).where(*conditions))
    return [row[0] for row in result.all()]
