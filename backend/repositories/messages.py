import uuid
from typing import List, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Message


async def create(db: AsyncSession, **fields) -> Message:
    fields.setdefault("id", uuid.uuid4())
    message = Message(**fields)
    db.add(message)
    await db.flush()
    return message


async def get_by_id(db: AsyncSession, message_id) -> Optional[Message]:
    result = await db.execute(select(Message).where(Message.id == message_id))
    return result.scalar_one_or_none()


async def get_in_company(db: AsyncSession, message_id, company_id) -> Optional[Message]:
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.company_id == company_id)
    )
    return result.scalar_one_or_none()


async def list_thread(db: AsyncSession, thread_id) -> List[Message]:
    result = await db.execute(select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.asc()))
    return list(result.scalars().all())


async def count_replies_by_thread(db: AsyncSession, thread_ids) -> dict:
    if not thread_ids:
        return {}
    result = await db.execute(
        select(Message.thread_id, func.count()).where(Message.thread_id.in_(list(thread_ids))).group_by(Message.thread_id)
    )
    return {str(tid): count for tid, count in result.all()}


class MessageQuery:
    """Incrementally-built filtered query for the several list endpoints
    (inbox/sent/drafts/starred/archived/communication-center), replacing
    the old Mongo query-dict-building pattern with real SQL conditions."""

    def __init__(self, company_id):
        self.conditions = [Message.company_id == company_id]
        self._recipient_join = False

    def where(self, *conditions):
        self.conditions.extend(conditions)
        return self

    def subject_like(self, subject: Optional[str]):
        if subject:
            self.conditions.append(Message.subject.ilike(f"%{subject}%"))
        return self

    def priority_eq(self, priority: Optional[str]):
        if priority:
            self.conditions.append(Message.priority == priority)
        return self

    def tags_overlap(self, tags: Optional[str]):
        if tags:
            values = [t.strip() for t in tags.split(",") if t.strip()]
            if values:
                self.conditions.append(Message.tags.overlap(values))
        return self

    def sender_name_like(self, sender: Optional[str]):
        if sender:
            self.conditions.append(Message.sender_name.ilike(f"%{sender}%"))
        return self

    def reference_number_like(self, reference_number: Optional[str]):
        if reference_number:
            self.conditions.append(Message.reference_number.ilike(f"%{reference_number}%"))
        return self

    def department_eq(self, department: Optional[str]):
        if department:
            self.conditions.append(Message.recipient_department == department)
        return self

    def created_between(self, date_from: Optional, date_to: Optional):
        if date_from:
            self.conditions.append(Message.created_at >= date_from)
        if date_to:
            self.conditions.append(Message.created_at <= date_to)
        return self

    def thread_ids_in(self, thread_ids):
        self.conditions.append(Message.id.in_(list(thread_ids)))
        return self

    async def count(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count()).select_from(Message).where(and_(*self.conditions)))
        return result.scalar_one()

    async def page(self, db: AsyncSession, page: int, page_size: int, sort_field) -> List[Message]:
        result = await db.execute(
            select(Message)
            .where(and_(*self.conditions))
            .order_by(Message.is_pinned.desc(), sort_field.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all())
