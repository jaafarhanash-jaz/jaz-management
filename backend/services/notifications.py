from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.calendar_event_reminders as calendar_reminders_repo
import repositories.message_reminders as message_reminders_repo
import repositories.notifications as notifications_repo
from services.admin import parse_uuid


def _iso(value):
    return value.isoformat() if value else None


def notification_response(notification) -> dict:
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id),
        "company_id": str(notification.company_id) if notification.company_id else None,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "read_status": notification.read_status,
        "created_at": _iso(notification.created_at),
    }


async def deliver_due_message_reminders(db: AsyncSession, user_id) -> None:
    """Self-heal on read, same house pattern as subscription-expiry/
    presence: no scheduler exists, converts any past-due reminder into a
    real notification row on whichever request next reads notifications.
    message_reminders has no rows until the Work Messaging module migrates
    its own write path - this stays a correct no-op until then."""
    now = datetime.now(timezone.utc)
    due = await message_reminders_repo.list_due(db, user_id, now)
    for reminder in due:
        subject = await message_reminders_repo.get_message_subject(db, reminder.message_id)
        await notifications_repo.create(
            db,
            user_id=user_id,
            company_id=reminder.company_id,
            type="message_reminder",
            title="تذكير برسالة",
            message=f"تذكير: {subject or ''}",
        )
        await message_reminders_repo.mark_notified(db, reminder.id, now)


async def deliver_due_calendar_reminders(db: AsyncSession, user_id) -> None:
    """Same self-heal-on-read pattern as message reminders. Correct no-op
    until the Calendar module migrates its own write path."""
    now = datetime.now(timezone.utc)
    due = await calendar_reminders_repo.list_due(db, user_id, now)
    for reminder in due:
        title = await calendar_reminders_repo.get_event_title(db, reminder.event_id)
        await notifications_repo.create(
            db,
            user_id=user_id,
            company_id=reminder.company_id,
            type="calendar_reminder",
            title="تذكير بموعد",
            message=f"تذكير: {title or ''}",
        )
        await calendar_reminders_repo.mark_notified(db, reminder.id, now)


async def get_notifications(db: AsyncSession, current_user: dict) -> List[dict]:
    user_id = parse_uuid(current_user["id"])
    await deliver_due_message_reminders(db, user_id)
    await deliver_due_calendar_reminders(db, user_id)
    notifications = await notifications_repo.list_for_user(db, user_id)
    return [notification_response(n) for n in notifications]


async def mark_notification_read(db: AsyncSession, current_user: dict, notification_id: str) -> dict:
    parsed_id = parse_uuid(notification_id)
    if not parsed_id or not await notifications_repo.mark_read(db, parsed_id, parse_uuid(current_user["id"])):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}
