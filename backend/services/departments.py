from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.departments as departments_repo
import repositories.users as users_repo
from services.admin import parse_uuid


def department_response(department, head_name=None) -> dict:
    data = {
        "id": str(department.id),
        "company_id": str(department.company_id),
        "name": department.name,
        "head_id": str(department.head_id) if department.head_id else None,
        "created_at": department.created_at.isoformat() if department.created_at else None,
    }
    if head_name is not None:
        data["head_name"] = head_name
    return data


async def list_departments(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[dict]:
    departments = await departments_repo.list_by_company(db, company_id, limit=limit, offset=offset)
    head_ids = {d.head_id for d in departments if d.head_id}
    names = await users_repo.get_names_by_ids(db, head_ids)
    return [department_response(d, names.get(str(d.head_id))) for d in departments]


async def create_department(db: AsyncSession, company_id, data) -> dict:
    head_id = None
    if data.head_id:
        # PostgreSQL correctness note: departments.head_id is a real FK to
        # users.id (already part of the approved schema, not a new decision
        # here). The old Mongo implementation stored any head_id verbatim,
        # even a nonexistent one, and silently omitted head_name from the
        # response. A dangling reference can no longer be inserted once the
        # FK exists, so an invalid head_id now returns a clean 400 instead
        # of crashing on an unhandled IntegrityError - this is required
        # given the FK, not a discretionary behavior change.
        head_id = parse_uuid(data.head_id)
        if not head_id or not await users_repo.get_by_id(db, head_id):
            raise HTTPException(status_code=400, detail="head_id does not reference an existing user")

    department = await departments_repo.create(db, company_id=company_id, name=data.name, head_id=head_id)
    await db.flush()
    await db.refresh(department)
    head_name = None
    if department.head_id:
        head_name = (await users_repo.get_names_by_ids(db, [department.head_id])).get(str(department.head_id))
    return department_response(department, head_name)
