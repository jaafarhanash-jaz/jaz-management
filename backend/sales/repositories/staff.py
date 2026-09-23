import uuid
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.models import StaffRole, StaffRolePermission, StaffUserRole
from sales.permissions import PLATFORM_ROLE_STAFF


# ---- staff users -----------------------------------------------------------
# Every lookup below is pinned to role == 'jaz_staff'. That pin is what stops the
# team-management API from ever touching a Company Owner, Employee or Super Admin
# account, even if a caller passes their id.

async def get_staff_user(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == PLATFORM_ROLE_STAFF, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def list_staff_users(db: AsyncSession, limit: int, offset: int) -> Tuple[List[User], int]:
    base = (User.role == PLATFORM_ROLE_STAFF) & (User.deleted_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(User).where(base))).scalar_one()
    result = await db.execute(select(User).where(base).order_by(User.created_at, User.id).limit(limit).offset(offset))
    return list(result.scalars().all()), total


# ---- roles / permissions ---------------------------------------------------

async def get_role_by_key(db: AsyncSession, key: str) -> Optional[StaffRole]:
    result = await db.execute(select(StaffRole).where(StaffRole.key == key))
    return result.scalar_one_or_none()


async def get_active_roles_by_keys(db: AsyncSession, keys: Iterable[str]) -> Dict[str, StaffRole]:
    keys = list(keys)
    if not keys:
        return {}
    result = await db.execute(select(StaffRole).where(StaffRole.key.in_(keys), StaffRole.is_active.is_(True)))
    return {r.key: r for r in result.scalars().all()}


async def list_active_roles_with_permissions(db: AsyncSession) -> List[Tuple[StaffRole, List[str]]]:
    result = await db.execute(
        select(StaffRole, StaffRolePermission.permission_key)
        .outerjoin(StaffRolePermission, StaffRolePermission.role_id == StaffRole.id)
        .where(StaffRole.is_active.is_(True))
        .order_by(StaffRole.name_en, StaffRolePermission.permission_key)
    )
    roles: Dict[uuid.UUID, Tuple[StaffRole, List[str]]] = {}
    for role, permission_key in result.all():
        entry = roles.setdefault(role.id, (role, []))
        if permission_key is not None:
            entry[1].append(permission_key)
    return list(roles.values())


async def get_permission_profile(db: AsyncSession, user_id: uuid.UUID) -> Tuple[List[StaffRole], Set[str]]:
    """The user's ACTIVE roles and the UNION of their permissions - one query.
    Revoked grants and retired (is_active=False) roles contribute nothing."""
    result = await db.execute(
        select(StaffRole, StaffRolePermission.permission_key)
        .select_from(StaffUserRole)
        .join(StaffRole, and_(StaffRole.id == StaffUserRole.role_id, StaffRole.is_active.is_(True)))
        .outerjoin(StaffRolePermission, StaffRolePermission.role_id == StaffRole.id)
        .where(StaffUserRole.user_id == user_id, StaffUserRole.revoked_at.is_(None))
        .order_by(StaffRole.name_en)
    )
    roles: Dict[uuid.UUID, StaffRole] = {}
    permissions: Set[str] = set()
    for role, permission_key in result.all():
        roles[role.id] = role
        if permission_key is not None:
            permissions.add(permission_key)
    return list(roles.values()), permissions


async def active_roles_for_users(db: AsyncSession, user_ids: List[uuid.UUID]) -> Dict[uuid.UUID, List[StaffRole]]:
    """Batched (no per-user queries) - used by the team listing."""
    if not user_ids:
        return {}
    result = await db.execute(
        select(StaffUserRole.user_id, StaffRole)
        .join(StaffRole, and_(StaffRole.id == StaffUserRole.role_id, StaffRole.is_active.is_(True)))
        .where(StaffUserRole.user_id.in_(user_ids), StaffUserRole.revoked_at.is_(None))
        .order_by(StaffRole.name_en)
    )
    out: Dict[uuid.UUID, List[StaffRole]] = {}
    for user_id, role in result.all():
        out.setdefault(user_id, []).append(role)
    return out


# ---- grants ----------------------------------------------------------------

async def grant_role_id(
    db: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, granted_by: uuid.UUID
) -> Optional[uuid.UUID]:
    """Idempotent and race-safe: the partial unique index makes a duplicate
    ACTIVE grant impossible, and ON CONFLICT DO NOTHING turns the losing side of
    a concurrent grant into a clean no-op. Returns the id of the grant row that
    was inserted, or None when nothing changed (already actively granted) - the
    audit trail records the former and stays silent on the latter."""
    stmt = (
        pg_insert(StaffUserRole)
        .values(id=uuid.uuid4(), user_id=user_id, role_id=role_id, granted_by=granted_by)
        .on_conflict_do_nothing(index_elements=["user_id", "role_id"], index_where=text("revoked_at IS NULL"))
        .returning(StaffUserRole.id)
    )
    row = (await db.execute(stmt)).first()
    return row[0] if row is not None else None


async def revoke_role_id(
    db: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, revoked_by: uuid.UUID
) -> Optional[uuid.UUID]:
    """Idempotent: a single UPDATE ... WHERE revoked_at IS NULL, so repeating a
    revoke (or racing two of them) changes nothing the second time. The row is
    kept - history is never deleted. clock_timestamp() (not now()) so the CHECK
    revoked_at >= granted_at holds even when this transaction started before a
    concurrent one that committed the grant. Returns the id of the grant row
    that was revoked, or None when there was no active grant to revoke."""
    stmt = (
        update(StaffUserRole)
        .where(StaffUserRole.user_id == user_id, StaffUserRole.role_id == role_id, StaffUserRole.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp(), revoked_by=revoked_by)
        .returning(StaffUserRole.id)
    )
    row = (await db.execute(stmt)).first()
    return row[0] if row is not None else None


async def grant_role(db: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, granted_by: uuid.UUID) -> bool:
    """True only if a grant row was actually inserted (see grant_role_id)."""
    return (await grant_role_id(db, user_id, role_id, granted_by)) is not None


async def revoke_role(db: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, revoked_by: uuid.UUID) -> bool:
    """True only if an active grant was actually revoked (see revoke_role_id)."""
    return (await revoke_role_id(db, user_id, role_id, revoked_by)) is not None
