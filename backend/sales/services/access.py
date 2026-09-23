"""Who is this caller, and what may they do in JAZ Sales?

Decision table (server-side, authoritative - the frontend only mirrors it):

  users.role       | result
  -----------------+---------------------------------------------------------
  super_admin      | every permission in the catalog (implicit)
  jaz_staff        | UNION of the permissions of their ACTIVE assigned roles;
                   | denied outright (403) if the account is not status=active.
                   | With zero roles they are authenticated but hold nothing.
  company_owner /  |
  employee / other | 403 "Access denied" - always.
"""
import uuid
from dataclasses import dataclass
from typing import Tuple

from fastapi import HTTPException

from sales.permissions import ALL_PERMISSIONS, MODULES, PLATFORM_ROLE_STAFF, PLATFORM_ROLE_SUPER_ADMIN
from sales.repositories import staff as staff_repo


# The only user fields a StaffContext carries: exactly what GET /sales/me returns. An ALLOWLIST, not
# "everything except password": the core user dict (services/auth.py::user_to_dict) also holds the
# bcrypt hash, and a field added to it later must not silently flow into every Sales request - or into
# any log line, traceback or test diff that prints a context (a dataclass repr includes its fields).
_CONTEXT_USER_FIELDS = ("id", "name", "email", "phone", "role", "status")


def _context_user(user: dict) -> dict:
    return {key: user.get(key) for key in _CONTEXT_USER_FIELDS}


@dataclass(frozen=True)
class StaffContext:
    user: dict  # allowlisted fields only (_CONTEXT_USER_FIELDS) - never the core user dict
    user_id: uuid.UUID
    is_super_admin: bool
    roles: Tuple[dict, ...]
    permissions: frozenset

    def has(self, *keys: str) -> bool:
        return all(key in self.permissions for key in keys)

    def visible_modules(self) -> list:
        return [m["key"] for m in MODULES if m["permission"] in self.permissions]


def _deny() -> HTTPException:
    # Same shape as every other role-denied response in the core API.
    return HTTPException(status_code=403, detail="Access denied")


def _role_ref(role) -> dict:
    return {"key": role.key, "name_en": role.name_en, "name_ar": role.name_ar}


async def resolve_staff_context(db, user: dict) -> StaffContext:
    platform_role = user.get("role")
    user_id = uuid.UUID(user["id"])

    if platform_role == PLATFORM_ROLE_SUPER_ADMIN:
        return StaffContext(_context_user(user), user_id, True, tuple(), frozenset(ALL_PERMISSIONS))

    if platform_role == PLATFORM_ROLE_STAFF:
        # Core auth does not enforce users.status, so Sales does: a deactivated
        # staff account is denied here on every request, immediately - even while
        # an already-issued access token is still within its lifetime.
        if user.get("status") != "active":
            raise _deny()
        roles, permissions = await staff_repo.get_permission_profile(db, user_id)
        return StaffContext(
            _context_user(user), user_id, False, tuple(_role_ref(r) for r in roles), frozenset(permissions)
        )

    raise _deny()
