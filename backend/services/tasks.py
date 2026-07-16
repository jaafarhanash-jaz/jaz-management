import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.daily_task_assignees as assignees_repo
import repositories.daily_tasks as daily_tasks_repo
import repositories.task_attachments as attachments_repo
import repositories.tasks as tasks_repo
import repositories.users as users_repo
import services.notifications as notifications_service
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


def _combine_schedule(date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
    """Scheduled Task activation timestamp (Part 3): combines the owner's
    'YYYY-MM-DD' date + optional 'HH:MM' time into one UTC datetime. Returns
    None (i.e. "activate immediately, not a scheduled task") when no date
    was given, or when the given date/time has already passed - a
    past/blank schedule is not an error, it just means the task should
    behave exactly like a normal one."""
    parsed_date = _parse_date(date_str)
    if not parsed_date:
        return None
    hour, minute = 0, 0
    if time_str:
        try:
            hour, minute = (int(p) for p in time_str.split(":")[:2])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid time format: {time_str!r}")
    activation = datetime(parsed_date.year, parsed_date.month, parsed_date.day, hour, minute, tzinfo=timezone.utc)
    return activation if activation > datetime.now(timezone.utc) else None


async def _attachments_response(db: AsyncSession, task_id) -> List[dict]:
    """Fetches attachment metadata for a task and re-inlines the file bytes
    as base64, keeping the response shape byte-identical to the old
    Mongo-era implementation that stored base64 directly on the task
    document - see the migration plan's attachment response-shape decision.
    The frontend always uploads via FileReader.readAsDataURL, i.e. a full
    "data:<mime>;base64,<payload>" string (same contract as Messaging/
    Calendar attachments) - reconstructing that exact prefix here is
    required for byte-identical old-Mongo-era responses; download_base64()
    alone only returns the bare payload."""
    rows = await attachments_repo.list_by_task(db, task_id)
    result = []
    for row in rows:
        result.append({
            "filename": row.original_filename,
            "mime_type": row.mime_type,
            "attachment_type": row.attachment_type,
            "data": f"data:{row.mime_type};base64,{download_base64(row.storage_path)}",
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
            "data": f"data:{row.mime_type};base64,{download_base64(row.storage_path)}",
            "size_bytes": row.file_size,
        })
    return grouped


def task_response(task, *, assigned_to_name=None, completed_by_name=None, attachments=None, created_by_name=None) -> dict:
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
        "created_by_name": created_by_name,
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
        "scheduled_activation_at": _iso(task.scheduled_activation_at),
        "sequence_order": task.sequence_order,
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


async def activate_due_tasks(db: AsyncSession, company_id) -> None:
    """Self-heal on read (Part 3, Scheduled Tasks): any task still
    status='scheduled' whose scheduled_activation_at has arrived becomes a
    normal visible task now, exactly like the existing message/calendar
    reminder self-heal - no scheduler process. Called from every
    task-list/dashboard read path for the company (owner list, employee
    list, both dashboards, the heartbeat), so activation is felt within
    one poll cycle at most, without any new infrastructure."""
    now = datetime.now(timezone.utc)
    due = await tasks_repo.list_due_scheduled(db, company_id, now)
    for task in due:
        task.status = "new"
    if due:
        await db.flush()
    for task in due:
        await notifications_service.publish(
            db,
            user_id=task.assigned_to,
            company_id=task.company_id,
            category=notifications_service.CATEGORY_TASKS,
            type="task_assigned",
            title="مهمة جديدة",
            message=f"تم تفعيل مهمة مجدولة: {task.title}",
            entity_type="task",
            entity_id=task.id,
            action_url="/employee/tasks",
        )


async def _advance_workflow(db: AsyncSession, task) -> None:
    """Sequential Workflow cascade (Part 4): when a task that is one step
    of a workflow (sequence_order set) completes, activate the next step
    (pending_sequence -> new) and notify its assignee. No-op for ordinary
    tasks (sequence_order is None) and for the last step (no next row)."""
    if task.sequence_order is None or task.batch_id is None:
        return
    next_task = await tasks_repo.get_next_workflow_step(db, task.batch_id, task.sequence_order + 1)
    if not next_task or next_task.status != "pending_sequence":
        return
    next_task.status = "new"
    await db.flush()
    await notifications_service.publish(
        db,
        user_id=next_task.assigned_to,
        company_id=next_task.company_id,
        category=notifications_service.CATEGORY_TASKS,
        type="task_assigned",
        title="مهمة جديدة",
        message=f"دورك الآن في سير العمل: {next_task.title}",
        entity_type="task",
        entity_id=next_task.id,
        action_url="/employee/tasks",
    )


async def get_workflow_progress(db: AsyncSession, company_id) -> List[dict]:
    """Powers the Owner Dashboard's Sequential Workflow widget (Part 4) -
    every workflow that still has at least one non-terminal step, with each
    step's assignee and current status, in order."""
    batch_ids = await tasks_repo.list_active_workflow_batch_ids(db, company_id)
    workflows = []
    for batch_id in batch_ids:
        steps = await tasks_repo.list_workflow_steps(db, batch_id)
        if not steps:
            continue
        names = await users_repo.get_names_by_ids(db, [s.assigned_to for s in steps])
        workflows.append({
            "batch_id": str(batch_id),
            "title": steps[0].title,
            "steps": [
                {
                    "task_id": str(s.id),
                    "employee_id": str(s.assigned_to),
                    "employee_name": names.get(str(s.assigned_to)),
                    "sequence_order": s.sequence_order,
                    "status": s.status,
                }
                for s in steps
            ],
        })
    return workflows


async def create_task_workflow(db: AsyncSession, company_id, created_by, data) -> dict:
    """Sequential Workflow creation (Part 4): one task row per employee in
    `data.employee_order`, sharing one batch_id and carrying its position
    in `sequence_order`. Only the first row is ever visible immediately
    (status='new', or 'scheduled' if a future activation date was also
    given) - every later row starts at 'pending_sequence' and only becomes
    visible when _advance_workflow activates it on the previous step's
    completion. Reuses the exact same Task row/create_task machinery as a
    plain task - just N rows instead of one, linked the same way the
    existing parallel "urgent task to N employees" batch_id already links
    rows, distinguished by sequence_order being set."""
    if len(data.employee_order) < 2:
        raise HTTPException(status_code=400, detail="Sequential workflow requires at least two employees, in order")

    scheduled_at = _combine_schedule(getattr(data, "scheduled_date", None), getattr(data, "scheduled_time", None))
    batch_id = uuid.uuid4()
    created_tasks = []
    try:
        for index, employee_id in enumerate(data.employee_order):
            if index == 0:
                status = "scheduled" if scheduled_at else "new"
            else:
                status = "pending_sequence"
            task = await tasks_repo.create(
                db,
                company_id=company_id,
                assigned_to=employee_id,
                title=data.title,
                description=data.description,
                priority=data.priority,
                status=status,
                due_date=_parse_date(data.due_date) if data.due_date else None,
                due_time=data.due_time,
                requires_proof=data.requires_proof,
                proof_files=[],
                created_by=created_by,
                batch_id=batch_id,
                sequence_order=index,
                scheduled_activation_at=scheduled_at if index == 0 else None,
            )
            created_tasks.append(task)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))

    await db.flush()

    if created_tasks[0].status == "new":
        await notifications_service.publish(
            db,
            user_id=created_tasks[0].assigned_to,
            company_id=company_id,
            category=notifications_service.CATEGORY_TASKS,
            type="task_assigned",
            title="مهمة جديدة",
            message=f"بدأ سير عمل تسلسلي - دورك الآن: {created_tasks[0].title}",
            entity_type="task",
            entity_id=created_tasks[0].id,
            action_url="/employee/tasks",
        )

    names = await users_repo.get_names_by_ids(db, [t.assigned_to for t in created_tasks])
    return {
        "batch_id": str(batch_id),
        "steps": [task_response(t, assigned_to_name=names.get(str(t.assigned_to))) for t in created_tasks],
    }


async def list_tasks_for_owner(db: AsyncSession, company_id) -> List[dict]:
    """Active Tasks view (Task History feature): everything except
    completed tasks, which live in the separate permanent archive - see
    list_completed_tasks_for_owner. No task is ever deleted or moved to get
    there; this is purely a different filter over the same table."""
    await activate_due_tasks(db, company_id)
    tasks = await tasks_repo.list_active_by_company(db, company_id)
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


def _day_bounds(date_str: Optional[str], *, end: bool = False) -> Optional[datetime]:
    """'YYYY-MM-DD' -> a UTC boundary for filtering a DateTime column by
    calendar day: start of that day, or the start of the *next* day
    (exclusive upper bound) when end=True - so a `_to` filter still
    includes every moment of the given day."""
    if not date_str:
        return None
    day = date.fromisoformat(date_str)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start + timedelta(days=1) if end else start


_PRIORITY_SORT_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


async def list_completed_tasks_for_owner(
    db: AsyncSession, company_id, *,
    search=None, employee_id=None, created_by=None, priority=None,
    created_from=None, created_to=None, completed_from=None, completed_to=None,
    sort=None,
) -> List[dict]:
    """Task History (permanent completed-task archive): reuses the exact
    same Task rows and task_response() shape as the active list - nothing
    is duplicated or copied, only a different filtered/sorted read of the
    same table. Intended as the future data source for analytics/employee
    performance evaluation, per the feature spec."""
    tasks = await tasks_repo.list_completed_by_company(
        db, company_id,
        search=search,
        employee_id=parse_uuid(employee_id) if employee_id else None,
        created_by=parse_uuid(created_by) if created_by else None,
        priority=priority,
        created_from=_day_bounds(created_from),
        created_to=_day_bounds(created_to, end=True),
        completed_from=_day_bounds(completed_from),
        completed_to=_day_bounds(completed_to, end=True),
    )
    assignee_ids = {t.assigned_to for t in tasks}
    creator_ids = {t.created_by for t in tasks}
    completer_ids = {t.completed_by for t in tasks if t.completed_by}
    names = await users_repo.get_names_by_ids(db, assignee_ids | creator_ids | completer_ids)
    attachments_by_task = await _attachments_by_task(db, [t.id for t in tasks])

    results = [
        task_response(
            t,
            assigned_to_name=names.get(str(t.assigned_to)),
            completed_by_name=names.get(str(t.completed_by)) if t.completed_by else None,
            created_by_name=names.get(str(t.created_by)),
            attachments=attachments_by_task.get(str(t.id), []),
        )
        for t in tasks
    ]

    # completed_desc (newest completed first) is already the repo's default
    # DB-level order; the remaining options need values (names, priority
    # rank) that only exist after the response dicts above are built.
    if sort == "completed_asc":
        results.sort(key=lambda r: r["completed_at"] or "")
    elif sort == "created_desc":
        results.sort(key=lambda r: r["created_at"] or "", reverse=True)
    elif sort == "employee_name":
        results.sort(key=lambda r: r["assigned_to_name"] or "")
    elif sort == "priority":
        results.sort(key=lambda r: _PRIORITY_SORT_ORDER.get(r["priority"], 99))

    return results


async def create_task(db: AsyncSession, company_id, created_by, data) -> dict:
    scheduled_at = _combine_schedule(getattr(data, "scheduled_date", None), getattr(data, "scheduled_time", None))
    initial_status = "scheduled" if scheduled_at else "new"
    try:
        task = await tasks_repo.create(
            db,
            company_id=company_id,
            assigned_to=data.assigned_to,
            title=data.title,
            description=data.description,
            priority=data.priority,
            status=initial_status,
            due_date=_parse_date(data.due_date),
            due_time=data.due_time,
            requires_proof=data.requires_proof,
            proof_files=[],
            created_by=created_by,
            scheduled_activation_at=scheduled_at,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=_constraint_error_detail(exc, "Invalid task data"))

    await _store_attachments(db, task.id, company_id, created_by, data.attachments)

    # A Scheduled Task (Part 3) must be completely invisible to its
    # assignee - including the notification - until self-heal activates it
    # (see activate_due_tasks below).
    if initial_status == "new":
        await notifications_service.publish(
            db,
            user_id=data.assigned_to,
            company_id=company_id,
            category=notifications_service.CATEGORY_TASKS,
            type="task_assigned",
            title="مهمة جديدة",
            message=f"تم تعيين مهمة جديدة لك: {task.title}",
            entity_type="task",
            entity_id=task.id,
            action_url="/employee/tasks",
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
            await notifications_service.publish(
                db,
                user_id=employee_id,
                company_id=company_id,
                category=notifications_service.CATEGORY_URGENT_TASKS,
                type="urgent_task",
                title="مهمة جديدة",
                message=f"لديك مهمة جديدة: {data.title}",
                entity_type="task",
                entity_id=task.id,
                action_url="/employee/tasks",
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
    await activate_due_tasks(db, parse_uuid(employee["company_id"]))

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
        await notifications_service.publish(
            db,
            user_id=critical_task_started.created_by,
            company_id=company_id,
            category=notifications_service.CATEGORY_CRITICAL_TASKS,
            type="critical_task_started",
            title="بدأ الموظف العمل على المهمة العاجلة",
            message=f"بدأ الموظف {employee['name']} العمل على المهمة العاجلة: {critical_task_started.title}",
            entity_type="task",
            entity_id=critical_task_started.id,
            action_url="/company-owner/tasks",
            sender_id=employee["id"],
            sender_name=employee["name"],
        )

    if new_status == "completed":
        await notifications_service.publish(
            db,
            user_id=task.created_by,
            company_id=company_id,
            category=notifications_service.CATEGORY_TASKS,
            type="task_completed",
            title="اكتملت المهمة",
            message=f"أكمل الموظف {employee['name']} المهمة: {task.title}",
            entity_type="task",
            entity_id=task.id,
            action_url="/company-owner/tasks",
            sender_id=employee["id"],
            sender_name=employee["name"],
        )
        await _advance_workflow(db, task)

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

    await notifications_service.publish(
        db,
        user_id=task.created_by,
        company_id=company_id,
        category=notifications_service.CATEGORY_CRITICAL_TASKS,
        type="critical_task_received",
        title="تم استلام المهمة العاجلة",
        message=f"قام الموظف {employee['name']} باستلام المهمة العاجلة: {task.title}",
        entity_type="task",
        entity_id=task.id,
        action_url="/company-owner/tasks",
        sender_id=employee["id"],
        sender_name=employee["name"],
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

    employee_name = (await users_repo.get_by_id(db, employee_id)).name
    await notifications_service.publish(
        db,
        user_id=task.created_by,
        company_id=task.company_id,
        category=notifications_service.CATEGORY_TASKS,
        type="task_completed",
        title="اكتملت المهمة",
        message=f"أكمل الموظف {employee_name} المهمة: {task.title}",
        entity_type="task",
        entity_id=task.id,
        action_url="/company-owner/tasks",
        sender_id=employee_id,
        sender_name=employee_name,
    )
    await _advance_workflow(db, task)

    return {"message": "Task completed successfully", "completed_at": completed_at.isoformat()}
