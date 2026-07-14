from datetime import datetime, timezone
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

import repositories.notifications as notifications_repo
import repositories.tasks as tasks_repo
import repositories.users as users_repo


async def pending_critical_tasks(db: AsyncSession, current_user: dict) -> List[dict]:
    """Critical Task Alert fallback detection, piggybacked on the heartbeat
    (no scheduler in this codebase - see the house self-heal-on-read
    convention). First delivery is claimed atomically so concurrent
    heartbeats (multiple tabs) fire the 'delivered' notification exactly
    once. Response shape matches the old Mongo implementation field for
    field; attachments stay [] until the Tasks module migrates the
    attachment pipeline to object storage."""
    pending = await tasks_repo.get_pending_critical_for_employee(db, current_user["id"])

    creator_names = await users_repo.get_names_by_ids(db, {t.created_by for t in pending if t.created_by})

    now = datetime.now(timezone.utc)
    results = []
    for task in pending:
        if not task.alert_delivered_at:
            if await tasks_repo.claim_alert_delivery(db, task.id, now):
                task.alert_delivered_at = now
                await notifications_repo.create(
                    db,
                    user_id=task.created_by,
                    company_id=current_user["company_id"],
                    type="critical_task_delivered",
                    title="تم تسليم مهمة عاجلة",
                    message=f"تم عرض تنبيه المهمة العاجلة على الموظف {current_user['name']} للمهمة: {task.title}",
                )
        results.append({
            "id": str(task.id),
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "status": task.status,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_time": task.due_time,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "created_by_name": creator_names.get(str(task.created_by)),
            "requires_proof": task.requires_proof,
            "attachments": [],
        })
    return results
