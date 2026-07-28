from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.calendar_event_reminders as calendar_reminders_repo
import repositories.message_reminders as message_reminders_repo
import repositories.notifications as notifications_repo
import repositories.users as users_repo
import services.push as push_service
import services.realtime as realtime_service
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# Reusable notification framework.
#
# publish() is the single write path every module in the platform uses to
# create a notification - Tasks, Attendance, Messaging, Calendar, Reports,
# Subscriptions today; Finance/CRM/Inventory/Payroll/whatever tomorrow. No
# module ever imports repositories.notifications directly or constructs a
# Notification row itself. Adding a brand-new notification type or category
# never requires touching this file - callers just pass a new `category`/
# `type` string; there is no whitelist to extend.
#
# `category` drives icon/color grouping on the frontend (see
# frontend/src/constants/notifications.js CATEGORY_META) - kept as a plain
# string, not a DB enum/CHECK constraint, precisely so future modules are
# not a schema change. The categories below are the ones currently in use;
# a new module is free to introduce its own.
# ---------------------------------------------------------------------------

CATEGORY_SYSTEM = "system"
CATEGORY_ATTENDANCE = "attendance"
CATEGORY_TASKS = "tasks"
CATEGORY_URGENT_TASKS = "urgent_tasks"
CATEGORY_CRITICAL_TASKS = "critical_tasks"
CATEGORY_MESSAGES = "messages"
CATEGORY_CALENDAR = "calendar"
CATEGORY_REPORTS = "reports"
CATEGORY_SUBSCRIPTIONS = "subscriptions"
CATEGORY_PAYMENTS = "payments"
CATEGORY_ANNOUNCEMENTS = "announcements"

SUBSCRIPTION_EXPIRING_SOON_DAYS = 7


def _iso(value):
    return value.isoformat() if value else None


def notification_response(notification) -> dict:
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id),
        "company_id": str(notification.company_id) if notification.company_id else None,
        "type": notification.type,
        "category": notification.category,
        "title": notification.title,
        "message": notification.message,
        "read_status": notification.read_status,
        "entity_type": notification.entity_type,
        "entity_id": str(notification.entity_id) if notification.entity_id else None,
        "action_url": notification.action_url,
        "sender_id": str(notification.sender_id) if notification.sender_id else None,
        "sender_name": notification.sender_name,
        "created_at": _iso(notification.created_at),
    }


async def publish(
    db: AsyncSession,
    *,
    user_id,
    company_id,
    category: str,
    type: str,
    title: str,
    message: str,
    entity_type: Optional[str] = None,
    entity_id=None,
    action_url: Optional[str] = None,
    sender_id=None,
    sender_name: Optional[str] = None,
) -> dict:
    """Create one notification for one user. Every module in the platform
    calls this - never repositories.notifications.create() directly. Also
    delivers the notification over two additional, best-effort channels on
    top of the row (the source of truth):
      - the realtime SSE channel (services/realtime.py), for instant
        delivery to any open web connection;
      - FCM push (services/push.py), for delivery to registered mobile
        devices, including while the app is backgrounded or killed.
    Firing before commit is intentional: the payload below is a plain
    dict, not a lazy ORM handle, so both channels get the exact same data
    whether or not this request's transaction has committed yet."""
    notification = await notifications_repo.create(
        db,
        user_id=user_id,
        company_id=company_id,
        type=type,
        title=title,
        message=message,
        category=category,
        entity_type=entity_type,
        entity_id=entity_id,
        action_url=action_url,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    response = notification_response(notification)
    realtime_service.publish_to_user(str(user_id), response)
    await push_service.send_push_to_user(
        db,
        user_id,
        title=title,
        body=message,
        category=category,
        type=type,
        entity_type=entity_type,
        entity_id=entity_id,
        action_url=action_url,
    )
    return response


async def publish_to_role(
    db: AsyncSession,
    *,
    role: str,
    category: str,
    type: str,
    title: str,
    message: str,
    company_id=None,
    entity_type: Optional[str] = None,
    entity_id=None,
    action_url: Optional[str] = None,
    sender_id=None,
    sender_name: Optional[str] = None,
) -> List[dict]:
    """Broadcast one notification to every active user with the given role
    - used for platform-wide events (e.g. a payment received, notified to
    every super_admin) where there is no single natural recipient."""
    recipients = await users_repo.list_by_role(db, role)
    return [
        await publish(
            db,
            user_id=recipient.id,
            company_id=company_id,
            category=category,
            type=type,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            action_url=action_url,
            sender_id=sender_id,
            sender_name=sender_name,
        )
        for recipient in recipients
    ]


async def deliver_due_message_reminders(db: AsyncSession, user_id) -> None:
    """Self-heal on read, same house pattern as subscription-expiry/
    presence: no scheduler exists, converts any past-due reminder into a
    real notification row on whichever request next reads notifications."""
    now = datetime.now(timezone.utc)
    due = await message_reminders_repo.list_due(db, user_id, now)
    for reminder in due:
        subject = await message_reminders_repo.get_message_subject(db, reminder.message_id)
        await publish(
            db,
            user_id=user_id,
            company_id=reminder.company_id,
            category=CATEGORY_MESSAGES,
            type="message_reminder",
            title="تذكير برسالة",
            message=f"تذكير: {subject or ''}",
            entity_type="message",
            entity_id=reminder.message_id,
            action_url="/company-owner/messages" if reminder.company_id else "/employee/messages",
        )
        await message_reminders_repo.mark_notified(db, reminder.id, now)


async def deliver_due_calendar_reminders(db: AsyncSession, user_id) -> None:
    """Same self-heal-on-read pattern as message reminders."""
    now = datetime.now(timezone.utc)
    due = await calendar_reminders_repo.list_due(db, user_id, now)
    for reminder in due:
        title = await calendar_reminders_repo.get_event_title(db, reminder.event_id)
        await publish(
            db,
            user_id=user_id,
            company_id=reminder.company_id,
            category=CATEGORY_CALENDAR,
            type="calendar_reminder",
            title="تذكير بموعد",
            message=f"تذكير: {title or ''}",
            entity_type="calendar_event",
            entity_id=reminder.event_id,
            action_url="/company-owner/calendar",
        )
        await calendar_reminders_repo.mark_notified(db, reminder.id, now)


async def deliver_subscription_expiry_warning(db: AsyncSession, current_user: dict) -> None:
    """Self-heal on read: a company_owner whose subscription expires within
    SUBSCRIPTION_EXPIRING_SOON_DAYS gets one warning notification per day
    (deduped via exists_since, since there is no scheduler to fire this
    once). No-op for any other role or once subscription_end_date is out
    of the warning window."""
    if current_user["role"] != "company_owner" or not current_user.get("company_id"):
        return
    # Reuses the company row the auth dependency already fetched this same
    # request (see get_current_user) instead of re-querying it here -
    # covers the "company not found" case too, since that dependency only
    # sets these when its own company lookup succeeded.
    subscription_end_date = current_user.get("_company_subscription_end_date")
    subscription_status = current_user.get("_company_subscription_status")
    if not subscription_end_date or subscription_status != "active":
        return
    company_id = parse_uuid(current_user["company_id"])

    now = datetime.now(timezone.utc)
    days_remaining = (subscription_end_date - now).total_seconds() / 86400
    if days_remaining < 0 or days_remaining > SUBSCRIPTION_EXPIRING_SOON_DAYS:
        return

    user_id = parse_uuid(current_user["id"])
    if await notifications_repo.exists_since(db, user_id, "subscription_expiring", now - timedelta(hours=24)):
        return

    days_label = max(0, int(days_remaining))
    await publish(
        db,
        user_id=user_id,
        company_id=company_id,
        category=CATEGORY_SUBSCRIPTIONS,
        type="subscription_expiring",
        title="اقتراب انتهاء الاشتراك",
        message=f"سينتهي اشتراكك خلال {days_label} يوم/أيام. جدد الآن لتفادي انقطاع الخدمة.",
        entity_type="company",
        entity_id=company_id,
        action_url="/company-owner/subscription",
    )


async def get_notifications(db: AsyncSession, current_user: dict) -> List[dict]:
    user_id = parse_uuid(current_user["id"])
    await deliver_due_message_reminders(db, user_id)
    await deliver_due_calendar_reminders(db, user_id)
    await deliver_subscription_expiry_warning(db, current_user)
    notifications = await notifications_repo.list_for_user(db, user_id)
    return [notification_response(n) for n in notifications]


async def get_unread_count(db: AsyncSession, current_user: dict) -> dict:
    user_id = parse_uuid(current_user["id"])
    await deliver_due_message_reminders(db, user_id)
    await deliver_due_calendar_reminders(db, user_id)
    await deliver_subscription_expiry_warning(db, current_user)
    return {"count": await notifications_repo.count_unread(db, user_id)}


async def mark_notification_read(db: AsyncSession, current_user: dict, notification_id: str) -> dict:
    parsed_id = parse_uuid(notification_id)
    if not parsed_id or not await notifications_repo.mark_read(db, parsed_id, parse_uuid(current_user["id"])):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}


async def mark_all_notifications_read(db: AsyncSession, current_user: dict) -> dict:
    count = await notifications_repo.mark_all_read(db, parse_uuid(current_user["id"]))
    return {"message": "All notifications marked as read", "count": count}
