"""Permission catalog and navigation registry for JAZ Sales.

Permission keys are namespaced `sales.<area>.<action>`. Later phases append
their keys here (and add them to the DB catalog in their own migration).

Effective permissions for a staff user = the UNION of the permissions of every
ACTIVE role currently assigned to them (see services/access.py). Super Admin
implicitly holds every key in ALL_PERMISSIONS. The platform role `jaz_staff`
by itself grants nothing.
"""
from typing import Dict, List

MODULE = "sales"

# Platform (users.role) values that may reach the Sales API at all. This is the
# outer gate; what a staff user can actually DO is decided by permissions.
PLATFORM_ROLE_STAFF = "jaz_staff"
PLATFORM_ROLE_SUPER_ADMIN = "super_admin"

PERM_ACCESS = "sales.access"
PERM_TEAM_VIEW = "sales.team.view"
PERM_STAFF_CREATE = "sales.staff.create"
PERM_STAFF_UPDATE = "sales.staff.update"
PERM_STAFF_ASSIGN_ROLES = "sales.staff.assign_roles"

# key -> human description. Super Admin's implicit permission set.
ALL_PERMISSIONS: Dict[str, str] = {
    PERM_ACCESS: "Open the JAZ Sales workspace",
    PERM_TEAM_VIEW: "View the internal Sales team and role definitions",
    PERM_STAFF_CREATE: "Create internal staff accounts",
    PERM_STAFF_UPDATE: "Edit, deactivate and reset the password of internal staff accounts",
    PERM_STAFF_ASSIGN_ROLES: "Assign and revoke internal staff roles",
}

# The four seeded system roles (keys only; definitions/grants live in the DB).
SYSTEM_ROLE_KEYS = ("sales_manager", "sales_employee", "lead_data_entry", "onboarding_employee")

# Server-side source of truth for which workspace sections a user may see.
# GET /api/sales/me returns only the modules whose permission the user holds;
# the frontend maps `key` -> route/label. (UI filtering is convenience only -
# every endpoint enforces its own permission regardless.)
MODULES: List[Dict[str, str]] = [
    {"key": "home", "permission": PERM_ACCESS},
    {"key": "team", "permission": PERM_TEAM_VIEW},
]
