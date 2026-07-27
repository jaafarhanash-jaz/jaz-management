import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User


async def get_by_id(db: AsyncSession, user_id) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_by_email_or_phone(db: AsyncSession, identifier: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(
            (User.email == identifier) | (User.phone == identifier),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> User:
    fields.setdefault("id", uuid.uuid4())
    user = User(**fields)
    db.add(user)
    await db.flush()
    return user


async def update_last_seen(db: AsyncSession, user_id, last_seen_at) -> None:
    user = await get_by_id(db, user_id)
    if user:
        user.last_seen_at = last_seen_at


async def get_names_by_ids(db: AsyncSession, user_ids) -> dict:
    """Batched id -> name lookup (one query, replaces the Mongo-era N+1
    per-creator find_one loop). Includes soft-deleted users on purpose:
    a display name on historical data should survive the user's deletion."""
    if not user_ids:
        return {}
    result = await db.execute(select(User.id, User.name).where(User.id.in_(list(user_ids))))
    return {str(row.id): row.name for row in result.all()}


async def get_by_ids(db: AsyncSession, user_ids) -> Dict[str, User]:
    if not user_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(list(user_ids))))
    return {str(u.id): u for u in result.scalars().all()}


async def count_employees(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == "employee", User.deleted_at.is_(None))
    )
    return result.scalar_one()


async def employee_stats_by_company(db: AsyncSession, company_ids, online_cutoff) -> dict:
    """One GROUP BY replacing the old per-company count_documents + per-user
    presence loop: employee count, employees currently online (last_seen_at
    within the presence window), and the most recent last_seen, per company."""
    if not company_ids:
        return {}
    result = await db.execute(
        select(
            User.company_id,
            func.count().label("employee_count"),
            func.count().filter(User.last_seen_at >= online_cutoff).label("employees_online"),
            func.max(User.last_seen_at).label("max_last_seen"),
        )
        .where(
            User.company_id.in_(list(company_ids)),
            User.role == "employee",
            User.deleted_at.is_(None),
        )
        .group_by(User.company_id)
    )
    return {
        str(row.company_id): {
            "employee_count": row.employee_count,
            "employees_online": row.employees_online,
            "max_last_seen": row.max_last_seen,
        }
        for row in result.all()
    }


async def email_taken(db: AsyncSession, email: str, exclude_id=None) -> bool:
    query = select(func.count()).select_from(User).where(User.email == email, User.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    result = await db.execute(query)
    return result.scalar_one() > 0


async def phone_taken(db: AsyncSession, phone: str, exclude_id=None) -> bool:
    query = select(func.count()).select_from(User).where(User.phone == phone, User.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    result = await db.execute(query)
    return result.scalar_one() > 0


async def list_employees_by_company(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[User]:
    query = select(User).where(User.company_id == company_id, User.role == "employee", User.deleted_at.is_(None))
    query = query.order_by(User.created_at)
    if limit is not None:
        query = query.limit(limit).offset(offset or 0)
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_employees_by_company(db: AsyncSession, company_id) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(
            User.company_id == company_id, User.role == "employee", User.deleted_at.is_(None)
        )
    )
    return result.scalar_one()


async def get_employee_in_company(db: AsyncSession, employee_id, company_id) -> Optional[User]:
    result = await db.execute(
        select(User).where(
            User.id == employee_id, User.company_id == company_id,
            User.role == "employee", User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_employees_by_company_and_department(db: AsyncSession, company_id, department: str) -> List[User]:
    result = await db.execute(
        select(User).where(
            User.company_id == company_id, User.role == "employee",
            User.department == department, User.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def list_employees_by_company_and_departments(db: AsyncSession, company_id, departments) -> List[User]:
    if not departments:
        return []
    result = await db.execute(
        select(User).where(
            User.company_id == company_id, User.role == "employee",
            User.department.in_(list(departments)), User.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def count_matching_ids_in_company(db: AsyncSession, ids, company_id) -> int:
    """No role filter, matching the old Mongo membership check exactly -
    any user (owner or employee) belonging to the company counts."""
    if not ids:
        return 0
    result = await db.execute(
        select(func.count()).select_from(User).where(User.id.in_(list(ids)), User.company_id == company_id)
    )
    return result.scalar_one()


async def list_by_role(db: AsyncSession, role: str) -> List[User]:
    result = await db.execute(select(User).where(User.role == role, User.deleted_at.is_(None)))
    return list(result.scalars().all())
