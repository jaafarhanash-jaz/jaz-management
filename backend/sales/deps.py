"""FastAPI dependencies that enforce JAZ Sales access on the server.

Frontend hiding is NOT security: every Sales endpoint must depend on
`require_permission(...)` (or, for /me, `get_staff_context`). The router itself
also carries `get_staff_context` as a router-level dependency, so an endpoint
that forgets its permission guard is still closed to Company Owners, Employees
and every other non-staff caller. tests/test_sales_route_sweep.py asserts both.
"""
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import services.auth as auth_service
from database import get_db
from sales.services import access as access_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext, new_audit_context

# Same scheme/behavior as server.py's own `security = HTTPBearer()`.
_bearer = HTTPBearer()


async def _current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Identical to server.py::get_current_user (kept local to avoid importing
    # server.py, which mounts this package). Core authentication is unchanged.
    return await auth_service.get_current_user(db, credentials.credentials)


async def get_staff_context(
    user: dict = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
) -> StaffContext:
    """Outer gate: only an ACTIVE `jaz_staff` or a `super_admin` passes.
    Everyone else gets 403. Grants no business permission by itself."""
    return await access_service.resolve_staff_context(db, user)


async def get_audit_context(request: Request) -> AuditContext:
    """One per request (FastAPI caches a dependency per request): the correlation id shared by every
    audit event the request emits, plus the caller's user agent. No client IP on purpose - see
    sales/services/audit.py."""
    return new_audit_context(request.headers.get("user-agent"))


# Structural markers read by tests/test_sales_route_sweep.py.
get_staff_context.__sales_guard__ = "staff"


def require_permission(*keys: str):
    """Dependency factory: the caller must hold EVERY listed permission
    (Super Admin holds all of them implicitly)."""
    if not keys:
        raise ValueError("require_permission needs at least one permission key")

    async def _guard(ctx: StaffContext = Depends(get_staff_context)) -> StaffContext:
        if not ctx.has(*keys):
            raise HTTPException(status_code=403, detail="Access denied")
        return ctx

    _guard.__sales_guard__ = "permission"
    _guard.__sales_permissions__ = tuple(keys)
    return _guard
