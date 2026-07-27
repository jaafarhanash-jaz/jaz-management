from typing import Optional

import repositories.work_schedules as work_schedules_repo
from models import WorkSchedule
from services.attendance_calc_engine import ScheduleSnapshot


def _break_minutes(schedule: WorkSchedule) -> float:
    if not schedule.break_start_time or not schedule.break_end_time:
        return 0.0
    start_h, start_m = (int(p) for p in schedule.break_start_time.split(":"))
    end_h, end_m = (int(p) for p in schedule.break_end_time.split(":"))
    return float((end_h * 60 + end_m) - (start_h * 60 + start_m))


async def get_effective_schedule_for_employee(
    db, *, schedule_id=None, company_id=None
) -> Optional[WorkSchedule]:
    """Resolution order: the employee's own assignment, else the company's
    default schedule, else None (a company that hasn't set up Work
    Schedules yet - every caller must handle this gracefully, never crash).

    Takes plain `schedule_id`/`company_id` values rather than a `User` ORM
    object - every real call site (check-in/out, dashboards, `/auth/me`)
    already has these as parsed UUIDs from the JWT-derived `current_user`
    dict this codebase passes everywhere, not a freshly-fetched User row.

    Deliberately NOT filtered by WorkSchedule.is_active - see the
    is_active comment on the model. services/work_schedules.py's
    deactivate_schedule already prevents this situation from arising
    through normal use (a schedule can't be deactivated while it's the
    default or still assigned to anyone), but this stays robust
    regardless of how the data got here.
    """
    if schedule_id:
        return await work_schedules_repo.get_by_id_and_company(db, schedule_id, company_id)
    if company_id:
        return await work_schedules_repo.get_default_for_company(db, company_id)
    return None


def to_snapshot(schedule: Optional[WorkSchedule]) -> Optional[ScheduleSnapshot]:
    if schedule is None:
        return None
    return ScheduleSnapshot(
        schedule_id=str(schedule.id),
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        break_minutes=_break_minutes(schedule),
        required_minutes=schedule.required_hours * 60,
    )
