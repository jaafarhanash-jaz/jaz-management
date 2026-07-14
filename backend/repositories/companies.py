import uuid
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Company, Department, Task, User


async def get_by_id(db: AsyncSession, company_id) -> Optional[Company]:
    result = await db.execute(select(Company).where(Company.id == company_id, Company.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def list_all(db: AsyncSession) -> List[Company]:
    result = await db.execute(select(Company).where(Company.deleted_at.is_(None)))
    return list(result.scalars().all())


async def create(db: AsyncSession, **fields) -> Company:
    fields.setdefault("id", uuid.uuid4())
    company = Company(**fields)
    db.add(company)
    await db.flush()
    return company


async def count_by_plan(db: AsyncSession, plan_id) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Company)
        .where(Company.subscription_plan_id == plan_id, Company.deleted_at.is_(None))
    )
    return result.scalar_one()


async def detach_plan_from_deleted_companies(db: AsyncSession, plan_id) -> None:
    """Soft-deleted companies keep their row (and their FK to the plan),
    which would block plan deletion forever - something the old hard-delete
    world never did. Their plan pointer is meaningless (the company snapshot
    already carries the copied plan config), so clear it. Live companies are
    never touched: the delete-plan guard rejects while any exist."""
    await db.execute(
        update(Company)
        .where(Company.subscription_plan_id == plan_id, Company.deleted_at.is_not(None))
        .values(subscription_plan_id=None)
    )


async def soft_delete_cascade(db: AsyncSession, company_id, deleted_at) -> None:
    """Company deletion = one transaction stamping deleted_at on the company
    and every soft-deletable child (users, tasks, departments). Rows without
    their own deleted_at column (attendance, reports, messages, ...) are
    hidden implicitly at read time via their company_id join - see the
    migration plan's soft-delete section."""
    await db.execute(
        update(Company).where(Company.id == company_id, Company.deleted_at.is_(None)).values(deleted_at=deleted_at)
    )
    await db.execute(
        update(User).where(User.company_id == company_id, User.deleted_at.is_(None)).values(deleted_at=deleted_at)
    )
    await db.execute(
        update(Task).where(Task.company_id == company_id, Task.deleted_at.is_(None)).values(deleted_at=deleted_at)
    )
    await db.execute(
        update(Department)
        .where(Department.company_id == company_id, Department.deleted_at.is_(None))
        .values(deleted_at=deleted_at)
    )
