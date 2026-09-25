"""Shared helpers for the JAZ Sales Phase-2 (leads) test modules.

Like the Phase-1 helpers they only ever run against the disposable scratch database (sales_test_utils
.assert_scratch_target). Leads are never deleted (the app archives), so the scratch DB accumulates them across
runs: every value that duplicate detection looks at (phone, email, website, name) is generated unique per call, and
tests isolate their rows with their own campaign or search token instead of assuming an empty table.
"""
import asyncio
import random
import uuid
from typing import Dict, Iterable, List

from sales_test_utils import api, create_staff

PHANTOM_ID = "00000000-0000-4000-8000-000000000000"


def uniq(n: int = 10) -> str:
    return uuid.uuid4().hex[:n]


def unique_lead_phone() -> str:
    """+1 then ten random digits. Ten random digits make an accidental tail collision with another test's number
    (or a leftover from an earlier run) negligible."""
    return "+1" + "".join(random.choices("23456789", k=1) + random.choices("0123456789", k=9))


def rand_digits(n: int) -> str:
    return "".join(random.choices("0123456789", k=n))


def unique_lead_email(label: str = "lead") -> str:
    return f"{label}-{uniq()}@lead-tests.example.com"


def unique_website() -> str:
    return f"https://www.site-{uniq()}.example.org/"


def lead_body(**overrides) -> dict:
    """A valid create body whose identity values are all unique. Override anything (None removes a key)."""
    body = {
        "business_name": f"Acme Trading {uniq()}",
        "business_type": "Retail",
        "contact_name": "Test Contact",
        "phone": unique_lead_phone(),
        "email": unique_lead_email(),
        "city": f"City-{uniq(6)}",
        "country": "Iraq",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def create_lead(headers: dict, expect: int = 201, **overrides) -> dict:
    r = api("POST", "/api/sales/leads", headers, lead_body(**overrides))
    assert r.status_code == expect, f"create lead: expected {expect}, got {r.status_code} {r.text[:400]}"
    return r.json()


def get_lead(headers: dict, lead_id: str) -> dict:
    r = api("GET", f"/api/sales/leads/{lead_id}", headers)
    assert r.status_code == 200, f"get lead: {r.status_code} {r.text[:300]}"
    return r.json()


def activities(headers: dict, lead_id: str, limit: int = 200) -> List[dict]:
    """The lead's timeline, OLDEST first (the API returns newest first)."""
    r = api("GET", f"/api/sales/leads/{lead_id}/activities?limit={limit}", headers)
    assert r.status_code == 200, f"activities: {r.status_code} {r.text[:300]}"
    return list(reversed(r.json()["items"]))


def event_types(headers: dict, lead_id: str) -> List[str]:
    return [e["event_type"] for e in activities(headers, lead_id)]


def create_campaign(headers: dict, expect: int = 201, **overrides) -> dict:
    body = {"name": f"Campaign {uniq()}", "source": "facebook"}
    body.update(overrides)
    r = api("POST", "/api/sales/campaigns", headers, {k: v for k, v in body.items() if v is not None})
    assert r.status_code == expect, f"create campaign: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def assign(headers: dict, lead_id: str, assignee_id: str, expect: int = 200):
    r = api("POST", f"/api/sales/leads/{lead_id}/assign", headers, {"assigned_to": assignee_id})
    assert r.status_code == expect, f"assign: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def set_stage(headers: dict, lead_id: str, stage: str, expect: int = 200, **extra):
    r = api("POST", f"/api/sales/leads/{lead_id}/stage", headers, {"stage": stage, **extra})
    assert r.status_code == expect, f"stage {stage}: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def make_personas(admin_h: dict, prefix: str = "lp") -> Dict[str, dict]:
    """The staff personas the lead tests need, created through the real API as Super Admin."""
    return {
        "manager": create_staff(admin_h, ["sales_manager"], f"{prefix}-manager"),
        "employee": create_staff(admin_h, ["sales_employee"], f"{prefix}-employee"),
        "employee2": create_staff(admin_h, ["sales_employee"], f"{prefix}-employee2"),
        "data_entry": create_staff(admin_h, ["lead_data_entry"], f"{prefix}-entry"),
        "data_entry2": create_staff(admin_h, ["lead_data_entry"], f"{prefix}-entry2"),
        "onboarding": create_staff(admin_h, ["onboarding_employee"], f"{prefix}-onboarding"),
        "no_roles": create_staff(admin_h, [], f"{prefix}-noroles"),
    }


def errors_of(response) -> dict:
    """The {"field","message","code",...} detail of a Sales error response."""
    detail = response.json()["detail"]
    assert isinstance(detail, dict), f"expected a structured error, got {detail!r}"
    return detail


# ---------------------------------------------------------------------------
# direct-DB helpers (scratch only)
# ---------------------------------------------------------------------------
def run_db(fn):
    """Run `async def fn(db)` in a fresh session and return its result; commits on success. Engine disposed after."""
    from database import SessionLocal, engine

    async def _go():
        try:
            async with SessionLocal() as db:
                result = await fn(db)
                await db.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def make_custom_role(permission_keys: Iterable[str]) -> str:
    """A throwaway staff role holding exactly `permission_keys` (direct DB write; scratch only). Returns its key."""
    from sales.models import StaffRole, StaffRolePermission

    key = f"test_{uuid.uuid4().hex[:10]}"

    async def _go(db):
        role = StaffRole(id=uuid.uuid4(), module="sales", key=key, name_en=key, name_ar=key)
        db.add(role)
        await db.flush()
        for permission in permission_keys:
            db.add(StaffRolePermission(role_id=role.id, permission_key=permission))

    run_db(_go)
    return key


def retire_roles(role_keys: Iterable[str]) -> None:
    """Roles that were ever assigned cannot be deleted (RESTRICT): retire them so they stop granting anything."""
    from sqlalchemy import update
    from sales.models import StaffRole

    async def _go(db):
        await db.execute(update(StaffRole).where(StaffRole.key.in_(list(role_keys)), StaffRole.is_system.is_(False)).values(is_active=False))

    run_db(_go)


def demo_company() -> Dict[str, str]:
    """The seeded demo company and its owner (read straight from the scratch DB)."""
    from sqlalchemy import select
    from models import Company, User

    async def _go(db):
        row = (await db.execute(
            select(Company.id, Company.name, User.email, User.phone)
            .join(User, User.id == Company.owner_id)
            .where(User.email == "owner@demo.com", Company.deleted_at.is_(None))
        )).first()
        return {"id": str(row[0]), "name": row[1], "owner_email": row[2], "owner_phone": row[3]}

    return run_db(_go)
