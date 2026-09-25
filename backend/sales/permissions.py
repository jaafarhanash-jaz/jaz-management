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

# ---- Phase 2: leads and campaigns ----
# Action permissions say WHAT a caller may do; the three `scope_*` permissions say to WHICH leads (see
# services/lead_access.py). Effective scope = the union of the scopes a caller holds; an action permission with no
# scope permission reaches nothing. Like every other check in Sales these are permissions, never role names, so a
# custom role built from the same keys behaves the same way.
PERM_LEADS_VIEW = "sales.leads.view"
PERM_LEADS_CREATE = "sales.leads.create"
PERM_LEADS_UPDATE = "sales.leads.update"
PERM_LEADS_DELETE = "sales.leads.delete"                      # archive / restore
PERM_LEADS_CHANGE_STAGE = "sales.leads.change_stage"
PERM_LEADS_ASSIGN = "sales.leads.assign"                      # assign / reassign / unassign
PERM_LEADS_OVERRIDE_DUPLICATES = "sales.leads.override_duplicates"
PERM_LEADS_SCOPE_ALL = "sales.leads.scope_all"
PERM_LEADS_SCOPE_ASSIGNED = "sales.leads.scope_assigned"      # also: may RECEIVE assignments
PERM_LEADS_SCOPE_INTAKE = "sales.leads.scope_intake"
PERM_CAMPAIGNS_VIEW = "sales.campaigns.view"
PERM_CAMPAIGNS_MANAGE = "sales.campaigns.manage"

# ---- Phase 3: calls, follow-ups, demos, trials ----
# Same two layers as the lead keys: these say WHAT a caller may do with each kind of work item; the lead-scope
# permissions above say on WHICH leads (an item is visible and actionable exactly when its lead is - there is no
# second scope system). `view` reads them (and their events on a lead's timeline); `manage` creates and changes them.
# Giving a follow-up or a demo to somebody OTHER than yourself, or changing who has it, additionally needs
# sales.leads.assign (the same "choosing the owner is a separate privilege" rule as creating a lead).
PERM_CALLS_VIEW = "sales.calls.view"
PERM_CALLS_MANAGE = "sales.calls.manage"
PERM_FOLLOWUPS_VIEW = "sales.followups.view"
PERM_FOLLOWUPS_MANAGE = "sales.followups.manage"
PERM_DEMOS_VIEW = "sales.demos.view"
PERM_DEMOS_MANAGE = "sales.demos.manage"
PERM_TRIALS_VIEW = "sales.trials.view"
PERM_TRIALS_MANAGE = "sales.trials.manage"

# ---- Phase 4: customers and onboarding ----
# Customers follow the LEAD scope (a customer is visible exactly when the lead it came from is - there is no second
# scope system for them). Onboarding has its own row scope, because the people who run it are not salespeople and must
# not see leads: `scope_all` (every onboarding record) or `scope_assigned` (only the ones assigned to the caller,
# which also makes the caller assignable). Converting is a separate, narrower privilege than viewing; assigning an
# onboarding employee is separate from working the onboarding.
PERM_CUSTOMERS_VIEW = "sales.customers.view"
PERM_CUSTOMERS_CONVERT = "sales.customers.convert"
PERM_ONBOARDING_VIEW = "sales.onboarding.view"
PERM_ONBOARDING_MANAGE = "sales.onboarding.manage"           # change the stage, keep the notes
PERM_ONBOARDING_ASSIGN = "sales.onboarding.assign"           # assign / reassign the onboarding employee
PERM_ONBOARDING_SCOPE_ALL = "sales.onboarding.scope_all"
PERM_ONBOARDING_SCOPE_ASSIGNED = "sales.onboarding.scope_assigned"

# ---- Phase 5: dashboard and reports ----
# Aggregates obey the SAME scope as the raw records they count. These two keys only say whether the caller may open the
# dashboard / the reports at all; WHAT each section counts is decided by the permissions that guard the underlying records
# (sales.leads.view + the lead scope for lead metrics, sales.<kind>.view for calls / follow-ups / demos / trials,
# sales.campaigns.view for campaigns, sales.onboarding.view + the onboarding scope for onboarding). A section the caller may
# not see is simply absent from the response. "Team" numbers (per-employee comparisons, everyone's totals) need
# sales.leads.scope_all; anybody else gets their own numbers only. Lead Data Entry holds neither key: metrics are opt-in.
PERM_DASHBOARD_VIEW = "sales.dashboard.view"
PERM_REPORTS_VIEW = "sales.reports.view"

# key -> human description. Super Admin's implicit permission set.
ALL_PERMISSIONS: Dict[str, str] = {
    PERM_ACCESS: "Open the JAZ Sales workspace",
    PERM_TEAM_VIEW: "View the internal Sales team and role definitions",
    PERM_STAFF_CREATE: "Create internal staff accounts",
    PERM_STAFF_UPDATE: "Edit, deactivate and reset the password of internal staff accounts",
    PERM_STAFF_ASSIGN_ROLES: "Assign and revoke internal staff roles",
    PERM_LEADS_VIEW: "View leads within the caller's lead scope",
    PERM_LEADS_CREATE: "Create leads",
    PERM_LEADS_UPDATE: "Edit lead details within the caller's lead scope",
    PERM_LEADS_DELETE: "Archive and restore leads within the caller's lead scope",
    PERM_LEADS_CHANGE_STAGE: "Move leads through the sales pipeline, including marking them won or lost",
    PERM_LEADS_ASSIGN: "Assign, reassign and unassign leads within the caller's lead scope",
    PERM_LEADS_OVERRIDE_DUPLICATES: "Continue past an exact duplicate of an existing lead or JAZ company",
    PERM_LEADS_SCOPE_ALL: "Lead scope: every lead",
    PERM_LEADS_SCOPE_ASSIGNED: "Lead scope: leads assigned to the caller (also makes the caller assignable)",
    PERM_LEADS_SCOPE_INTAKE: "Lead scope: leads the caller created, and unassigned leads",
    PERM_CAMPAIGNS_VIEW: "View sales campaigns",
    PERM_CAMPAIGNS_MANAGE: "Create, edit and delete sales campaigns",
    PERM_CALLS_VIEW: "View calls logged on leads within the caller's lead scope",
    PERM_CALLS_MANAGE: "Log and edit calls on leads within the caller's lead scope",
    PERM_FOLLOWUPS_VIEW: "View follow-ups on leads within the caller's lead scope",
    PERM_FOLLOWUPS_MANAGE: "Create, edit, complete and cancel follow-ups on leads within the caller's lead scope",
    PERM_DEMOS_VIEW: "View demos on leads within the caller's lead scope",
    PERM_DEMOS_MANAGE: "Schedule, reschedule, complete, cancel and mark as no-show the demos of leads within the caller's lead scope",
    PERM_TRIALS_VIEW: "View Sales-side trials on leads within the caller's lead scope",
    PERM_TRIALS_MANAGE: "Start, edit, complete and cancel Sales-side trials on leads within the caller's lead scope",
    PERM_CUSTOMERS_VIEW: "View customers whose lead is within the caller's lead scope",
    PERM_CUSTOMERS_CONVERT: "Convert won leads within the caller's lead scope into a customer and a JAZ company",
    PERM_ONBOARDING_VIEW: "View onboarding records within the caller's onboarding scope",
    PERM_ONBOARDING_MANAGE: "Change the stage and keep the notes of onboarding records within the caller's onboarding scope",
    PERM_ONBOARDING_ASSIGN: "Assign and reassign the onboarding employee of an onboarding record",
    PERM_ONBOARDING_SCOPE_ALL: "Onboarding scope: every onboarding record",
    PERM_ONBOARDING_SCOPE_ASSIGNED: "Onboarding scope: onboarding records assigned to the caller (also makes the caller assignable)",
    PERM_DASHBOARD_VIEW: "Open the Sales dashboard (each section shows only what the caller's other permissions allow)",
    PERM_REPORTS_VIEW: "Open the Sales reports (each report also needs the permissions of the records it counts)",
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
    {"key": "leads", "permission": PERM_LEADS_VIEW},
    {"key": "pipeline", "permission": PERM_LEADS_VIEW},
    {"key": "campaigns", "permission": PERM_CAMPAIGNS_VIEW},
    {"key": "followups", "permission": PERM_FOLLOWUPS_VIEW},
    {"key": "calls", "permission": PERM_CALLS_VIEW},
    {"key": "demos", "permission": PERM_DEMOS_VIEW},
    {"key": "trials", "permission": PERM_TRIALS_VIEW},
    {"key": "reports", "permission": PERM_REPORTS_VIEW},
    {"key": "customers", "permission": PERM_CUSTOMERS_VIEW},
    {"key": "onboarding", "permission": PERM_ONBOARDING_VIEW},
]
