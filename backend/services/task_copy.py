from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.daily_task_assignees as daily_task_assignees_repo
import repositories.daily_tasks as daily_tasks_repo
import repositories.tasks as tasks_repo
import repositories.users as users_repo
import services.tasks as tasks_service
from services.admin import parse_uuid

# ---------------------------------------------------------------------------
# Employee task-copy (Part 21) - when an employee leaves and a replacement
# is created, the Owner copies appropriate task CONFIGURATION from the old
# employee to the new one. Both employees are always re-resolved from THIS
# company only (via users_repo.get_employee_in_company), so an Owner can
# never copy across a tenant boundary even by guessing ids. This is a
# COPY, never a MOVE: the old employee's tasks/templates are never
# modified. Role checks happen in the route handlers (server.py), matching
# this codebase's convention - this layer trusts it was already checked.
# ---------------------------------------------------------------------------


async def _resolve_pair(db: AsyncSession, current_user: dict, old_employee_id: str, new_employee_id: str):
    company_id = parse_uuid(current_user["company_id"])
    old_employee = await users_repo.get_employee_in_company(db, parse_uuid(old_employee_id), company_id)
    if not old_employee:
        raise HTTPException(status_code=404, detail="Old employee not found")
    new_employee = await users_repo.get_employee_in_company(db, parse_uuid(new_employee_id), company_id)
    if not new_employee:
        raise HTTPException(status_code=404, detail="New employee not found")
    if old_employee.id == new_employee.id:
        raise HTTPException(status_code=400, detail="Old and new employee must be different")
    return company_id, old_employee, new_employee


async def preview_copy(db: AsyncSession, current_user: dict, old_employee_id: str, new_employee_id: str) -> dict:
    company_id, old_employee, new_employee = await _resolve_pair(db, current_user, old_employee_id, new_employee_id)

    copyable_tasks = await tasks_repo.list_copyable_for_employee(db, old_employee.id, company_id)
    daily_templates = await daily_tasks_repo.list_active_for_employee(db, company_id, old_employee.id)
    critical_count = await tasks_repo.count_critical_for_employee(db, old_employee.id, company_id)

    return {
        "old_employee_id": str(old_employee.id),
        "old_employee_name": old_employee.name,
        "new_employee_id": str(new_employee.id),
        "new_employee_name": new_employee.name,
        "task_count": len(copyable_tasks),
        "daily_task_count": len(daily_templates),
        "critical_task_count_not_copied": critical_count,
    }


TASK_CONFIGURATION_FIELDS = [
    "title", "description", "priority", "requires_proof", "due_date", "due_time", "task_category",
]


async def _copy_tasks(db, old_employee, new_employee, company_id, created_by) -> int:
    copyable_tasks = await tasks_repo.list_copyable_for_employee(db, old_employee.id, company_id)
    for task in copyable_tasks:
        fields = {field: getattr(task, field) for field in TASK_CONFIGURATION_FIELDS}
        await tasks_repo.create(
            db, company_id=company_id, assigned_to=new_employee.id, created_by=created_by,
            status="new", proof_files=[], **fields,
        )
    return len(copyable_tasks)


async def _clone_daily_templates(db, old_employee, new_employee, company_id, created_by) -> int:
    """Clones each template rather than adding the new employee as an
    assignee on the SAME template (Part 21: "do NOT simply attach the new
    employee to the old employee's existing template... future edits must
    not unintentionally affect the old employee") - the clone gets its own
    independent DailyTaskAssignee row pointing only at the new employee;
    the original template and its own assignee list are never touched."""
    templates = await daily_tasks_repo.list_active_for_employee(db, company_id, old_employee.id)
    for template in templates:
        clone = await daily_tasks_repo.create(
            db, company_id=company_id, created_by=created_by, is_active=True,
            title=template.title, description=template.description, execution_time=template.execution_time,
            requires_proof=template.requires_proof, recurrence_type=template.recurrence_type,
            recurrence_config=template.recurrence_config,
        )
        await daily_task_assignees_repo.replace_for_template(db, clone.id, [new_employee.id])
    return len(templates)


async def confirm_copy(db: AsyncSession, current_user: dict, old_employee_id: str, new_employee_id: str, idempotency_key) -> dict:
    company_id, old_employee, new_employee = await _resolve_pair(db, current_user, old_employee_id, new_employee_id)
    created_by = parse_uuid(current_user["id"])

    async def _do_copy():
        task_count = await _copy_tasks(db, old_employee, new_employee, company_id, created_by)
        daily_count = await _clone_daily_templates(db, old_employee, new_employee, company_id, created_by)
        critical_count = await tasks_repo.count_critical_for_employee(db, old_employee.id, company_id)
        await db.flush()
        return {
            "message": "Tasks copied successfully",
            "old_employee_id": str(old_employee.id),
            "new_employee_id": str(new_employee.id),
            "tasks_copied": task_count,
            "daily_tasks_cloned": daily_count,
            "critical_tasks_not_copied": critical_count,
        }

    # Single-company operation (both employees already re-resolved to THIS
    # company above) - fits services.tasks._idempotent exactly, same
    # pattern task creation already uses to survive a double-submit.
    return await tasks_service._idempotent(db, idempotency_key, company_id, created_by, "copy_employee_tasks", _do_copy)
