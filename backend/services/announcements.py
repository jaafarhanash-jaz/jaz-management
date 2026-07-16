from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.announcements as announcements_repo
import repositories.users as users_repo
import services.notifications as notifications_service
from services.admin import parse_uuid


def _iso(value):
    return value.isoformat() if value else None


def announcement_response(announcement) -> dict:
    return {
        "id": str(announcement.id),
        "company_id": str(announcement.company_id),
        "created_by": str(announcement.created_by),
        "created_by_name": announcement.created_by_name,
        "title": announcement.title,
        "message": announcement.message,
        "created_at": _iso(announcement.created_at),
    }


async def create_announcement(db: AsyncSession, current_user: dict, data) -> dict:
    """Broadcasts a company-wide announcement: one row, plus one real
    notification (category=announcements) per current employee via the
    existing publish() framework - no separate delivery mechanism."""
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")

    company_id = parse_uuid(current_user["company_id"])
    announcement = await announcements_repo.create(
        db,
        company_id=company_id,
        created_by=parse_uuid(current_user["id"]),
        created_by_name=current_user["name"],
        title=data.title,
        message=data.message,
    )
    await db.flush()

    employees = await users_repo.list_employees_by_company(db, company_id)
    for employee in employees:
        await notifications_service.publish(
            db,
            user_id=employee.id,
            company_id=company_id,
            category=notifications_service.CATEGORY_ANNOUNCEMENTS,
            type="announcement",
            title=announcement.title,
            message=announcement.message,
            entity_type="announcement",
            entity_id=announcement.id,
            action_url="/employee/announcements",
            sender_id=announcement.created_by,
            sender_name=announcement.created_by_name,
        )

    return announcement_response(announcement)


async def list_announcements(db: AsyncSession, current_user: dict) -> List[dict]:
    company_id = parse_uuid(current_user["company_id"])
    announcements = await announcements_repo.list_by_company(db, company_id)
    return [announcement_response(a) for a in announcements]
