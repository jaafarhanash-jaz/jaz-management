import uuid
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CalendarEventParticipant


async def create(db: AsyncSession, **fields) -> CalendarEventParticipant:
    fields.setdefault("id", uuid.uuid4())
    row = CalendarEventParticipant(**fields)
    db.add(row)
    await db.flush()
    return row


async def get_participant(db: AsyncSession, event_id, participant_id) -> Optional[CalendarEventParticipant]:
    result = await db.execute(
        select(CalendarEventParticipant).where(
            CalendarEventParticipant.event_id == event_id,
            CalendarEventParticipant.participant_id == participant_id,
        )
    )
    return result.scalar_one_or_none()


async def list_for_event(db: AsyncSession, event_id) -> List[CalendarEventParticipant]:
    result = await db.execute(select(CalendarEventParticipant).where(CalendarEventParticipant.event_id == event_id))
    return list(result.scalars().all())


async def list_for_events(db: AsyncSession, event_ids) -> List[CalendarEventParticipant]:
    if not event_ids:
        return []
    result = await db.execute(
        select(CalendarEventParticipant).where(CalendarEventParticipant.event_id.in_(list(event_ids)))
    )
    return list(result.scalars().all())


async def participant_ids_for_event(db: AsyncSession, event_id) -> List[uuid.UUID]:
    result = await db.execute(
        select(CalendarEventParticipant.participant_id).where(CalendarEventParticipant.event_id == event_id)
    )
    return [row[0] for row in result.all()]


async def event_ids_for_participant(db: AsyncSession, event_ids, participant_id) -> set:
    if not event_ids:
        return set()
    result = await db.execute(
        select(CalendarEventParticipant.event_id).where(
            CalendarEventParticipant.event_id.in_(list(event_ids)),
            CalendarEventParticipant.participant_id == participant_id,
        )
    )
    return {row[0] for row in result.all()}


async def event_ids_for_department(db: AsyncSession, event_ids, department) -> set:
    if not event_ids or not department:
        return set()
    result = await db.execute(
        select(CalendarEventParticipant.event_id).where(
            CalendarEventParticipant.event_id.in_(list(event_ids)),
            CalendarEventParticipant.department == department,
        )
    )
    return {row[0] for row in result.all()}


async def departments_for_event_matching(db: AsyncSession, event_id) -> set:
    result = await db.execute(
        select(CalendarEventParticipant.department).where(
            CalendarEventParticipant.event_id == event_id, CalendarEventParticipant.department.is_not(None),
        )
    )
    return {row[0] for row in result.all()}


async def event_ids_matching(db: AsyncSession, company_id, *, participant_name=None, department=None) -> set:
    """Both filters apply to the SAME participant row (matches the old Mongo
    single-query semantics: one row whose participant_name AND department
    both match), not two independently-matched sets intersected afterward."""
    conditions = [CalendarEventParticipant.company_id == company_id]
    if participant_name:
        conditions.append(CalendarEventParticipant.participant_name.ilike(f"%{participant_name}%"))
    if department:
        conditions.append(CalendarEventParticipant.department == department)
    result = await db.execute(select(CalendarEventParticipant.event_id).where(*conditions))
    return {row[0] for row in result.all()}


async def all_event_ids_for_participant_in_company(db: AsyncSession, company_id, participant_id) -> List[uuid.UUID]:
    result = await db.execute(
        select(CalendarEventParticipant.event_id).where(
            CalendarEventParticipant.company_id == company_id,
            CalendarEventParticipant.participant_id == participant_id,
        )
    )
    return [row[0] for row in result.all()]


async def delete_participants(db: AsyncSession, event_id, participant_ids) -> int:
    result = await db.execute(
        delete(CalendarEventParticipant).where(
            CalendarEventParticipant.event_id == event_id,
            CalendarEventParticipant.participant_id.in_(list(participant_ids)),
        )
    )
    return result.rowcount


async def count_by_event_and_status(db: AsyncSession, event_ids, status) -> Dict[uuid.UUID, int]:
    if not event_ids:
        return {}
    result = await db.execute(
        select(CalendarEventParticipant.event_id, func.count())
        .where(CalendarEventParticipant.event_id.in_(list(event_ids)), CalendarEventParticipant.attendance_status == status)
        .group_by(CalendarEventParticipant.event_id)
    )
    return {row[0]: row[1] for row in result.all()}
