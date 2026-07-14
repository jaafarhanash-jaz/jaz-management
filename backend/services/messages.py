import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.audit_logs as audit_logs_repo
import repositories.counters as counters_repo
import repositories.message_attachments as attachments_repo
import repositories.message_recipients as recipients_repo
import repositories.message_reminders as reminders_repo
import repositories.messages as messages_repo
import repositories.notifications as notifications_repo
import repositories.users as users_repo
from models import Message
from services.admin import parse_uuid
from services.storage import classify_attachment_type, decode_and_validate, download_base64, upload

REMINDER_PRESETS = {"30m": {"minutes": 30}, "1h": {"hours": 1}, "tomorrow": {"days": 1}}


def require_company_member(current_user: dict):
    if current_user["role"] not in ("company_owner", "employee"):
        raise HTTPException(status_code=403, detail="Access denied")


def paginate_params(page: int, page_size: int):
    return max(page, 1), min(max(page_size, 1), 100)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _clean_ids(raw_ids) -> List[uuid.UUID]:
    """Filters out unparseable id strings rather than letting them reach a
    UUID-typed query (which would raise a DB-level syntax error). A bogus
    id therefore always fails the subsequent membership/count check -
    matching the old Mongo behavior where a nonexistent id simply never
    matched any document."""
    return [pid for pid in (parse_uuid(rid) for rid in raw_ids) if pid is not None]


# ---- Recipient resolution / delivery ----

async def resolve_recipients(db: AsyncSession, company_id, recipient_type: str, recipient_ids: List[str]) -> tuple:
    """Fan-out resolution at send time. Returns (list of real employee UUIDs,
    department label or None)."""
    if recipient_type == "department":
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="Department is required")
        department = recipient_ids[0]
        employees = await users_repo.list_employees_by_company_and_department(db, company_id, department)
        if not employees:
            raise HTTPException(status_code=400, detail="No employees found in this department")
        return [e.id for e in employees], department

    if not recipient_ids:
        raise HTTPException(status_code=400, detail="At least one recipient is required")
    unique_raw = set(recipient_ids)
    valid_ids = _clean_ids(unique_raw)
    valid_count = await users_repo.count_matching_ids_in_company(db, valid_ids, company_id)
    if valid_count != len(unique_raw):
        raise HTTPException(status_code=400, detail="One or more recipients do not belong to your company")
    return valid_ids, None


async def write_message_activity(db: AsyncSession, company_id, message_id, actor: dict, verb: str, subject: str):
    """Unified audit_logs table (per the approved schema): entity_type=
    'message', the denormalized subject snapshot folded into new_values -
    never stores the message body."""
    await audit_logs_repo.create(
        db,
        company_id=company_id,
        user_id=parse_uuid(actor["id"]),
        entity_type="message",
        entity_id=message_id,
        action=verb,
        new_values={"subject": subject},
    )


async def deliver_message(db: AsyncSession, message, employee_ids: List[uuid.UUID]):
    """Creates a brand-new thread's participant rows - one per participant,
    including the sender - plus one notification per real recipient and one
    'sent' activity entry."""
    now = datetime.now(timezone.utc)

    await recipients_repo.create(
        db,
        thread_id=message.thread_id, company_id=message.company_id,
        recipient_id=message.sender_id, recipient_name=message.sender_name,
        role="sender", status="delivered", is_unread=False, is_starred=False,
        delivered_at=now,
    )

    names = await users_repo.get_names_by_ids(db, employee_ids)
    for employee_id in employee_ids:
        await recipients_repo.create(
            db,
            thread_id=message.thread_id, company_id=message.company_id,
            recipient_id=employee_id, recipient_name=names.get(str(employee_id)),
            role="recipient", status="delivered", is_unread=True, is_starred=False,
            delivered_at=now,
        )
        await notifications_repo.create(
            db, user_id=employee_id, company_id=message.company_id,
            type="work_message", title="رسالة عمل جديدة", message=message.subject,
        )

    await write_message_activity(
        db, message.company_id, message.id, {"id": str(message.sender_id), "name": message.sender_name},
        "sent", message.subject,
    )


async def notify_reply_participants(db: AsyncSession, reply, thread_id, participant_ids: List[uuid.UUID]):
    """A reply does not create new message_recipients rows - it re-opens the
    existing thread-level row for every other participant and notifies them."""
    await recipients_repo.mark_unread_bulk(db, thread_id, participant_ids)
    for participant_id in participant_ids:
        await notifications_repo.create(
            db, user_id=participant_id, company_id=reply.company_id,
            type="message_reply", title="رد على رسالة", message=reply.subject,
        )
    await write_message_activity(
        db, reply.company_id, reply.id, {"id": str(reply.sender_id), "name": reply.sender_name},
        "replied", reply.subject,
    )


# ---- Response building ----

def message_response(message, *, recipient_names=None, attachment_count=0, reply_count=0,
                      delivery_progress=None, my_status=None, my_is_unread=None, my_is_starred=None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": str(message.id),
        "reference_number": message.reference_number,
        "company_id": str(message.company_id),
        "thread_id": str(message.thread_id),
        "parent_message_id": str(message.parent_message_id) if message.parent_message_id else None,
        "sender_id": str(message.sender_id),
        "sender_name": message.sender_name,
        "subject": message.subject,
        "body": message.body,
        "priority": message.priority,
        "confidentiality": message.confidentiality,
        "tags": list(message.tags or []),
        "requires_acknowledgement": message.requires_acknowledgement,
        "completion_required": message.completion_required,
        "expires_at": _iso(message.expires_at),
        "is_expired": bool(message.expires_at) and message.expires_at < now,
        "recipient_type": message.recipient_type,
        "recipient_department": message.recipient_department,
        "is_draft": message.is_draft,
        "is_forward": message.is_forward,
        "forwarded_from_id": str(message.forwarded_from_id) if message.forwarded_from_id else None,
        "is_pinned": message.is_pinned,
        "closed_at": _iso(message.closed_at),
        "created_at": _iso(message.created_at),
        "updated_at": _iso(message.updated_at),
        "recipient_names": recipient_names if recipient_names is not None else [],
        "attachment_count": attachment_count,
        "delivery_progress": delivery_progress,
        "my_status": my_status,
        "my_is_unread": my_is_unread,
        "my_is_starred": my_is_starred,
        "reply_count": reply_count,
    }


async def enrich_messages(db: AsyncSession, messages: List, current_user: dict) -> List[dict]:
    """Batches every join a message list needs into one query per table
    (never N+1); never fetches attachment bytes - list views stay cheap."""
    if not messages:
        return []
    thread_ids = list({m.thread_id for m in messages})

    all_recipients = await recipients_repo.list_for_threads(db, thread_ids)
    recipients_by_thread = {}
    for r in all_recipients:
        recipients_by_thread.setdefault(str(r.thread_id), []).append(r)

    attachment_counts = await attachments_repo.list_by_threads_counts(db, thread_ids)
    reply_counts = await messages_repo.count_replies_by_thread(db, thread_ids)

    current_user_id = current_user["id"]
    enriched = []
    for m in messages:
        participants = recipients_by_thread.get(str(m.thread_id), [])
        recips_only = [p for p in participants if p.role == "recipient"]
        mine = next((p for p in participants if str(p.recipient_id) == current_user_id), None)

        def reached(*statuses):
            return sum(1 for r in recips_only if r.status in statuses)

        total = len(recips_only)
        delivery_progress = {
            "total": total,
            "delivered": total,
            "seen": reached("seen", "accepted", "completed", "archived"),
            "accepted": reached("accepted", "completed", "archived"),
            "completed": reached("completed", "archived"),
        }
        enriched.append(message_response(
            m,
            recipient_names=[r.recipient_name for r in recips_only if r.recipient_name],
            attachment_count=attachment_counts.get(str(m.thread_id), 0),
            reply_count=max(reply_counts.get(str(m.thread_id), 1) - 1, 0),
            delivery_progress=delivery_progress,
            my_status=mine.status if mine else None,
            my_is_unread=mine.is_unread if mine else None,
            my_is_starred=mine.is_starred if mine else None,
        ))
    return enriched


async def get_accessible_message(db: AsyncSession, message_id: str, current_user: dict):
    """Shared access check: sender, an actual thread participant, or the
    company owner. Super Admin has no path here by omission."""
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_by_id(db, parsed_id) if parsed_id else None
    if not message or str(message.company_id) != current_user.get("company_id"):
        raise HTTPException(status_code=404, detail="Message not found")

    is_sender = str(message.sender_id) == current_user["id"]
    is_owner = current_user["role"] == "company_owner"
    participant_row = await recipients_repo.get_participant(db, message.thread_id, parse_uuid(current_user["id"]))
    if not (is_sender or is_owner or participant_row):
        raise HTTPException(status_code=404, detail="Message not found")
    return message


# ---- Compose / draft / send ----

async def create_message(db: AsyncSession, current_user: dict, data) -> dict:
    require_company_member(current_user)
    if data.priority not in ("normal", "important", "urgent"):
        raise HTTPException(status_code=400, detail="Invalid priority")
    if data.confidentiality not in ("public", "internal", "confidential", "highly_confidential"):
        raise HTTPException(status_code=400, detail="Invalid confidentiality level")

    company_id = parse_uuid(current_user["company_id"])
    message_id = uuid.uuid4()

    department_label = None
    employee_ids: List[uuid.UUID] = []
    if not data.is_draft:
        if data.recipient_type not in ("employee", "department"):
            raise HTTPException(status_code=400, detail="Invalid recipient type")
        employee_ids, department_label = await resolve_recipients(db, company_id, data.recipient_type, data.recipient_ids)

    reference_number = f"MSG-{await counters_repo.increment_and_get(db, f'message_seq:{company_id}'):06d}"
    message = await messages_repo.create(
        db,
        id=message_id, reference_number=reference_number, company_id=company_id,
        thread_id=message_id, parent_message_id=None,
        sender_id=parse_uuid(current_user["id"]), sender_name=current_user.get("name"),
        subject=data.subject, body=data.body, priority=data.priority, confidentiality=data.confidentiality,
        tags=data.tags, requires_acknowledgement=data.requires_acknowledgement,
        completion_required=data.completion_required, expires_at=_parse_dt(data.expires_at),
        recipient_type=data.recipient_type, recipient_ids=data.recipient_ids,
        recipient_department=department_label, is_draft=data.is_draft,
        is_forward=False, forwarded_from_id=None, is_pinned=False, closed_at=None,
    )

    if not data.is_draft:
        await deliver_message(db, message, employee_ids)

    result = (await enrich_messages(db, [message], current_user))[0]
    return result


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def update_message(db: AsyncSession, current_user: dict, message_id: str, updates) -> dict:
    require_company_member(current_user)
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message or str(message.sender_id) != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")

    seen_exists = await _any_recipient_seen(db, message.thread_id)
    if seen_exists:
        raise HTTPException(status_code=400, detail="لا يمكن تعديل الرسالة بعد أن شاهدها أحد المستلمين.")

    changes = updates.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if "expires_at" in changes:
        changes["expires_at"] = _parse_dt(changes["expires_at"])
    for field, value in changes.items():
        setattr(message, field, value)
    message.updated_at = datetime.now(timezone.utc)
    await db.flush()

    result = (await enrich_messages(db, [message], current_user))[0]
    return result


async def _any_recipient_seen(db: AsyncSession, thread_id) -> bool:
    rows = await recipients_repo.list_recipient_only_for_thread(db, thread_id)
    return any(r.seen_at is not None for r in rows)


async def send_draft(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message or str(message.sender_id) != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")
    if not message.is_draft:
        raise HTTPException(status_code=400, detail="Message has already been sent")

    employee_ids, department_label = await resolve_recipients(
        db, message.company_id, message.recipient_type, [str(i) for i in (message.recipient_ids or [])]
    )
    message.is_draft = False
    message.recipient_department = department_label
    message.updated_at = datetime.now(timezone.utc)
    await deliver_message(db, message, employee_ids)

    result = (await enrich_messages(db, [message], current_user))[0]
    return result


async def reply_to_message(db: AsyncSession, current_user: dict, message_id: str, data) -> dict:
    require_company_member(current_user)
    root_or_reply = await get_accessible_message(db, message_id, current_user)
    thread_id = root_or_reply.thread_id

    participant_rows = await recipients_repo.list_for_thread(db, thread_id)
    participant_ids = {r.recipient_id for r in participant_rows}
    participant_ids.discard(parse_uuid(current_user["id"]))
    if not participant_ids:
        raise HTTPException(status_code=400, detail="No other participants in this thread")

    root = await messages_repo.get_by_id(db, thread_id)
    subject = root.subject if root else root_or_reply.subject
    company_id = parse_uuid(current_user["company_id"])
    reference_number = f"MSG-{await counters_repo.increment_and_get(db, f'message_seq:{company_id}'):06d}"

    reply = await messages_repo.create(
        db,
        reference_number=reference_number, company_id=company_id,
        thread_id=thread_id, parent_message_id=parse_uuid(message_id),
        sender_id=parse_uuid(current_user["id"]), sender_name=current_user.get("name"),
        subject=subject if subject.startswith("Re: ") else f"Re: {subject}",
        body=data.body, priority="normal",
        confidentiality=root_or_reply.confidentiality, tags=[],
        requires_acknowledgement=False, completion_required=False, expires_at=None,
        recipient_type="employee", recipient_ids=[str(pid) for pid in participant_ids], recipient_department=None,
        is_draft=False, is_forward=False, forwarded_from_id=None, is_pinned=False, closed_at=None,
    )
    await notify_reply_participants(db, reply, thread_id, list(participant_ids))

    result = (await enrich_messages(db, [root or root_or_reply], current_user))[0]
    return result


async def forward_message(db: AsyncSession, current_user: dict, message_id: str, data) -> dict:
    require_company_member(current_user)
    original = await get_accessible_message(db, message_id, current_user)
    if data.recipient_type not in ("employee", "department"):
        raise HTTPException(status_code=400, detail="Invalid recipient type")

    company_id = parse_uuid(current_user["company_id"])
    employee_ids, department_label = await resolve_recipients(db, company_id, data.recipient_type, data.recipient_ids)

    forward_id = uuid.uuid4()
    reference_number = f"MSG-{await counters_repo.increment_and_get(db, f'message_seq:{company_id}'):06d}"
    forward = await messages_repo.create(
        db,
        id=forward_id, reference_number=reference_number, company_id=company_id,
        thread_id=forward_id, parent_message_id=None,
        sender_id=parse_uuid(current_user["id"]), sender_name=current_user.get("name"),
        subject=original.subject if original.subject.startswith("Fwd: ") else f"Fwd: {original.subject}",
        body=f"{data.body}\n\n---\n{original.body}" if data.body else original.body,
        priority=data.priority, confidentiality=original.confidentiality, tags=list(original.tags or []),
        requires_acknowledgement=data.requires_acknowledgement, completion_required=data.completion_required,
        expires_at=_parse_dt(data.expires_at), recipient_type=data.recipient_type,
        recipient_ids=data.recipient_ids, recipient_department=department_label,
        is_draft=False, is_forward=True, forwarded_from_id=original.id, is_pinned=False, closed_at=None,
    )

    # Copy attachment metadata onto the new message so its access-control
    # boundary (get_accessible_message on forward_id) stays simple.
    original_attachments = await attachments_repo.list_by_message(db, original.id)
    now = datetime.now(timezone.utc)
    for att in original_attachments:
        await attachments_repo.create(
            db, message_id=forward_id, thread_id=forward_id, company_id=company_id,
            storage_path=att.storage_path, original_filename=att.original_filename,
            mime_type=att.mime_type, attachment_type=att.attachment_type,
            file_size=att.file_size, checksum=att.checksum, uploaded_by=att.uploaded_by, uploaded_at=now,
        )

    await deliver_message(db, forward, employee_ids)

    result = (await enrich_messages(db, [forward], current_user))[0]
    return result


# ---- Listing ----

async def _list_messages(db: AsyncSession, query, current_user: dict, page: int, page_size: int, sort_field) -> dict:
    page, page_size = paginate_params(page, page_size)
    total = await query.count(db)
    docs = await query.page(db, page, page_size, sort_field)
    items = await enrich_messages(db, docs, current_user)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _apply_common_filters(query, subject, priority, tags, date_from, date_to):
    query.subject_like(subject).priority_eq(priority).tags_overlap(tags)
    query.created_between(
        _parse_dt(date_from) if date_from else None,
        _parse_dt(date_to + "T23:59:59") if date_to else None,
    )


async def get_message_recipients(db: AsyncSession, current_user: dict) -> dict:
    require_company_member(current_user)
    employees = await users_repo.list_employees_by_company(db, parse_uuid(current_user["company_id"]))
    departments = sorted({e.department for e in employees if e.department})
    return {
        "employees": [{"id": str(e.id), "name": e.name, "department": e.department, "position": e.position} for e in employees],
        "departments": departments,
    }


async def get_inbox(db: AsyncSession, current_user: dict, *, page, page_size, subject, priority, tags,
                     status, sender, date_from, date_to, unread_only) -> dict:
    require_company_member(current_user)
    recipient_id = parse_uuid(current_user["id"])
    if unread_only:
        thread_ids = await recipients_repo.thread_ids_for_recipient(
            db, recipient_id, exclude_archived=True, unread_only=True, status=status,
        )
    else:
        thread_ids = await recipients_repo.thread_ids_for_recipient(
            db, recipient_id, exclude_archived=True, role="recipient", status=status,
        )
    query = messages_repo.MessageQuery(parse_uuid(current_user["company_id"])).thread_ids_in(thread_ids)
    query.sender_name_like(sender)
    _apply_common_filters(query, subject, priority, tags, date_from, date_to)
    return await _list_messages(db, query, current_user, page, page_size, Message.created_at)


async def get_sent(db: AsyncSession, current_user: dict, *, page, page_size, subject, priority, tags,
                    date_from, date_to) -> dict:
    require_company_member(current_user)
    query = messages_repo.MessageQuery(parse_uuid(current_user["company_id"])).where(
        Message.sender_id == parse_uuid(current_user["id"]), Message.is_draft.is_(False),
        Message.parent_message_id.is_(None),
    )
    _apply_common_filters(query, subject, priority, tags, date_from, date_to)
    return await _list_messages(db, query, current_user, page, page_size, Message.created_at)


async def get_drafts(db: AsyncSession, current_user: dict, *, page, page_size) -> dict:
    require_company_member(current_user)
    query = messages_repo.MessageQuery(parse_uuid(current_user["company_id"])).where(
        Message.sender_id == parse_uuid(current_user["id"]), Message.is_draft.is_(True),
    )
    return await _list_messages(db, query, current_user, page, page_size, Message.updated_at)


async def get_starred(db: AsyncSession, current_user: dict, *, page, page_size) -> dict:
    require_company_member(current_user)
    recipient_id = parse_uuid(current_user["id"])
    thread_ids = await recipients_repo.thread_ids_for_recipient(db, recipient_id, starred=True)
    query = messages_repo.MessageQuery(parse_uuid(current_user["company_id"])).thread_ids_in(thread_ids)
    return await _list_messages(db, query, current_user, page, page_size, Message.created_at)


async def get_archived(db: AsyncSession, current_user: dict, *, page, page_size) -> dict:
    require_company_member(current_user)
    recipient_id = parse_uuid(current_user["id"])
    thread_ids = await recipients_repo.thread_ids_for_recipient(db, recipient_id, status="archived")
    query = messages_repo.MessageQuery(parse_uuid(current_user["company_id"])).thread_ids_in(thread_ids)
    return await _list_messages(db, query, current_user, page, page_size, Message.created_at)


# ---- Thread detail + workflow actions ----

async def _reconstruct_message_activity(db: AsyncSession, message_ids) -> List[dict]:
    """Reconstructs the old message_activity_log doc shape from the
    unified audit_logs table."""
    rows = await audit_logs_repo.list_for_entities(db, "message", message_ids)
    actor_ids = {r.user_id for r in rows if r.user_id}
    names = await users_repo.get_names_by_ids(db, actor_ids)
    return [{
        "id": str(r.id), "company_id": str(r.company_id), "message_id": str(r.entity_id),
        "actor_id": str(r.user_id) if r.user_id else None, "actor_name": names.get(str(r.user_id)) if r.user_id else None,
        "verb": r.action, "message_subject": (r.new_values or {}).get("subject"),
        "created_at": _iso(r.created_at),
    } for r in rows]


def _attachment_response(a, *, with_data: bool) -> dict:
    data = {
        "id": str(a.id), "message_id": str(a.message_id), "thread_id": str(a.thread_id),
        "company_id": str(a.company_id), "filename": a.original_filename, "mime_type": a.mime_type,
        "attachment_type": a.attachment_type, "size_bytes": a.file_size, "uploaded_at": _iso(a.uploaded_at),
    }
    if with_data:
        # The frontend always uploads via FileReader.readAsDataURL, i.e. a full
        # "data:<mime>;base64,<payload>" string (same contract as task
        # proof_files) - reconstructing that exact prefix here is required for
        # the response to stay byte-identical to the old Mongo-era behavior
        # (which stored/returned the client's original string verbatim).
        data["data"] = f"data:{a.mime_type};base64,{download_base64(a.storage_path)}"
    return data


async def get_message_thread(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    thread_id = message.thread_id

    thread_messages = await messages_repo.list_thread(db, thread_id)
    enriched = await enrich_messages(db, thread_messages, current_user)

    attachments = await attachments_repo.list_by_thread(db, thread_id)
    attachments_by_message = {}
    for a in attachments:
        attachments_by_message.setdefault(str(a.message_id), []).append(_attachment_response(a, with_data=False))

    activity = await _reconstruct_message_activity(db, [m.id for m in thread_messages])

    thread = [{**enriched[i], "attachments": attachments_by_message.get(str(thread_messages[i].id), [])}
              for i in range(len(thread_messages))]

    return {"thread_id": str(thread_id), "messages": thread, "activity": activity}


async def open_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    recipient = await recipients_repo.get_participant(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        return {"message": "Opened"}

    recipient.is_unread = False
    became_seen = recipient.status == "delivered"
    if became_seen:
        recipient.status = "seen"
        recipient.seen_at = datetime.now(timezone.utc)
    await db.flush()

    if became_seen and recipient.role == "recipient":
        await write_message_activity(db, parse_uuid(current_user["company_id"]), message.id, current_user, "opened", message.subject)
    return {"message": "Opened"}


async def accept_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    if str(message.sender_id) == current_user["id"]:
        raise HTTPException(status_code=400, detail="A sender cannot accept their own message")
    if not message.requires_acknowledgement:
        raise HTTPException(status_code=400, detail="This message does not require acknowledgement")
    recipient = await recipients_repo.get_recipient_row(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    if recipient.status not in ("delivered", "seen"):
        raise HTTPException(status_code=400, detail="Message already acknowledged or beyond that stage")

    now = datetime.now(timezone.utc)
    recipient.status = "accepted"
    recipient.accepted_at = now
    recipient.seen_at = recipient.seen_at or now
    recipient.is_unread = False
    await db.flush()
    await write_message_activity(db, parse_uuid(current_user["company_id"]), message.id, current_user, "accepted", message.subject)
    return {"message": "Accepted"}


async def complete_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    if str(message.sender_id) == current_user["id"]:
        raise HTTPException(status_code=400, detail="A sender cannot complete their own message")
    if not message.completion_required:
        raise HTTPException(status_code=400, detail="This message does not require completion")
    recipient = await recipients_repo.get_recipient_row(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    if recipient.status == "completed":
        raise HTTPException(status_code=400, detail="Already completed")
    if message.requires_acknowledgement and recipient.status not in ("accepted", "completed"):
        raise HTTPException(status_code=400, detail="This message must be accepted before it can be completed")

    now = datetime.now(timezone.utc)
    recipient.status = "completed"
    recipient.completed_at = now
    recipient.seen_at = recipient.seen_at or now
    recipient.is_unread = False
    await db.flush()
    await write_message_activity(db, parse_uuid(current_user["company_id"]), message.id, current_user, "completed", message.subject)
    return {"message": "Completed"}


async def archive_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    recipient = await recipients_repo.get_participant(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")

    recipient.status = "archived"
    recipient.archived_at = datetime.now(timezone.utc)
    await db.flush()
    await write_message_activity(db, parse_uuid(current_user["company_id"]), message.id, current_user, "archived", message.subject)
    return {"message": "Archived"}


async def mark_message_unread(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    recipient = await recipients_repo.get_participant(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    recipient.is_unread = True
    await db.flush()
    return {"message": "Marked as unread"}


async def toggle_star_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    message = await get_accessible_message(db, message_id, current_user)
    recipient = await recipients_repo.get_participant(db, message.thread_id, parse_uuid(current_user["id"]))
    if not recipient:
        raise HTTPException(status_code=404, detail="Message not found")
    recipient.is_starred = not recipient.is_starred
    await db.flush()
    return {"message": "Starred" if recipient.is_starred else "Unstarred", "is_starred": recipient.is_starred}


async def close_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    require_company_member(current_user)
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message or str(message.sender_id) != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.closed_at:
        raise HTTPException(status_code=400, detail="Thread already closed")

    now = datetime.now(timezone.utc)
    message.closed_at = now
    message.updated_at = now
    await db.flush()
    await write_message_activity(db, parse_uuid(current_user["company_id"]), message.id, current_user, "closed", message.subject)
    return {"message": "Thread closed"}


async def toggle_pin_message(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    message.is_pinned = not message.is_pinned
    await db.flush()
    return {"message": "Pinned" if message.is_pinned else "Unpinned", "is_pinned": message.is_pinned}


# ---- Attachments ----

async def upload_message_attachment(db: AsyncSession, current_user: dict, message_id: str, data) -> dict:
    require_company_member(current_user)
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message or str(message.sender_id) != current_user["id"]:
        raise HTTPException(status_code=404, detail="Message not found")

    decoded = decode_and_validate(data.data, data.filename)
    storage_path, checksum = upload(decoded, prefix=f"messages/{message.id}")

    attachment = await attachments_repo.create(
        db, message_id=message.id, thread_id=message.thread_id, company_id=parse_uuid(current_user["company_id"]),
        storage_path=storage_path, original_filename=data.filename, mime_type=data.mime_type,
        attachment_type=classify_attachment_type(data.mime_type), file_size=len(decoded), checksum=checksum,
        uploaded_by=parse_uuid(current_user["id"]),
    )
    return {
        "id": str(attachment.id), "filename": attachment.original_filename,
        "attachment_type": attachment.attachment_type, "size_bytes": attachment.file_size,
    }


async def get_message_attachment(db: AsyncSession, current_user: dict, attachment_id: str) -> dict:
    require_company_member(current_user)
    parsed_id = parse_uuid(attachment_id)
    attachment = await attachments_repo.get_by_id(db, parsed_id) if parsed_id else None
    if not attachment or str(attachment.company_id) != current_user["company_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found")
    # Lazy-load boundary: the base64 payload is only ever returned here.
    await get_accessible_message(db, str(attachment.message_id), current_user)
    return _attachment_response(attachment, with_data=True)


# ---- Reminders ----

async def create_reminder(db: AsyncSession, current_user: dict, message_id: str, data) -> dict:
    require_company_member(current_user)
    await get_accessible_message(db, message_id, current_user)

    if data.preset:
        if data.preset not in REMINDER_PRESETS:
            raise HTTPException(status_code=400, detail="Invalid reminder preset")
        if data.preset == "tomorrow":
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            remind_at = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            remind_at = datetime.now(timezone.utc) + timedelta(**REMINDER_PRESETS[data.preset])
    elif data.remind_at:
        remind_at = _parse_dt(data.remind_at)
    else:
        raise HTTPException(status_code=400, detail="Provide a preset or an explicit remind_at")

    reminder = await reminders_repo.create(
        db, message_id=parse_uuid(message_id), user_id=parse_uuid(current_user["id"]),
        company_id=parse_uuid(current_user["company_id"]), remind_at=remind_at,
    )
    return {"id": str(reminder.id), "remind_at": _iso(reminder.remind_at)}


# ---- Communication Center / timeline ----

async def get_communication_center(db: AsyncSession, current_user: dict, **filters) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = parse_uuid(current_user["company_id"])
    query = messages_repo.MessageQuery(company_id).where(Message.parent_message_id.is_(None))
    query.reference_number_like(filters.get("reference_number"))
    query.department_eq(filters.get("department"))
    _apply_common_filters(query, filters.get("subject"), filters.get("priority"), filters.get("tags"),
                          filters.get("date_from"), filters.get("date_to"))
    query.sender_name_like(filters.get("sender"))

    status = filters.get("status")
    status_filters_recipients = bool(status) and status != "closed"
    recipient = filters.get("recipient")
    if recipient or status_filters_recipients:
        matching_ids = await recipients_repo.thread_ids_matching(
            db, company_id, recipient_name=recipient, status=status if status_filters_recipients else None,
        )
        query.where(Message.id.in_(matching_ids))
    if status == "closed":
        query.where(Message.closed_at.is_not(None))

    attachment_type = filters.get("attachment_type")
    if attachment_type:
        matching_ids = await attachments_repo.thread_ids_by_company_and_type(db, company_id, attachment_type)
        query.where(Message.id.in_(matching_ids))

    return await _list_messages(db, query, current_user, filters.get("page", 1), filters.get("page_size", 20), Message.created_at)


async def get_message_timeline(db: AsyncSession, current_user: dict, message_id: str) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    parsed_id = parse_uuid(message_id)
    message = await messages_repo.get_in_company(db, parsed_id, parse_uuid(current_user["company_id"])) if parsed_id else None
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    thread_messages = await messages_repo.list_thread(db, message.thread_id)
    activity = await _reconstruct_message_activity(db, [m.id for m in thread_messages])
    events = [{"timestamp": a["created_at"], "label": a["verb"], "actor_name": a["actor_name"]} for a in activity]

    recipients = await recipients_repo.list_recipient_only_for_thread(db, message.thread_id)
    for r in recipients:
        if r.delivered_at:
            events.append({"timestamp": _iso(r.delivered_at), "label": "delivered", "actor_name": r.recipient_name})

    events.sort(key=lambda e: e["timestamp"])

    recips_only = recipients
    total = len(recips_only)

    def reached(*statuses):
        return sum(1 for r in recips_only if r.status in statuses)

    delivery_progress = {
        "total": total, "delivered": total,
        "seen": reached("seen", "accepted", "completed", "archived"),
        "accepted": reached("accepted", "completed", "archived"),
        "completed": reached("completed", "archived"),
    }

    return {
        "message_id": str(message.id),
        "reference_number": message.reference_number,
        "delivery_progress": delivery_progress,
        "events": events,
    }
