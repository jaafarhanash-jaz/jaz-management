import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.daily_task_assignees as assignees_repo
import repositories.daily_tasks as daily_tasks_repo
import repositories.notifications as notifications_repo
import repositories.task_attachments as attachments_repo
import repositories.tasks as tasks_repo
import repositories.users as users_repo
from services.admin import parse_uuid
from services.storage import classify_attachment_type, decode_and_validate, download_base64, upload

TASK_TERMINAL_UNDELETABLE = ("in_progress",)
TASK_TERMINAL_HISTORY = ("completed", "cancelled")


def _constraint_error_detail(exc: IntegrityError, default: str) -> str:
    """The old Mongo implementation stored any client-supplied
    status/priority/assigned_to value with zero validation. Postgres now
    enforces CHECK/FK constraints (already part of the approved schema,
    not a new decision here) - a previously-silently-accepted invalid
    value must become a clean, specific 400 instead of an unhandled
    IntegrityError -> 500."""
    constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    if constraint == "ck_tasks_status":
        return "Invalid status value"
    if constraint == "ck_tasks_priority":
        return "Invalid priority value"
    if constraint and ("assigned_to" in constraint or "employee_id" in constraint):
        return "assigned_to does not reference an existing user"
    return default


async def _flush_or_400(db: AsyncSession, default_message: str) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, default_message))


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_date(value: Optional[str]) -> Optional[date]:
    """TaskCreate/TaskUpdate/UrgentTaskCreate carry due_date/execution_date
    as plain 'YYYY-MM-DD' strings (unchanged request contract). The old
    Mongo documents stored that string as-is; the real Date column now
    needs an actual date object - asyncpg does not coerce str -> date
    itself. Required for Postgres correctness, not a contract change.
    A malformed date string, which the old code silently stored verbatim,
    now surfaces as a clean 400 instead of corrupt data."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {value!r}")


async def _attachments_response(db: AsyncSession, task_id) -> List[dict]:
    """Fetches attachment metadata for a task and re-inlines the file bytes
    as base64, keeping the response shape byte-identical to the old
    Mongo-era implementation that stored base64 directly on the task
    document - see the migration plan's attachment response-shape decision."""
    rows = await attachments_repo.list_by_task(db, task_id)
    result = []
    for row in rows:
        result.append({
            "filename": row.original_filename,
            "mime_type": row.mime_type,
            "attachment_type": row.attachment_type,
            "data": download_base64(row.storage_path),
            "size_bytes": row.file_size,
        })
    return result


async def _attachments_by_task(db: AsyncSession, task_ids) -> dict:
    """Batched version of _attachments_response for list views - one
    metadata query for every task instead of one per task; still one
    object-storage fetch per attachment (attachments are lazily-fetched
    files, not something to hold in a bigger batch query)."""
    rows = await attachments_repo.list_by_tasks(db, task_ids)
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(str(row.task_id), []).append({
            "filename": row.original_filename,
            "mime_type": row.mime_type,
            "attachment_type": row.attachment_type,
            "data": download_base64(row.storage_path),
            "size_bytes": row.file_size,
        })
    return grouped


def task_response(task, *, assigned_to_name=None, completed_by_name=None, attachments=None) -> dict:
    return {
        "id": str(task.id),
        "company_id": str(task.company_id),
        "assigned_to": str(task.assigned_to),
        "assigned_to_name": assigned_to_name,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "status": task.status,
        "due_date": _iso(task.due_date),
        "requires_proof": task.requires_proof,
        "proof_files": list(task.proof_files or []),
        "attachments": attachments if attachments is not None else [],
        "created_by": str(task.created_by),
        "created_at": _iso(task.created_at),
        "completed_at": _iso(task.completed_at),
        "task_category": task.task_category,
        "alert_delivered_at": _iso(task.alert_delivered_at),
        "received_at": _iso(task.received_at),
        "daily_task_id": str(task.daily_task_id) if task.daily_task_id else None,
        "occurrence_date": _iso(task.occurrence_date),
        "execution_date": _iso(task.execution_date),
        "execution_time": task.execution_time,
        "due_time": task.due_time,
        "started_at": _iso(task.started_at),
        "seen_at": _iso(task.seen_at),
        "completed_by": str(task.completed_by) if task.completed_by else None,
        "completed_by_name": completed_by_name,
        "batch_id": str(task.batch_id) if task.batch_id else None,
    }


async def _store_attachments(db: AsyncSession, task_id, company_id, uploaded_by, attachments):
    """Decodes/validates each owner-provided attachment (same rules as the
    old validate_task_attachments) and stores it in object storage with a
    metadata row, per the already-approved object-storage architecture -
    not a new decision made in this module."""
    if not attachments:
        return
    for a in attachments:
        filename, mime_type, data = a.get("filename"), a.get("mime_type"), a.get("data")
        decoded = decode_and_validate(data, filename)
        storage_path, checksum = upload(decoded, prefix=f"tasks/{task_id}")
        await attachments_repo.create(
            db,
            task_id=task_id,
            company_id=company_id,
            storage_path=storage_path,
            original_filename=filename,
            mime_type=mime_type,
            attachment_type=classify_attachment_type(mime_type),
            file_size=len(decoded),
            checksum=checksum,
            uploaded_by=uploaded_by,
        )


async def list_tasks_for_owner(db: AsyncSession, company_id) -> List[dict]:
    tasks = await tasks_repo.list_by_company(db, company_id)
    assignee_ids = {t.assigned_to for t in tasks}
    completer_ids = {t.completed_by for t in tasks if t.completed_by}
    names = await users_repo.get_names_by_ids(db, assignee_ids | completer_ids)
    attachments_by_task = await _attachments_by_task(db, [t.id for t in tasks])
    return [
        task_response(
            t,
            assigned_to_name=names.get(str(t.assigned_to)),
            completed_by_name=names.get(str(t.completed_by)) if t.completed_by else None,
            attachments=attachments_by_task.get(str(t.id), []),
        )
        for t in tasks
    ]


async def create_task(db: AsyncSession, company_id, created_by, data) -> dict:
    try:
        task = await tasks_repo.create(
            db,
            company_id=company_id,
            assigned_to=data.assigned_to,
            title=data.title,
            description=data.description,
            priority=data.priority,
            status="new",
            due_date=_parse_date(data.due_date),
            due_time=data.due_time,
            requires_proof=data.requires_proof,
            proof_files=[],
            created_by=created_by,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))

    await _store_attachments(db, task.id, company_id, created_by, data.attachments)

    await notifications_repo.create(
        db,
        user_id=data.assigned_to,
        company_id=company_id,
        type="task_assigned",
        title="مهمة جديدة",
        message=f"تم تعيين مهمة جديدة لك: {task.title}",
    )

    await db.refresh(task)
    names = await users_repo.get_names_by_ids(db, [task.assigned_to])
    attachments = await _attachments_response(db, task.id)
    return task_response(task, assigned_to_name=names.get(str(task.assigned_to)), attachments=attachments)


async def update_task(db: AsyncSession, company_id, task_id: str, updates) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_in_company(db, parsed_id, company_id) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    changes = {k: v for k, v in updates.model_dump().items() if v is not None}
    if "due_date" in changes:
        changes["due_date"] = _parse_date(changes["due_date"])
    for field, value in changes.items():
        setattr(task, field, value)
    await _flush_or_400(db, "Invalid task data")
    return {"message": "Task updated successfully"}


async def delete_task(db: AsyncSession, company_id, task_id: str) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_in_company(db, parsed_id, company_id) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Task history must always remain available - in-progress, completed,
    # and cancelled tasks are never physically deleted. An in-progress task
    # can be cancelled instead, which keeps the row.
    if task.status == "in_progress":
        raise HTTPException(status_code=400, detail="Cannot delete a task that is in progress. Cancel it instead to preserve history.")
    if task.status in TASK_TERMINAL_HISTORY:
        raise HTTPException(status_code=400, detail=f"Cannot delete a {task.status} task - history must remain available.")

    # Soft delete per the approved plan (was a hard delete_one in Mongo).
    task.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    return {"message": "Task deleted successfully"}


async def cancel_task(db: AsyncSession, company_id, task_id: str) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_in_company(db, parsed_id, company_id) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in TASK_TERMINAL_HISTORY:
        raise HTTPException(status_code=400, detail=f"Task is already {task.status}")

    task.status = "cancelled"
    await db.flush()
    return {"message": "Task cancelled successfully"}


async def create_urgent_task(db: AsyncSession, company_id, created_by, data) -> dict:
    if not data.assigned_to:
        raise HTTPException(status_code=400, detail="At least one employee must be assigned")

    batch_id = uuid.uuid4()
    created_ids = []
    try:
        for employee_id in data.assigned_to:
            task = await tasks_repo.create(
                db,
                company_id=company_id,
                assigned_to=employee_id,
                title=data.title,
                description=data.description,
                priority="high",
                status="new",
                due_date=_parse_date(data.due_date),
                requires_proof=data.requires_proof,
                proof_files=[],
                created_by=created_by,
                task_category="urgent",
                execution_date=_parse_date(data.execution_date),
                execution_time=data.execution_time,
                due_time=data.due_time,
                batch_id=batch_id,
            )
            created_ids.append(str(task.id))
            await notifications_repo.create(
                db,
                user_id=employee_id,
                company_id=company_id,
                type="urgent_task",
                title="مهمة جديدة",
                message=f"لديك مهمة جديدة: {data.title}",
            )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))
    return {"message": "Urgent task created successfully", "task_ids": created_ids, "batch_id": str(batch_id)}


# ---- Daily task templates ----

async def list_daily_tasks(db: AsyncSession, company_id) -> List[dict]:
    templates = await daily_tasks_repo.list_by_company(db, company_id)
    assignees = await assignees_repo.list_employee_ids_for_templates(db, [t.id for t in templates])
    all_ids = {eid for ids in assignees.values() for eid in ids}
    names = await users_repo.get_names_by_ids(db, all_ids)

    results = []
    for t in templates:
        ids = assignees.get(str(t.id), [])
        results.append({
            "id": str(t.id),
            "company_id": str(t.company_id),
            "title": t.title,
            "description": t.description,
            "assigned_to": [str(i) for i in ids],
            "assigned_to_names": [names[str(i)] for i in ids if str(i) in names],
            "execution_time": t.execution_time,
            "requires_proof": t.requires_proof,
            "is_active": t.is_active,
            "recurrence_type": t.recurrence_type,
            "recurrence_config": t.recurrence_config or {},
            "created_by": str(t.created_by),
            "created_at": _iso(t.created_at),
        })
    return results


async def create_daily_task(db: AsyncSession, company_id, created_by, data) -> dict:
    if not data.assigned_to:
        raise HTTPException(status_code=400, detail="At least one employee must be assigned")

    template = await daily_tasks_repo.create(
        db,
        company_id=company_id,
        title=data.title,
        description=data.description,
        execution_time=data.execution_time,
        requires_proof=data.requires_proof,
        is_active=True,
        recurrence_type="daily",
        recurrence_config={},
        created_by=created_by,
    )
    await db.flush()
    try:
        await assignees_repo.replace_for_template(db, template.id, data.assigned_to)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))

    names = await users_repo.get_names_by_ids(db, data.assigned_to)
    await db.refresh(template)
    return {
        "id": str(template.id),
        "company_id": str(template.company_id),
        "title": template.title,
        "description": template.description,
        "assigned_to": [str(i) for i in data.assigned_to],
        "assigned_to_names": [names[str(i)] for i in data.assigned_to if str(i) in names],
        "execution_time": template.execution_time,
        "requires_proof": template.requires_proof,
        "is_active": template.is_active,
        "recurrence_type": template.recurrence_type,
        "recurrence_config": template.recurrence_config or {},
        "created_by": str(template.created_by),
        "created_at": _iso(template.created_at),
    }


async def update_daily_task(db: AsyncSession, company_id, template_id: str, updates) -> dict:
    changes = updates.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    parsed_id = parse_uuid(template_id)
    template = await daily_tasks_repo.get_in_company(db, parsed_id, company_id) if parsed_id else None
    if not template:
        raise HTTPException(status_code=404, detail="Daily task not found")

    assigned_to = changes.pop("assigned_to", None)
    for field, value in changes.items():
        setattr(template, field, value)
    try:
        if assigned_to is not None:
            await assignees_repo.replace_for_template(db, template.id, assigned_to)
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))
    return {"message": "Daily task updated successfully"}


async def toggle_daily_task(db: AsyncSession, company_id, template_id: str) -> dict:
    parsed_id = parse_uuid(template_id)
    template = await daily_tasks_repo.get_in_company(db, parsed_id, company_id) if parsed_id else None
    if not template:
        raise HTTPException(status_code=404, detail="Daily task not found")

    template.is_active = not template.is_active
    await db.flush()
    return {"message": "Daily task updated successfully", "is_active": template.is_active}


async def delete_daily_task(db: AsyncSession, company_id, template_id: str) -> dict:
    parsed_id = parse_uuid(template_id)
    # Deletes the template only - past daily-task occurrences already
    # created in the tasks table are left untouched, preserving history.
    if not parsed_id or not await daily_tasks_repo.delete_by_id(db, parsed_id, company_id):
        raise HTTPException(status_code=404, detail="Daily task not found")
    return {"message": "Daily task deleted successfully"}


# ---- Employee-facing task actions ----

async def generate_daily_task_instances(db: AsyncSession, employee_id, company_id) -> None:
    """Self-heal on read: lazily creates today's occurrence (as an ordinary
    tasks row) for every active daily task template assigned to this
    employee. Same no-scheduler approach as before."""
    today = datetime.now(timezone.utc).date()
    templates = await daily_tasks_repo.list_active_for_employee(db, company_id, employee_id)

    for template in templates:
        existing = await tasks_repo.get_daily_instance(db, template.id, employee_id, today)
        if existing:
            continue
        await tasks_repo.create(
            db,
            company_id=company_id,
            assigned_to=employee_id,
            title=template.title,
            description=template.description,
            priority="medium",
            status="new",
            due_date=today,
            requires_proof=template.requires_proof,
            proof_files=[],
            created_by=template.created_by,
            task_category="daily",
            daily_task_id=template.id,
            occurrence_date=today,
            execution_time=template.execution_time,
        )
    await db.flush()


async def list_tasks_for_employee(db: AsyncSession, employee) -> List[dict]:
    await generate_daily_task_instances(db, employee["id"], employee["company_id"])

    tasks = await tasks_repo.list_for_employee(db, employee["id"])
    completer_ids = {t.completed_by for t in tasks if t.completed_by}
    names = await users_repo.get_names_by_ids(db, completer_ids)
    attachments_by_task = await _attachments_by_task(db, [t.id for t in tasks])

    results = []
    for task in tasks:
        # Self-heal: the first time a daily/urgent task is returned to the
        # employee, mark it seen.
        if task.task_category in ("urgent", "daily") and task.status == "new":
            task.seen_at = datetime.now(timezone.utc)
            task.status = "seen"
        results.append(task_response(
            task,
            assigned_to_name=employee["name"],
            completed_by_name=names.get(str(task.completed_by)) if task.completed_by else None,
            attachments=attachments_by_task.get(str(task.id), []),
        ))
    await db.flush()
    return results


async def update_task_status(db: AsyncSession, employee, company_id, task_id: str, status_data: dict) -> dict:
    new_status = status_data.get("status")
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_for_employee(db, parsed_id, employee["id"]) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = new_status
    if new_status == "completed":
        task.completed_at = datetime.now(timezone.utc)

    critical_task_started = None
    if new_status == "in_progress":
        # Critical Task Alert "Start Task" reuses this generic endpoint
        # instead of the dedicated /start endpoint, so stamp started_at
        # here too (only if not already set).
        if not task.started_at:
            task.started_at = datetime.now(timezone.utc)
        if task.priority == "critical":
            critical_task_started = task

    await _flush_or_400(db, "Invalid status value")

    if critical_task_started:
        await notifications_repo.create(
            db,
            user_id=critical_task_started.created_by,
            company_id=company_id,
            type="critical_task_started",
            title="بدأ الموظف العمل على المهمة العاجلة",
            message=f"بدأ الموظف {employee['name']} العمل على المهمة العاجلة: {critical_task_started.title}",
        )

    return {"message": "Task status updated successfully"}


async def receive_critical_task(db: AsyncSession, employee, company_id, task_id: str) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_for_employee(db, parsed_id, employee["id"]) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.priority != "critical":
        raise HTTPException(status_code=400, detail="Only urgent tasks use the receive workflow")
    if task.status not in ("new", "seen"):
        raise HTTPException(status_code=400, detail=f"Task cannot be received from status {task.status}")

    received_at = datetime.now(timezone.utc)
    task.status = "received"
    task.received_at = received_at
    await db.flush()

    await notifications_repo.create(
        db,
        user_id=task.created_by,
        company_id=company_id,
        type="critical_task_received",
        title="تم استلام المهمة العاجلة",
        message=f"قام الموظف {employee['name']} باستلام المهمة العاجلة: {task.title}",
    )

    return {"message": "Task received successfully", "received_at": received_at.isoformat()}


async def upload_task_proof(db: AsyncSession, employee_id, task_id: str, proof: dict) -> dict:
    parsed_id = parse_uuid(task_id)
    if not parsed_id or not await tasks_repo.append_proof_file(db, parsed_id, employee_id, proof.get("file_url")):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Proof uploaded successfully"}


async def start_task(db: AsyncSession, employee_id, task_id: str) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_for_employee(db, parsed_id, employee_id) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("new", "seen"):
        raise HTTPException(status_code=400, detail=f"Task cannot be started from status {task.status}")

    started_at = datetime.now(timezone.utc)
    task.status = "in_progress"
    task.started_at = started_at
    await db.flush()
    return {"message": "Task started successfully", "started_at": started_at.isoformat()}


async def complete_task(db: AsyncSession, employee_id, task_id: str) -> dict:
    parsed_id = parse_uuid(task_id)
    task = await tasks_repo.get_for_employee(db, parsed_id, employee_id) if parsed_id else None
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "in_progress":
        raise HTTPException(status_code=400, detail=f"Task cannot be completed from status {task.status}")
    if task.requires_proof and not task.proof_files:
        raise HTTPException(status_code=400, detail="Photo proof is required before this task can be completed")

    completed_at = datetime.now(timezone.utc)
    task.status = "completed"
    task.completed_at = completed_at
    task.completed_by = employee_id
    await db.flush()
    return {"message": "Task completed successfully", "completed_at": completed_at.isoformat()}
