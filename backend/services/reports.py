from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

import repositories.reports as reports_repo
import repositories.users as users_repo
from services.admin import parse_uuid


def _iso(value):
    return value.isoformat() if value else None


def report_response(report, *, employee_name=None) -> dict:
    return {
        "id": str(report.id),
        "employee_id": str(report.employee_id),
        "employee_name": employee_name,
        "company_id": str(report.company_id),
        "title": report.title,
        "description": report.description,
        "files": list(report.files or []),
        "images": list(report.images or []),
        "status": report.status,
        "created_at": _iso(report.created_at),
    }


async def list_reports_for_owner(db: AsyncSession, company_id) -> List[dict]:
    reports = await reports_repo.list_by_company(db, parse_uuid(company_id))
    employee_ids = {r.employee_id for r in reports}
    names = await users_repo.get_names_by_ids(db, employee_ids)
    return [report_response(r, employee_name=names.get(str(r.employee_id))) for r in reports]


async def create_report(db: AsyncSession, current_user: dict, data) -> dict:
    report = await reports_repo.create(
        db,
        employee_id=parse_uuid(current_user["id"]),
        employee_name=current_user["name"],
        company_id=parse_uuid(current_user["company_id"]),
        title=data.title,
        description=data.description,
        files=data.files,
        images=data.images,
        status="pending",
    )
    await db.flush()
    await db.refresh(report)
    return report_response(report, employee_name=current_user["name"])
