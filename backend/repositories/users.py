import uuid
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import Company, User, WorkSchedule


async def get_by_id(db: AsyncSession, user_id) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_by_id_with_company(db: AsyncSession, user_id) -> Tuple[Optional[User], Optional[Company]]:
    """Single round trip for what get_current_user needs on every request:
    the user row plus their company row (LEFT JOIN - company_id is NULL for
    super_admin). Replaces two sequential queries (get_by_id, then
    companies_repo.get_by_id inside enforce_company_access) with one -
    meaningful specifically because every network round trip to this
    database costs a fixed ~260ms (measured: VPS in Frankfurt, DB in
    Supabase's ap-southeast-1/Singapore), paid on every single authenticated
    request regardless of query complexity."""
    result = await db.execute(
        select(User, Company)
        .outerjoin(Company, User.company_id == Company.id)
        .where(User.id == user_id, User.deleted_at.is_(None))
    )
    row = result.first()
    if row is None:
        return None, None
    return row[0], row[1]


# Login lookups. Each is a plain single-column equality on LIVE rows (it matches the partial unique
# index of that column), and returns up to `limit` rows instead of asserting there is one, so the
# caller can tell "no such account" from "more than one" without an exception. Which column to
# search is the caller's decision - see services/identifiers.py.
async def find_live_by_email(db: AsyncSession, email: str, limit: int = 2) -> List[User]:
    result = await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)).limit(limit))
    return list(result.scalars().all())


async def find_live_by_phone(db: AsyncSession, phone: str, limit: int = 2) -> List[User]:
    result = await db.execute(select(User).where(User.phone == phone, User.deleted_at.is_(None)).limit(limit))
    return list(result.scalars().all())


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


async def list_owners_and_employees_by_company_ids(db: AsyncSession, company_ids) -> List[User]:
    """One query covering both halves of company presence (previously two:
    an employee-only GROUP BY aggregate, and a separate owner get_by_ids) -
    owner accounts have their own company_id set to the company they own
    (set once at creation, never reassigned - see create_company), so a
    plain company_id-scoped fetch already contains both roles. Aggregation
    and the owner lookup are done in the caller from this one result set."""
    if not company_ids:
        return []
    result = await db.execute(
        select(User).where(User.company_id.in_(list(company_ids)), User.deleted_at.is_(None))
    )
    return list(result.scalars().all())


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


async def list_employees_with_schedule_names(db: AsyncSession, company_id, limit: int = None, offset: int = None) -> List[tuple]:
    """(User, effective_schedule_name) - one query replacing the employees
    list endpoint's previous two (employees, then all of the company's
    work schedules, joined in Python). Two LEFT JOINs to the same table:
    the employee's own schedule if they have one, and separately the
    company's default schedule, COALESCEd together - the DB enforces at
    most one default per company (partial unique on (company_id,
    is_default), see WorkSchedule), so the second join can never multiply
    rows. This is exactly the old _effective_name fallback: own schedule's
    name if assigned, else the company default's name, else null."""
    own_schedule = aliased(WorkSchedule)
    default_schedule = aliased(WorkSchedule)
    query = (
        select(User, func.coalesce(own_schedule.name, default_schedule.name).label("schedule_name"))
        .outerjoin(own_schedule, User.schedule_id == own_schedule.id)
        .outerjoin(
            default_schedule,
            and_(default_schedule.company_id == company_id, default_schedule.is_default.is_(True)),
        )
        .where(User.company_id == company_id, User.role == "employee", User.deleted_at.is_(None))
        .order_by(User.created_at)
    )
    if limit is not None:
        query = query.limit(limit).offset(offset or 0)
    result = await db.execute(query)
    return result.all()


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


async def list_by_roles(db: AsyncSession, roles: List[str], company_ids: Optional[List[uuid.UUID]] = None) -> List[User]:
    """Generalizes list_by_role (any-of-roles) with an optional company
    scope - the one primitive services/company_notifications.py needs to
    resolve all of its recipient modes (platform-wide when company_ids is
    omitted, or scoped to specific companies for custom targeting).

    Only used by company_notifications.py (verified: no other caller) -
    joins Company and requires it to be in good standing, using exactly
    the predicate services/auth.py::enforce_company_access already treats
    as "active" elsewhere in the app (subscription_status == 'active',
    not the self-healing date check that function also does - a plain
    read here shouldn't have the side effect of mutating a company row).
    Both 'expired' and 'suspended' are excluded by that one equality
    check, since the CHECK constraint only allows one of the three values
    at a time. A soft-deleted company (deleted_at set) is excluded too.
    roles is always ['company_owner'] and/or ['employee'] here, and every
    row with either role always has a non-null company_id by construction
    (see Company.owner_id / employees_service.create_employee), so this
    is a plain inner join, not a left join - it can't accidentally drop a
    legitimately-matching row for either role."""
    query = (
        select(User)
        .join(Company, User.company_id == Company.id)
        .where(
            User.role.in_(roles),
            User.deleted_at.is_(None),
            Company.deleted_at.is_(None),
            Company.subscription_status == "active",
        )
    )
    if company_ids is not None:
        query = query.where(User.company_id.in_(company_ids))
    result = await db.execute(query)
    return list(result.scalars().all())
