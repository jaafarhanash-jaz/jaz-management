"""JAZ Sales API (Phase 1: workspace context, roles, internal staff management).

Mounted by server.py under /api/sales. Access rules:
  * router-level dependency get_staff_context: only an ACTIVE `jaz_staff` or a
    `super_admin` gets past the door - Company Owners/Employees/anyone else: 403;
  * each endpoint additionally declares the permission it needs via
    require_permission(...) - Super Admin holds all of them implicitly, a staff
    user only what their active roles grant.
Adding an endpoint without a guard fails tests/test_sales_route_sweep.py.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from sales import permissions as perms
from sales.deps import get_audit_context, get_staff_context, require_permission
from sales.schemas import (
    MeOut,
    PasswordReset,
    RoleAssignment,
    RoleChangeResult,
    RoleDefinition,
    StaffCreate,
    StaffList,
    StaffOut,
    StaffUpdate,
)
from sales.services import team as team_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext

sales_router = APIRouter(prefix="/api/sales", tags=["sales"], dependencies=[Depends(get_staff_context)])


# ---- workspace context -----------------------------------------------------

@sales_router.get("/me", response_model=MeOut)
async def get_me(ctx: StaffContext = Depends(get_staff_context)):
    """Who am I in JAZ Sales: roles, effective permissions and the workspace
    sections I may see. Needs only staff status - a staff user with no roles
    gets a valid response with empty roles/permissions (the UI shows a clean
    'no role assigned' state)."""
    user = ctx.user
    return {
        "user": {k: user[k] for k in ("id", "name", "email", "phone", "role", "status")},
        "is_super_admin": ctx.is_super_admin,
        "roles": list(ctx.roles),
        "permissions": sorted(ctx.permissions),
        "modules": ctx.visible_modules(),
    }


@sales_router.get("/roles", response_model=list[RoleDefinition])
async def list_roles(
    ctx: StaffContext = Depends(require_permission(perms.PERM_TEAM_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await team_service.list_roles(db)


# ---- team (internal staff) -------------------------------------------------

@sales_router.get("/team", response_model=StaffList)
async def list_team(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_TEAM_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await team_service.list_team(db, limit, offset)


@sales_router.post("/team", response_model=StaffOut, status_code=201)
async def create_staff(
    body: StaffCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_CREATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    # Creating an account and choosing its roles are separate privileges.
    if body.role_keys and not ctx.has(perms.PERM_STAFF_ASSIGN_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    return await team_service.create_staff(db, ctx, body, audit)


@sales_router.patch("/team/{user_id}", response_model=StaffOut)
async def update_staff(
    user_id: str,
    body: StaffUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_UPDATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.update_staff(db, ctx, user_id, body, audit)


@sales_router.post("/team/{user_id}/reset-password")
async def reset_staff_password(
    user_id: str,
    body: PasswordReset,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_UPDATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.reset_password(db, ctx, user_id, body.new_password, audit)


@sales_router.post("/team/{user_id}/roles", response_model=RoleChangeResult)
async def assign_staff_role(
    user_id: str,
    body: RoleAssignment,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_ASSIGN_ROLES)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.assign_role(db, ctx, user_id, body.role_key, audit)


@sales_router.delete("/team/{user_id}/roles/{role_key}", response_model=RoleChangeResult)
async def revoke_staff_role(
    user_id: str,
    role_key: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_ASSIGN_ROLES)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.revoke_role(db, ctx, user_id, role_key, audit)
