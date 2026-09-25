"""Shared helpers for the JAZ Sales Phase-3 (calls, follow-ups, demos, trials) test modules.

Like the Phase-1/2 helpers they only ever run against the disposable scratch database
(sales_test_utils.assert_scratch_target). Leads and their work items are never deleted (the app archives / ends them),
so the scratch DB accumulates them across runs: every test creates its OWN leads (whose identity values are unique per
call) and asserts on its own items - never on "the whole table".
"""
from datetime import datetime, timedelta, timezone
from typing import List

from sales_lead_test_utils import (  # noqa: F401  (re-exported for the Phase-3 test modules)
    PHANTOM_ID,
    activities,
    assign,
    create_campaign,
    create_lead,
    errors_of,
    event_types,
    get_lead,
    lead_body,
    make_custom_role,
    make_personas,
    retire_roles,
    run_db,
    set_stage,
    uniq,
)
from sales_test_utils import api, create_staff  # noqa: F401


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def later(**delta) -> str:
    """An ISO timestamp `delta` from now (later(days=1), later(minutes=30) ...)."""
    return iso(utcnow() + timedelta(**delta))


def ago(**delta) -> str:
    return iso(utcnow() - timedelta(**delta))


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---- leads -----------------------------------------------------------------
def worked_lead(manager_headers: dict, employee_id: str, **overrides) -> dict:
    """A fresh lead assigned to `employee_id` (so it is in that employee's scope)."""
    lead = create_lead(manager_headers, **overrides)
    assign(manager_headers, lead["id"], employee_id)
    return get_lead(manager_headers, lead["id"])


# ---- creating work items ---------------------------------------------------
def _create(headers: dict, path: str, body: dict, expect: int):
    r = api("POST", path, headers, body)
    assert r.status_code == expect, f"POST {path}: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def create_call(headers: dict, lead_id: str, expect: int = 201, **overrides) -> dict:
    """Override anything; None drops the key from the request (like lead_body)."""
    body = {"lead_id": lead_id, "result": "answered"}
    body.update(overrides)
    return _create(headers, "/api/sales/calls", {k: v for k, v in body.items() if v is not None}, expect)


def create_followup(headers: dict, lead_id: str, expect: int = 201, **overrides) -> dict:
    body = {"lead_id": lead_id, "due_at": later(days=1)}
    body.update(overrides)
    return _create(headers, "/api/sales/followups", {k: v for k, v in body.items() if v is not None}, expect)


def create_demo(headers: dict, lead_id: str, expect: int = 201, **overrides) -> dict:
    body = {"lead_id": lead_id, "scheduled_at": later(days=2)}
    body.update(overrides)
    return _create(headers, "/api/sales/demos", {k: v for k, v in body.items() if v is not None}, expect)


def create_trial(headers: dict, lead_id: str, expect: int = 201, **overrides) -> dict:
    body = {"lead_id": lead_id, "expected_end_at": later(days=14)}
    body.update(overrides)
    return _create(headers, "/api/sales/trials", {k: v for k, v in body.items() if v is not None}, expect)


def act(headers: dict, kind: str, item_id: str, action: str, body=None, expect: int = 200) -> dict:
    """POST /api/sales/<kind>/<id>/<action> (complete, cancel, no-show, reschedule ...)."""
    r = api("POST", f"/api/sales/{kind}/{item_id}/{action}", headers, body)
    assert r.status_code == expect, f"{kind}/{action}: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def patch(headers: dict, kind: str, item_id: str, body: dict, expect: int = 200) -> dict:
    r = api("PATCH", f"/api/sales/{kind}/{item_id}", headers, body)
    assert r.status_code == expect, f"PATCH {kind}: expected {expect}, got {r.status_code} {r.text[:500]}"
    return r.json()


def fetch(headers: dict, kind: str, item_id: str, expect: int = 200) -> dict:
    r = api("GET", f"/api/sales/{kind}/{item_id}", headers)
    assert r.status_code == expect, f"GET {kind}/{item_id}: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def listing(headers: dict, path: str, expect: int = 200) -> dict:
    """GET a list endpoint (path includes the query string)."""
    r = api("GET", path, headers)
    assert r.status_code == expect, f"GET {path}: expected {expect}, got {r.status_code} {r.text[:300]}"
    return r.json()


def ids_of(page: dict) -> List[str]:
    return [item["id"] for item in page["items"]]


def timeline_of(headers: dict, lead_id: str) -> List[dict]:
    """The lead's timeline, OLDEST first - only the events of the Phase-3 work items (call_/followup_/demo_/trial_)."""
    return [e for e in activities(headers, lead_id) if e["event_type"].split("_")[0] in ("call", "followup", "demo", "trial")]


def work_types(headers: dict, lead_id: str) -> List[str]:
    return [e["event_type"] for e in timeline_of(headers, lead_id)]


def stage_of(headers: dict, lead_id: str) -> str:
    return get_lead(headers, lead_id)["pipeline_stage"]
