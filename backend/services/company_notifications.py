import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.company_notifications as company_notifications_repo
import repositories.users as users_repo
import services.notifications as notifications_service
from services.admin import parse_uuid

logger = logging.getLogger(__name__)

# recipient_mode -> roles it targets. "custom" is resolved per company entry
# in target_config instead (see resolve_recipients). Mirrors the Literal
# choices on CompanyNotificationCreate/PreviewRequest in server.py - that
# schema-level Literal is the primary defense against an invalid mode
# (422 before this code ever runs); the explicit else-raise below is
# defense-in-depth so this function is also safe on its own terms if ever
# called from a path that isn't Pydantic-validated.
_MODE_ROLES = {
    "all_managers": ["company_owner"],
    "all_employees": ["employee"],
    "everyone": ["company_owner", "employee"],
}
_CUSTOM_GROUP_ROLES = {
    "managers": ["company_owner"],
    "employees": ["employee"],
    "everyone": ["company_owner", "employee"],
}


def _iso(value):
    return value.isoformat() if value else None


def _validate_target_config(recipient_mode: str, target_config) -> None:
    if recipient_mode != "custom":
        return
    companies = (target_config or {}).get("companies") if isinstance(target_config, dict) else None
    if not companies:
        raise HTTPException(status_code=400, detail="target_config.companies is required for custom targeting")
    for entry in companies:
        if not entry.get("company_id") or entry.get("mode") not in _CUSTOM_GROUP_ROLES:
            raise HTTPException(status_code=400, detail="Each custom target needs a company_id and a valid mode")


async def resolve_recipients(db: AsyncSession, recipient_mode: str, target_config: Optional[dict]) -> list:
    """Returns the deduplicated list of live User rows, at eligible active
    companies, that a broadcast in this mode would reach. Shared by the
    preview endpoint (count only) and the actual send (also used to fan
    out notifications), so the number shown in the confirmation dialog is
    always computed the exact same way the real send resolves it -
    "eligible active company" is enforced inside users_repo.list_by_roles
    itself (see its own docstring for the exact predicate), not
    duplicated here."""
    _validate_target_config(recipient_mode, target_config)

    if recipient_mode in _MODE_ROLES:
        return await users_repo.list_by_roles(db, _MODE_ROLES[recipient_mode])

    if recipient_mode != "custom":
        # Unreachable when called through the API (server.py's Literal
        # type rejects this before the route body runs) - kept as a real
        # guard, not a comment, so this function is correct on its own
        # even if called some other way in the future.
        raise HTTPException(status_code=400, detail=f"Unknown recipient_mode: {recipient_mode!r}")

    # custom: an explicit per-company allow-list - a company not listed is
    # implicitly excluded, so there's no separate "exclude" concept to
    # resolve. Dedup by id in case the same company is listed twice.
    by_id = {}
    for entry in target_config["companies"]:
        company_id = parse_uuid(entry["company_id"])
        roles = _CUSTOM_GROUP_ROLES[entry["mode"]]
        for user in await users_repo.list_by_roles(db, roles, company_ids=[company_id]):
            by_id[user.id] = user
    return list(by_id.values())


async def preview_recipient_count(db: AsyncSession, recipient_mode: str, target_config: Optional[dict]) -> dict:
    recipients = await resolve_recipients(db, recipient_mode, target_config)
    return {"recipient_count": len(recipients)}


def broadcast_response(broadcast) -> dict:
    return {
        "id": str(broadcast.id),
        "sender_id": str(broadcast.sender_id),
        "sender_name": broadcast.sender_name,
        "title_ar": broadcast.title_ar,
        "title_en": broadcast.title_en,
        "message_ar": broadcast.message_ar,
        "message_en": broadcast.message_en,
        "recipient_mode": broadcast.recipient_mode,
        "target_config": broadcast.target_config,
        "recipient_count": broadcast.recipient_count,
        "notification_created_count": broadcast.notification_created_count,
        "push_delivered_count": broadcast.push_delivered_count,
        "push_failed_count": broadcast.push_failed_count,
        "status": broadcast.status,
        "created_at": _iso(broadcast.created_at),
        "sent_at": _iso(broadcast.sent_at),
    }


def _require_nonempty(data, *fields) -> None:
    for field in fields:
        if not (getattr(data, field) or "").strip():
            raise HTTPException(status_code=400, detail=f"{field} is required")


async def create_and_send(db: AsyncSession, current_user: dict, data) -> dict:
    """Resolves the audience, creates the durable broadcast/audit row, then
    fans out one real notification per recipient via the existing
    services.notifications.publish() pipeline (DB row + realtime SSE + FCM
    push, all for free) - same delivery mechanism services/announcements.py
    already uses, just with a cross-company/role-flexible audience and
    per-recipient language selection instead of a fixed one-company
    employee list. Synchronous, matching the house convention (no job
    queue exists in this codebase) - one recipient's Notification-row
    creation failing never aborts the rest, it only counts against the
    broadcast's status.

    Idempotency: same pattern as services/tasks.py::_idempotent (client-
    supplied key, checked up front, then re-checked as an IntegrityError
    catch around the actual insert so two truly concurrent identical
    requests still only ever produce one row - the DB's own unique
    constraint on company_notifications.idempotency_key is what makes the
    second insert fail atomically rather than racing in Python). No key
    means no protection, same convention as tasks.py."""
    _require_nonempty(data, "title_ar", "title_en", "message_ar", "message_en")
    target_config = data.target_config.model_dump() if data.target_config else None
    _validate_target_config(data.recipient_mode, target_config)

    idempotency_key = getattr(data, "idempotency_key", None)
    if idempotency_key:
        existing = await company_notifications_repo.get_by_idempotency_key(db, idempotency_key)
        if existing:
            return broadcast_response(existing)

    recipients = await resolve_recipients(db, data.recipient_mode, target_config)

    try:
        broadcast = await company_notifications_repo.create(
            db,
            sender_id=parse_uuid(current_user["id"]),
            sender_name=current_user["name"],
            title_ar=data.title_ar,
            title_en=data.title_en,
            message_ar=data.message_ar,
            message_en=data.message_en,
            recipient_mode=data.recipient_mode,
            target_config=target_config if data.recipient_mode == "custom" else None,
            recipient_count=len(recipients),
            status="sent",
            idempotency_key=idempotency_key,
        )
        await db.flush()
    except IntegrityError:
        # Lost a genuine race to a concurrent request carrying the same
        # key - its row already won and (by the time this rolls back and
        # re-queries) is either mid-flight or already complete. Either
        # way, this request must not also send - replay whatever the
        # winner's row currently shows instead of creating a second
        # broadcast.
        await db.rollback()
        existing = await company_notifications_repo.get_by_idempotency_key(db, idempotency_key) if idempotency_key else None
        if existing:
            return broadcast_response(existing)
        raise

    notification_created_count = 0
    creation_failed_count = 0
    push_delivered_count = 0
    push_failed_count = 0
    for recipient in recipients:
        title = data.title_en if recipient.language == "en" else data.title_ar
        message = data.message_en if recipient.language == "en" else data.message_ar
        try:
            response = await notifications_service.publish(
                db,
                user_id=recipient.id,
                company_id=recipient.company_id,
                category=notifications_service.CATEGORY_COMPANY_NOTIFICATIONS,
                type="company_notification",
                title=title,
                message=message,
                entity_type="company_notification",
                entity_id=broadcast.id,
                sender_id=broadcast.sender_id,
                sender_name=broadcast.sender_name,
            )
            notification_created_count += 1
            push_result = response.get("push_result") or {}
            if push_result.get("attempted"):
                if push_result.get("sent_count", 0) > 0:
                    push_delivered_count += 1
                else:
                    push_failed_count += 1
            # else: nothing to report - no registered device, or push
            # unconfigured. Not counted as delivered or failed, since
            # neither is actually true.
        except Exception:
            creation_failed_count += 1
            logger.exception(
                "company_notification %s: failed to notify recipient %s", broadcast.id, recipient.id
            )

    broadcast.notification_created_count = notification_created_count
    broadcast.push_delivered_count = push_delivered_count
    broadcast.push_failed_count = push_failed_count
    broadcast.status = "failed" if recipients and notification_created_count == 0 else (
        "partial_failure" if creation_failed_count else "sent"
    )
    broadcast.sent_at = datetime.now(timezone.utc)
    await db.flush()

    return broadcast_response(broadcast)


async def list_broadcasts(db: AsyncSession, limit: int = 50, offset: int = 0) -> List[dict]:
    broadcasts = await company_notifications_repo.list_all(db, limit=limit, offset=offset)
    return [broadcast_response(b) for b in broadcasts]
