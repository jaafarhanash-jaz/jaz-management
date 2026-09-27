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
    return f"+1555{random.randint(1000000, 9999999)}"


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


def api(method: str, path: str, headers=None, json=None, **kw):
    return requests.request(method, f"{BASE_URL}{path}", headers=headers, json=json, timeout=30, **kw)


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
