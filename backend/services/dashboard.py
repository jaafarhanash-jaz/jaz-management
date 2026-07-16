from datetime import date as date_type, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.attendance as attendance_repo
import repositories.companies as companies_repo
import repositories.dashboard_layouts as dashboard_layouts_repo
import repositories.daily_tasks as daily_tasks_repo
import repositories.notifications as notifications_repo
import repositories.reports as reports_repo
import repositories.tasks as tasks_repo
import repositories.users as users_repo
import services.attendance as attendance_service
import services.calendar as calendar_service
import services.holidays as holidays_service
import services.messages as messages_service
import services.notifications as notifications_service
import services.tasks as tasks_service
from services.admin import parse_uuid

# Employee Workspace (Part 2) - "Focus Now" priority order, exactly as
# specified: Critical > Urgent > High > nearest due date. Only tasks the
# employee still needs to act on are candidates - 'pending_review' (already
# done their part, waiting on the owner) and terminal statuses are excluded.
FOCUS_CANDIDATE_STATUSES = ("new", "received", "seen", "in_progress", "rejected", "overdue")


def _due_sort_key(task: dict):
    due = task.get("due_date")
    return (due is None, due or "", task.get("due_time") or "")


def _pick_focus_task(tasks: list):
    candidates = [t for t in tasks if t["status"] in FOCUS_CANDIDATE_STATUSES]
    if not candidates:
        return None, None
    critical = [t for t in candidates if t["priority"] == "critical"]
    if critical:
        return sorted(critical, key=_due_sort_key)[0], "critical"
    urgent = [t for t in candidates if t["task_category"] == "urgent"]
    if urgent:
        return sorted(urgent, key=_due_sort_key)[0], "urgent"
    high = [t for t in candidates if t["priority"] == "high"]
    if high:
        return sorted(high, key=_due_sort_key)[0], "high"
    return sorted(candidates, key=_due_sort_key)[0], "nearest_due"

OPEN_STATUSES = ("new", "seen", "in_progress")
# Scheduled Tasks not yet activated, and later steps of a Sequential
# Workflow not yet reached - both real tasks, but per the explicit "does
# not affect dashboards" requirement they are excluded from every
# owner-facing count/chart until self-heal (activate_due_tasks) or the
# workflow cascade actually activates them. The owner still sees them in
# the plain Tasks list (they need to manage/cancel them), just not counted
# here.
HIDDEN_TASK_STATUSES = ("scheduled", "pending_sequence")


def compute_star_rating(score: float) -> str:
    if score >= 90:
        return "★★★★★ Excellent"
    if score >= 75:
        return "★★★★ Good"
    if score >= 50:
        return "★★★ Average"
    return "★★ Needs Improvement"


def _duration_minutes(start, end) -> Optional[float]:
    if not start or not end:
        return None
    delta = (end - start).total_seconds() / 60
    return delta if delta >= 0 else None


async def get_owner_dashboard(db: AsyncSession, current_user: dict) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = parse_uuid(current_user["company_id"])
    today = datetime.now(timezone.utc).date()
    today_holiday = await holidays_service.get_day_off_info(db, company_id, today)
    working_hours = await companies_repo.get_working_hours(db, company_id)
    await tasks_service.activate_due_tasks(db, company_id)

    # Scope attendance stats to CURRENT employees only, deduped per employee -
    # counting raw attendance rows would let orphaned rows (from deleted
    # employees) or a stray duplicate same-day check-in inflate the counts.
    employees = await users_repo.list_employees_by_company(db, company_id)
    employee_ids = {e.id for e in employees}
    total_employees = len(employee_ids)

    today_rows = await attendance_repo.list_by_company(db, company_id, date_=today)
    status_by_employee = {}
    checked_out_ids = set()
    for row in today_rows:
        if row.employee_id not in employee_ids:
            continue
        status_by_employee[row.employee_id] = row.status
        if row.check_out_time:
            checked_out_ids.add(row.employee_id)

    present_today = sum(1 for s in status_by_employee.values() if s == "present")
    late_today = 0 if today_holiday else sum(1 for s in status_by_employee.values() if s == "late")
    absent_today = 0 if today_holiday else max(0, total_employees - present_today - late_today)
    checked_out_today = len(checked_out_ids)
    attendance_percentage = min(100.0, round((present_today + late_today) / total_employees * 100, 1)) if total_employees > 0 else 0

    all_tasks = [t for t in await tasks_repo.list_by_company(db, company_id) if t.status not in HIDDEN_TASK_STATUSES]
    open_tasks = sum(1 for t in all_tasks if t.status in OPEN_STATUSES)
    completed_tasks = sum(1 for t in all_tasks if t.status == "completed")
    overdue_tasks = sum(1 for t in all_tasks if t.status == "overdue")
    pending_urgent_tasks = sum(1 for t in all_tasks if t.task_category == "urgent" and t.status in OPEN_STATUSES)
    completed_urgent_tasks = sum(1 for t in all_tasks if t.task_category == "urgent" and t.status == "completed")

    daily_tasks = await daily_tasks_repo.list_by_company(db, company_id)
    active_daily_tasks = sum(1 for t in daily_tasks if t.is_active)

    reports = await reports_repo.list_by_company(db, company_id)
    recent_reports = sorted(reports, key=lambda r: r.created_at, reverse=True)[:5]

    return {
        "total_employees": total_employees,
        "present_today": present_today,
        "late_today": late_today,
        "absent_today": absent_today,
        "checked_out_today": checked_out_today,
        "attendance_percentage": attendance_percentage,
        "open_tasks": open_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "active_daily_tasks": active_daily_tasks,
        "pending_urgent_tasks": pending_urgent_tasks,
        "completed_urgent_tasks": completed_urgent_tasks,
        "recent_reports": [_report_response(r) for r in recent_reports],
        "calendar_widgets": await calendar_service.compute_calendar_dashboard_widgets(db, current_user, is_owner=True),
        "today_is_holiday": today_holiday is not None,
        "today_holiday_type": today_holiday["type"] if today_holiday else None,
        "today_holiday_title": today_holiday["title"] if today_holiday else None,
        "weekly_holiday_days": [d for d in range(7) if d not in working_hours["working_days"]],
    }


def _report_response(r) -> dict:
    return {
        "id": str(r.id), "employee_id": str(r.employee_id), "employee_name": r.employee_name,
        "company_id": str(r.company_id), "title": r.title, "description": r.description,
        "files": list(r.files or []), "images": list(r.images or []), "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def get_owner_analytics(db: AsyncSession, current_user: dict) -> dict:
    """Employee performance analytics, computed entirely from existing task
    and attendance data - no AI, no new data sources. Reuses the same
    completion_rate/attendance_rate calculation as /employee/performance,
    extended with more factors and aggregated across the whole company."""
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = parse_uuid(current_user["company_id"])
    await tasks_service.activate_due_tasks(db, company_id)
    employees = await users_repo.list_employees_by_company(db, company_id)
    all_tasks = [t for t in await tasks_repo.list_by_company(db, company_id) if t.status not in HIDDEN_TASK_STATUSES]

    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def completed_since(task, start_date):
        if not task.completed_at:
            return False
        return task.completed_at.date() >= start_date

    completed_tasks_all = [t for t in all_tasks if t.status == "completed"]
    pending_tasks_all = [t for t in all_tasks if t.status in OPEN_STATUSES]
    urgent_tasks_all = [t for t in all_tasks if t.task_category == "urgent"]

    tasks_completed_today = sum(1 for t in completed_tasks_all if t.completed_at and t.completed_at.date() == today)
    tasks_completed_week = sum(1 for t in completed_tasks_all if completed_since(t, week_start))
    tasks_completed_month = sum(1 for t in completed_tasks_all if completed_since(t, month_start))

    completion_durations = [d for t in all_tasks if (d := _duration_minutes(t.started_at, t.completed_at)) is not None]
    avg_completion_time = round(sum(completion_durations) / len(completion_durations), 1) if completion_durations else None

    response_durations = [d for t in all_tasks if (d := _duration_minutes(t.created_at, t.started_at)) is not None]
    avg_response_time = round(sum(response_durations) / len(response_durations), 1) if response_durations else None

    # Fetched once and holiday-filtered here so both the company-wide and
    # per-employee stats below share a single exclusion pass - a stray
    # check-in on a company holiday must not count as a "late" violation or
    # skew anyone's attendance rate.
    all_attendance = await attendance_repo.list_by_company(db, company_id)
    all_attendance = await _filter_out_holiday_attendance(db, company_id, all_attendance)

    total_attendance_all = len(all_attendance)
    present_attendance_all = sum(1 for a in all_attendance if a.status == "present")
    late_attendance_all = sum(1 for a in all_attendance if a.status == "late")
    company_attendance_rate = round((present_attendance_all / total_attendance_all * 100), 1) if total_attendance_all > 0 else 0

    employee_stats = []
    for emp in employees:
        emp_tasks = [t for t in all_tasks if t.assigned_to == emp.id]
        emp_completed = [t for t in emp_tasks if t.status == "completed"]
        emp_urgent = [t for t in emp_tasks if t.task_category == "urgent"]
        emp_urgent_completed = [t for t in emp_urgent if t.status == "completed"]

        emp_attendance = [a for a in all_attendance if a.employee_id == emp.id]
        emp_total_days = len(emp_attendance)
        emp_present_days = sum(1 for a in emp_attendance if a.status == "present")
        emp_late_days = sum(1 for a in emp_attendance if a.status == "late")

        completion_rate = (len(emp_completed) / len(emp_tasks) * 100) if emp_tasks else 0
        urgent_completion_rate = (len(emp_urgent_completed) / len(emp_urgent) * 100) if emp_urgent else 100
        attendance_rate = (emp_present_days / emp_total_days * 100) if emp_total_days > 0 else 0

        emp_durations = [d for t in emp_completed if (d := _duration_minutes(t.started_at, t.completed_at)) is not None]
        emp_avg_completion = round(sum(emp_durations) / len(emp_durations), 1) if emp_durations else None

        if emp_avg_completion is None:
            speed_score = 70
        elif emp_avg_completion <= 60:
            speed_score = 100
        elif emp_avg_completion <= 180:
            speed_score = 75
        elif emp_avg_completion <= 480:
            speed_score = 50
        else:
            speed_score = 25

        late_penalty = min(emp_late_days * 2, 20)
        performance_score = (
            completion_rate * 0.35 + urgent_completion_rate * 0.20 + attendance_rate * 0.25 + speed_score * 0.20
        ) - late_penalty
        performance_score = max(0, min(100, round(performance_score, 1)))

        employee_stats.append({
            "employee_id": str(emp.id), "name": emp.name, "total_tasks": len(emp_tasks),
            "completed_tasks": len(emp_completed), "completion_rate": round(completion_rate, 1),
            "urgent_tasks": len(emp_urgent), "urgent_completed": len(emp_urgent_completed),
            "avg_completion_time_minutes": emp_avg_completion, "attendance_rate": round(attendance_rate, 1),
            "late_count": emp_late_days, "performance_score": performance_score,
            "rating": compute_star_rating(performance_score),
        })

    employee_stats.sort(key=lambda e: e["performance_score"], reverse=True)
    for idx, e in enumerate(employee_stats):
        e["rank"] = idx + 1

    insights = []
    for e in employee_stats:
        if e["urgent_tasks"] >= 2 and e["urgent_completed"] == e["urgent_tasks"]:
            insights.append(f"{e['name']} consistently completes urgent tasks quickly.")
        if e["late_count"] >= 3:
            insights.append(f"{e['name']} has frequent late arrivals.")
    if employee_stats:
        top = max(employee_stats, key=lambda e: e["completion_rate"])
        if top["total_tasks"] > 0:
            insights.append(f"{top['name']} has the highest completion rate this month.")

    tasks_completed_over_time = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        count = sum(1 for t in completed_tasks_all if t.completed_at and t.completed_at.date() == day)
        tasks_completed_over_time.append({"date": day.isoformat(), "count": count})

    status_counts = {}
    for t in all_tasks:
        status_counts[t.status or "unknown"] = status_counts.get(t.status or "unknown", 0) + 1
    task_distribution = [{"status": s, "count": c} for s, c in status_counts.items()]

    attendance_trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_rows = [a for a in all_attendance if a.date == day]
        day_total = len(day_rows)
        day_present = sum(1 for a in day_rows if a.status == "present")
        rate = round((day_present / day_total * 100), 1) if day_total > 0 else 0
        attendance_trend.append({"date": day.isoformat(), "rate": rate})

    return {
        "total_tasks": len(all_tasks), "completed_tasks": len(completed_tasks_all),
        "pending_tasks": len(pending_tasks_all), "urgent_tasks": len(urgent_tasks_all),
        "avg_completion_time_minutes": avg_completion_time, "avg_response_time_minutes": avg_response_time,
        "tasks_completed_today": tasks_completed_today, "tasks_completed_week": tasks_completed_week,
        "tasks_completed_month": tasks_completed_month, "attendance_rate": company_attendance_rate,
        "late_attendance_count": late_attendance_all, "employee_ranking": employee_stats, "insights": insights,
        "charts": {
            "tasks_completed_over_time": tasks_completed_over_time, "task_distribution": task_distribution,
            "employee_performance_ranking": [{"name": e["name"], "score": e["performance_score"]} for e in employee_stats],
            "attendance_trend": attendance_trend,
        },
    }


async def _filter_out_holiday_attendance(db: AsyncSession, company_id, records: list) -> list:
    """Drops attendance rows whose date falls on a non-working day (named
    company holiday or configured weekly holiday) - a stray check-in on a
    day off must never count as a late/absent violation or skew the
    attendance-rate denominator. One holiday lookup per distinct date."""
    dates = {r.date for r in records if r.date}
    if not dates:
        return records
    off_dates = {d for d in dates if await holidays_service.get_day_off_info(db, company_id, d)}
    if not off_dates:
        return records
    return [r for r in records if r.date not in off_dates]


async def get_employee_dashboard(db: AsyncSession, current_user: dict) -> dict:
    if current_user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Access denied")

    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])
    today = datetime.now(timezone.utc).date()
    await tasks_service.activate_due_tasks(db, company_id)

    tasks_raw = await tasks_repo.list_for_employee(db, employee_id)
    total_tasks = len(tasks_raw)
    completed_tasks = sum(1 for t in tasks_raw if t.status == "completed")
    pending_tasks = sum(1 for t in tasks_raw if t.status in ("new", "in_progress"))
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # My Tasks / Focus Now (Part 2): everything except completed/cancelled -
    # the active workspace, not the archive. created_by resolved so each
    # card can show its creator, per the Employee Workspace spec.
    active_raw = [t for t in tasks_raw if t.status not in ("completed", "cancelled")]
    creator_ids = {t.created_by for t in active_raw}
    names = await users_repo.get_names_by_ids(db, creator_ids)
    active_tasks = [
        tasks_service.task_response(t, assigned_to_name=current_user["name"], created_by_name=names.get(str(t.created_by)))
        for t in active_raw
    ]
    focus_task, focus_reason = _pick_focus_task(active_tasks)

    attendance = await attendance_repo.get_for_employee_on_date(db, employee_id, today)
    attendance_widget = attendance_service.attendance_response(attendance) if attendance else None

    # Messages widget: reuses the exact same unread-inbox query the sidebar
    # badge and the Work Messages page already use - no new message-listing
    # logic, just a small page_size for a preview.
    inbox = await messages_service.get_inbox(
        db, current_user, page=1, page_size=3, subject=None, priority=None, tags=None,
        status=None, sender=None, date_from=None, date_to=None, unread_only=True,
    )
    messages_widget = {
        "unread_conversations": inbox["total"],
        "recent": [
            {"sender_name": m["sender_name"], "subject": m["subject"], "preview": (m["body"] or "")[:140]}
            for m in inbox["items"]
        ],
    }

    # latest_notification kept for backward compatibility (pre-existing
    # field) - the redesigned dashboard's Notifications widget uses the
    # real-time useNotifications() hook instead (same one NotificationBell
    # already uses), not a second notification-fetch path here.
    notifications = await notifications_repo.list_for_user(db, employee_id, limit=1)
    latest_notification = notifications_service.notification_response(notifications[0]) if notifications else None

    today_holiday = await holidays_service.get_day_off_info(db, company_id, today)

    return {
        "total_tasks": total_tasks, "completed_tasks": completed_tasks, "pending_tasks": pending_tasks,
        "completion_rate": round(completion_rate, 1),
        "checked_in": attendance is not None and attendance.check_in_time is not None,
        "checked_out": attendance is not None and attendance.check_out_time is not None,
        "tasks": active_tasks,
        "focus_task": focus_task,
        "focus_reason": focus_reason,
        "attendance": attendance_widget,
        "messages_widget": messages_widget,
        "latest_notification": latest_notification,
        "calendar_widgets": await calendar_service.compute_calendar_dashboard_widgets(db, current_user, is_owner=False),
        "today_is_holiday": today_holiday is not None,
        "today_holiday_type": today_holiday["type"] if today_holiday else None,
        "today_holiday_title": today_holiday["title"] if today_holiday else None,
    }


async def get_employee_performance(db: AsyncSession, current_user: dict) -> dict:
    if current_user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Access denied")

    employee_id = parse_uuid(current_user["id"])
    company_id = parse_uuid(current_user["company_id"])

    tasks = await tasks_repo.list_for_employee(db, employee_id)
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == "completed")
    overdue_tasks = sum(1 for t in tasks if t.status == "overdue")

    employee_attendance = await attendance_repo.list_by_company(db, company_id, employee_id=employee_id)
    employee_attendance = await _filter_out_holiday_attendance(db, company_id, employee_attendance)
    total_days = len(employee_attendance)
    present_days = sum(1 for a in employee_attendance if a.status == "present")
    late_days = sum(1 for a in employee_attendance if a.status == "late")
    absent_days = sum(1 for a in employee_attendance if a.status == "absent")

    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    attendance_rate = (present_days / total_days * 100) if total_days > 0 else 0
    performance_score = completion_rate * 0.6 + attendance_rate * 0.4

    return {
        "total_tasks": total_tasks, "completed_tasks": completed_tasks, "overdue_tasks": overdue_tasks,
        "completion_rate": round(completion_rate, 1), "total_days": total_days, "present_days": present_days,
        "late_days": late_days, "absent_days": absent_days, "attendance_rate": round(attendance_rate, 1),
        "performance_score": round(performance_score, 1),
    }


async def get_dashboard_layout(db: AsyncSession, current_user: dict) -> dict:
    """Per-user widget layout for the customizable dashboard (Part 2). Not
    role-gated - any authenticated user has exactly one layout row. Missing
    row (first login, or first time a user reaches a customizable
    dashboard) returns an empty layout; the frontend's WIDGET_REGISTRY
    fills in defaults for every widget key absent from the saved list, so
    a brand-new widget shipped later needs no backend change here."""
    row = await dashboard_layouts_repo.get_by_user(db, parse_uuid(current_user["id"]))
    return {"layout": row.layout if row else []}


async def save_dashboard_layout(db: AsyncSession, current_user: dict, layout: list) -> dict:
    row = await dashboard_layouts_repo.upsert(db, parse_uuid(current_user["id"]), layout)
    return {"layout": row.layout}
