import json
import logging
import os
from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.device_tokens as device_tokens_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Push delivery for the notification framework (see services/notifications.py
# ::publish(), which is the only caller). Mirrors the realtime SSE channel's
# own contract: the Notification row is the source of truth, this is an
# additional best-effort delivery channel on top of it - a failed or
# unconfigured push must never block notification creation, so every
# exception below is caught and logged, never raised.
# ---------------------------------------------------------------------------

_app: Optional[firebase_admin.App] = None
_app_init_attempted = False


def _firebase_app():
    """Lazy, cached Firebase Admin app - same lazy-init shape as
    services/subscriptions.py's `_stripe_client()`, except failure here
    must degrade silently rather than raise: every existing flow (create a
    task, send a message, submit a report, ...) already works without push
    configured today, and must keep working identically once push
    credentials are added later. Configure via either:
      - FIREBASE_SERVICE_ACCOUNT_JSON: the full service-account JSON as one
        env var (convenient for containerized deploys where mounting a
        credentials file is awkward), or
      - GOOGLE_APPLICATION_CREDENTIALS: a file path (firebase-admin's own
        default credential lookup).
    Neither set -> every function below is a no-op."""
    global _app, _app_init_attempted
    if _app is not None or _app_init_attempted:
        return _app
    _app_init_attempted = True

    raw_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    try:
        if raw_json:
            cred = credentials.Certificate(json.loads(raw_json))
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            cred = credentials.ApplicationDefault()
        else:
            logger.info("Push notifications disabled: no Firebase credentials configured")
            return None
        _app = firebase_admin.initialize_app(cred)
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK - push notifications disabled")
        _app = None
    return _app


def _data_payload(*, category: str, type: str, entity_type, entity_id, action_url) -> dict:
    """FCM `data` payloads are string-only key/value pairs (unlike the
    `notification` block); this is exactly what lets the Flutter client
    deep-link a tapped push the same way it already deep-links an in-app
    notification tap, via the same entity_type/entity_id/action_url the
    Notification Center already returns (see notification_response() in
    services/notifications.py). None fields are omitted rather than sent
    as the literal string "None"."""
    data = {"category": category, "type": type}
    if entity_type:
        data["entity_type"] = entity_type
    if entity_id:
        data["entity_id"] = str(entity_id)
    if action_url:
        data["action_url"] = action_url
    return data


async def send_push_to_user(
    db: AsyncSession,
    user_id,
    *,
    title: str,
    body: str,
    category: str,
    type: str,
    entity_type: Optional[str] = None,
    entity_id=None,
    action_url: Optional[str] = None,
) -> None:
    """Best-effort push fan-out to every device registered for [user_id].
    Called from services.notifications.publish() after the notification
    row and SSE broadcast already succeeded - nothing here can undo that,
    so every failure mode (unconfigured SDK, no devices, FCM error) is
    silent from the caller's perspective. Any token FCM reports as
    permanently invalid (app uninstalled, token rotated) is deleted so it
    isn't retried on every future notification (Phase 7/10 reliability)."""
    app = _firebase_app()
    if app is None:
        return

    tokens = await device_tokens_repo.list_tokens_for_user(db, user_id)
    if not tokens:
        return

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data=_data_payload(category=category, type=type, entity_type=entity_type, entity_id=entity_id, action_url=action_url),
    )

    try:
        response = await messaging.send_each_for_multicast_async(message, app=app)
    except Exception:
        logger.exception("Push send failed for user %s", user_id)
        return

    dead_tokens = [
        tokens[i]
        for i, resp in enumerate(response.responses)
        if not resp.success and isinstance(resp.exception, (messaging.UnregisteredError, messaging.SenderIdMismatchError))
    ]
    if dead_tokens:
        try:
            await device_tokens_repo.delete_tokens(db, dead_tokens)
            await db.flush()
        except Exception:
            logger.exception("Failed to clean up dead push tokens for user %s", user_id)
