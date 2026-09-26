"""Shared helpers for the JAZ Sales test tiers.

These tests CREATE internal staff accounts and role grants, and the Phase-1
migration's downgrade deliberately refuses to run while any jaz_staff user
exists - so they must never touch a real database. `assert_scratch_target()`
is a hard guard: the suite refuses to run unless the HTTP backend is a local
instance on a port other than the dev backend's :8000 AND DATABASE_URL names the
disposable `jaz_sales_scratch` database.
"""
import os
import random
import re
import time
import uuid

import pytest
import requests

SCRATCH_DB_NAME = "jaz_sales_scratch"
STAFF_PASSWORD = "Staff#12345"

# The approved permission catalog and system-role grants: Phase 1 (bb595f6d8dae) + Phase 2 (d764d5f44e91) + Phase 3
# (e3a9c5b07d12) + Phase 4 (f4c8a1d92b63) + Phase 5 (b6d1f3a8c294) + the simplified workflow (a3f8c2d7e915). The tests that pin
# "exactly the approved grants" import these, so a new phase updates ONE place - and any drift between the code catalog
# (sales/permissions.py), the migrations and this list is caught.
_PHASE1_PERMISSIONS = {"sales.access", "sales.team.view", "sales.staff.create", "sales.staff.update", "sales.staff.assign_roles"}
_PHASE2_PERMISSIONS = {
    "sales.leads.view", "sales.leads.create", "sales.leads.update", "sales.leads.delete", "sales.leads.change_stage",
    "sales.leads.assign", "sales.leads.override_duplicates", "sales.leads.scope_all", "sales.leads.scope_assigned",
    "sales.leads.scope_intake", "sales.campaigns.view", "sales.campaigns.manage",
}
# Phase 3: calls, follow-ups, demos, trials. Held by Sales Manager and Sales Employee ONLY - Lead Data Entry and
# Onboarding Employee get none of them.
_PHASE3_PERMISSIONS = {
    "sales.calls.view", "sales.calls.manage", "sales.followups.view", "sales.followups.manage",
    "sales.demos.view", "sales.demos.manage", "sales.trials.view", "sales.trials.manage",
}
# Phase 4: customers and onboarding. Sales Manager: convert + view customers, run onboarding everywhere. Sales Employee: view
# the customers of their own leads, nothing else. Onboarding Employee: work the onboarding records assigned to them.
# Lead Data Entry gets none of them.
_PHASE4_PERMISSIONS = {
    "sales.customers.view", "sales.customers.convert", "sales.onboarding.view", "sales.onboarding.manage",
    "sales.onboarding.assign", "sales.onboarding.scope_all", "sales.onboarding.scope_assigned",
}
_PHASE4_MANAGER = {
    "sales.customers.view", "sales.customers.convert", "sales.onboarding.view", "sales.onboarding.manage",
    "sales.onboarding.assign", "sales.onboarding.scope_all",
}
_PHASE4_EMPLOYEE = {"sales.customers.view"}
_PHASE4_ONBOARDING = {"sales.onboarding.view", "sales.onboarding.manage", "sales.onboarding.scope_assigned"}
# Phase 5: dashboard and reports. Sales Manager, Sales Employee and Onboarding Employee hold both; Lead Data Entry holds neither
# (metrics are opt-in). What a section shows is still limited by the permissions of the records it counts.
_PHASE5_PERMISSIONS = {"sales.dashboard.view", "sales.reports.view"}
# Simplified workflow: the Sales Manager's settings (automatic distribution) and the lead export - Sales Manager only. The same
# migration grants sales.customers.convert to the Sales Employee (Customer Setup) and sales.leads.delete to Lead Data Entry
# (archive / restore of their own latest leads - the window is enforced in code).
_WORKFLOW_PERMISSIONS = {"sales.settings.manage", "sales.leads.export", "sales.customers.setup"}   # setup: e7a2d4c9b1f3
# Batches (f2b6d8a1c4e9): the Sales Manager's view of the Data / Master / Work batches, the reassignment of a completed Work Batch and
# the employee performance view. Sales Manager ONLY - Sales Employee, Lead Data Entry and the retired Onboarding role get none.
_BATCH_PERMISSIONS = {"sales.batches.view", "sales.batches.reassign", "sales.performance.view"}
ALL_PERMISSION_KEYS = (
    _PHASE1_PERMISSIONS | _PHASE2_PERMISSIONS | _PHASE3_PERMISSIONS | _PHASE4_PERMISSIONS | _PHASE5_PERMISSIONS | _WORKFLOW_PERMISSIONS
    | _BATCH_PERMISSIONS
)
SYSTEM_ROLE_PERMISSIONS = {
    "sales_manager": {
        "sales.access", "sales.team.view",
        "sales.leads.view", "sales.leads.create", "sales.leads.update", "sales.leads.delete", "sales.leads.change_stage",
        "sales.leads.assign", "sales.leads.override_duplicates", "sales.leads.scope_all",
        "sales.campaigns.view", "sales.campaigns.manage",
    } | _PHASE3_PERMISSIONS | _PHASE4_MANAGER | _PHASE5_PERMISSIONS | _WORKFLOW_PERMISSIONS | _BATCH_PERMISSIONS,   # (+ setup, batches)
    "sales_employee": {
        "sales.access", "sales.leads.view", "sales.leads.update", "sales.leads.change_stage",
        "sales.leads.scope_assigned", "sales.campaigns.view",
    } | _PHASE3_PERMISSIONS | _PHASE4_EMPLOYEE | _PHASE5_PERMISSIONS | {"sales.customers.setup"},   # setup - never convert
    "lead_data_entry": {
        "sales.access", "sales.leads.view", "sales.leads.create", "sales.leads.update", "sales.leads.delete",
        "sales.leads.scope_intake", "sales.campaigns.view",
    },
    "onboarding_employee": {"sales.access"} | _PHASE4_ONBOARDING | _PHASE5_PERMISSIONS,
}
# The grants the simplified workflow (a3f8c2d7e915) added to keys that EARLIER migrations created - so each earlier migration's
# frozen seed is compared with what the role held before them.
WORKFLOW_GRANTS = {
    ("sales_manager", "sales.settings.manage"), ("sales_manager", "sales.leads.export"), ("lead_data_entry", "sales.leads.delete"),
    ("sales_manager", "sales.customers.setup"), ("sales_employee", "sales.customers.setup"),       # e7a2d4c9b1f3
}
WORKFLOW_PERMISSIONS = frozenset(_WORKFLOW_PERMISSIONS)
BATCH_PERMISSIONS = frozenset(_BATCH_PERMISSIONS)
BATCH_GRANTS = {("sales_manager", key) for key in _BATCH_PERMISSIONS}       # f2b6d8a1c4e9 - new keys, granted to the manager only


def granted_before_workflow(role: str) -> set:
    """What a system role held before the simplified workflow's migration (so before the batches' one too)."""
    return SYSTEM_ROLE_PERMISSIONS[role] - {key for r, key in WORKFLOW_GRANTS | BATCH_GRANTS if r == role}


# Retired system roles (staff_roles.is_active = false, migration c5e1b9a4d2f7): their GRANTS stay in the database (the pins
# above), but they give their holders NOTHING. The Onboarding Employee is not part of the simplified workflow.
RETIRED_ROLES = frozenset({"onboarding_employee"})
# what a holder of each system role actually gets (GET /sales/me): nothing at all from a retired role
EFFECTIVE_ROLE_PERMISSIONS = {role: (set() if role in RETIRED_ROLES else keys) for role, keys in SYSTEM_ROLE_PERMISSIONS.items()}
# The dormant onboarding architecture is exercised through a custom role holding exactly the retired role's keys - "a custom role
# built from the same keys behaves the same way" - which is what re-enabling the role would give back.
ONBOARDING_WORKER_KEYS = frozenset(SYSTEM_ROLE_PERMISSIONS["onboarding_employee"])

# what GET /sales/me lists as `modules` for each system role (server order: sales/permissions.py::MODULES). Onboarding is no
# longer a section (simplified workflow), and the retired Onboarding Employee role reaches nothing.
SYSTEM_ROLE_MODULES = {
    "sales_manager": ["home", "team", "leads", "pipeline", "campaigns", "followups", "calls", "demos", "trials", "reports", "customers",
                      "batches", "performance"],
    "sales_employee": ["home", "leads", "pipeline", "campaigns", "followups", "calls", "demos", "trials", "reports", "customers"],
    "lead_data_entry": ["home", "leads", "pipeline", "campaigns"],
    "onboarding_employee": [],
}
ALL_MODULES = ["home", "team", "leads", "pipeline", "campaigns", "followups", "calls", "demos", "trials", "reports", "customers",
               "batches", "performance"]

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def assert_scratch_target(need_http: bool = True) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    db_name = db_url.rsplit("/", 1)[-1].split("?")[0]
    if db_name != SCRATCH_DB_NAME:
        raise RuntimeError(
            f"JAZ Sales tests refuse to run: DATABASE_URL targets '{db_name or '<unset>'}', "
            f"not '{SCRATCH_DB_NAME}'. Export the scratch DATABASE_URL first."
        )
    host = db_url.split("@", 1)[-1].split("/", 1)[0]
    if not (host.startswith("127.0.0.1:") or host.startswith("localhost:")):
        raise RuntimeError(f"JAZ Sales tests refuse to run: DB host '{host}' is not local.")
    if need_http:
        m = re.match(r"^https?://(127\.0\.0\.1|localhost):(\d+)$", BASE_URL)
        if not m:
            raise RuntimeError(f"JAZ Sales tests refuse to run: backend URL '{BASE_URL or '<unset>'}' is not a local instance.")
        if m.group(2) == "8000":
            raise RuntimeError("JAZ Sales tests refuse to run against :8000 (the real dev backend).")


def unique_email(label: str) -> str:
    return f"sales-{label}-{uuid.uuid4().hex[:10]}@example.com"


def unique_phone() -> str:
    # 9 random digits (10^8 - 10^9 possibilities): the scratch DB accumulates thousands of staff across runs, and a 7-digit
    # draw collided with an existing account often enough to fail a test now and then ("Phone number already registered").
    return f"+1555{random.randint(100000000, 999999999)}"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(email_or_phone: str, password: str, expect: int = 200):
    """POST /api/auth/login. The login route is rate-limited (60/min/IP) and the
    whole suite shares one IP, so a 429 is retried after a pause instead of
    failing the test for a reason unrelated to what is being tested."""
    last = None
    for attempt in range(4):
        last = requests.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": email_or_phone, "password": password}, timeout=30)
        if last.status_code != 429:
            break
        time.sleep(20)
    assert last.status_code == expect, f"login {email_or_phone}: expected {expect}, got {last.status_code} {last.text[:200]}"
    return last.json() if expect == 200 else last


def mint_token(user_id) -> str:
    """Access token signed with the app's own signer (services.auth.create_access_token) - the exact
    token /auth/login would issue, minus the HTTP round trip. Used for personas so the suite doesn't
    burn the shared 60/min login rate limit; tests that exercise login/refresh behaviour itself use
    real logins. get_current_user trusts only `sub` and re-reads the user (role, status) from the DB."""
    from services.auth import create_access_token
    return create_access_token({"sub": str(user_id)})


def lookup_user(email: str) -> dict:
    """Read an existing (seeded) user straight from the scratch DB."""
    import asyncio
    from sqlalchemy import select
    from database import SessionLocal, engine
    from models import User

    async def _go():
        try:
            async with SessionLocal() as db:
                u = (await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))).scalar_one()
                return {"id": str(u.id), "email": u.email, "phone": u.phone, "role": u.role}
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def create_staff(admin_headers: dict, role_keys, label: str = "staff", do_login: bool = True, real_login: bool = False) -> dict:
    """Create a jaz_staff account through the real API (as Super Admin).

    do_login=False  -> account only (no token).
    default         -> plus a minted access token (no HTTP login; see mint_token).
    real_login=True -> a real POST /auth/login, for tests that need the refresh token or the login response."""
    payload = {
        "name": f"Test {label}",
        "email": unique_email(label),
        "phone": unique_phone(),
        "password": STAFF_PASSWORD,
        "role_keys": list(role_keys),
    }
    r = requests.post(f"{BASE_URL}/api/sales/team", json=payload, headers=admin_headers, timeout=30)
    assert r.status_code == 201, f"create staff failed: {r.status_code} {r.text}"
    staff = r.json()
    out = {"id": staff["id"], "email": payload["email"], "phone": payload["phone"], "password": STAFF_PASSWORD}
    if not do_login:
        return out
    if real_login:
        session = login(payload["email"], STAFF_PASSWORD)
        out.update(token=session["token"], refresh_token=session["refresh_token"], headers=auth(session["token"]), login=session)
    else:
        token = mint_token(staff["id"])
        out.update(token=token, refresh_token=None, headers=auth(token), login=None)
    return out


def grant_retired_role(staff_id: str, role_key: str = "onboarding_employee") -> None:
    """Give an account a grant of a RETIRED system role, as if it had been granted before the role was retired (the API
    refuses to grant a retired role, so this is a direct write - scratch only). Models a real Onboarding Employee after
    migration c5e1b9a4d2f7."""
    import asyncio
    from sqlalchemy import select
    from database import SessionLocal, engine
    from sales.models import StaffRole, StaffUserRole

    async def _go():
        try:
            async with SessionLocal() as db:
                role = (await db.execute(select(StaffRole).where(StaffRole.key == role_key))).scalar_one()
                assert role.is_active is False, f"{role_key} is not retired"
                db.add(StaffUserRole(id=uuid.uuid4(), user_id=uuid.UUID(staff_id), role_id=role.id, granted_by=uuid.UUID(staff_id)))
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_go())


def create_retired_onboarding_employee(admin_headers: dict, label: str = "onboarding") -> dict:
    """An account holding the RETIRED Onboarding Employee role (created without roles, then the old grant written directly)."""
    person = create_staff(admin_headers, [], label)
    grant_retired_role(person["id"])
    return person


def api(method: str, path: str, headers=None, json=None, **kw):
    return requests.request(method, f"{BASE_URL}{path}", headers=headers, json=json, timeout=30, **kw)


def team_items(admin_headers) -> list:
    """Every row of the Super Admin team listing, paged (500 per page, oldest first). The scratch DB accumulates staff
    across runs, so an account created moments ago is not on page one - and "this account does not exist" is only a
    meaningful assertion when the whole listing has been read."""
    items, offset = [], 0
    while True:
        page = api("GET", f"/api/sales/team?limit=500&offset={offset}", admin_headers).json()
        items += page["items"]
        offset += 500
        if not page["items"] or offset >= page["total"]:
            return items


# ---------------------------------------------------------------------------
# Login fixtures for the Sales HTTP modules (override conftest's same-named ones there).
# conftest's `_login` does a real POST /auth/login per worker and asserts 200 with no retry, so a
# 429 from the shared 60/min limiter fails setup. These read the seeded demo users from the scratch
# DB and mint tokens instead: zero login requests. Import them into a test module to override.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin_token():
    return mint_token(lookup_user("admin@jaz.com")["id"])


@pytest.fixture(scope="module")
def owner_user():
    return lookup_user("owner@demo.com")


@pytest.fixture(scope="module")
def owner_token(owner_user):
    return mint_token(owner_user["id"])


@pytest.fixture(scope="module")
def employee_user():
    return lookup_user("employee1@demo.com")


@pytest.fixture(scope="module")
def employee_token(employee_user):
    return mint_token(employee_user["id"])
