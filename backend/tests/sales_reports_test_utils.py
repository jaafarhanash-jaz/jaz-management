"""Shared helpers for the JAZ Sales Phase-5 (dashboard and reports) test modules.

Like the earlier helpers they only ever run against the disposable scratch database (sales_test_utils.assert_scratch_target).
Leads and their work are never deleted, so the scratch DB accumulates them across runs and a team-wide total can never be
compared with a constant. The tests get EXACT numbers in two ways instead:

  * an EMPTY WINDOW: `build_world` picks a few-day window in the far past (2001-2015), proves it holds no lead and no work
    (through the API, as Super Admin), creates a known dataset and BACKDATES it into the window (leads by a direct UPDATE of
    created_at / closed_at; calls, follow-ups, demos and trials through the API, which accepts past timestamps). The team-wide
    numbers of that window are then exactly the dataset - compared against `World`'s Python oracle, never against the code
    under test;
  * FRESH PERSONAS: a personal (non-team) dashboard, or a team dashboard narrowed to one freshly created employee, only ever
    sees that employee's own data - exact even for "now" readings such as overdue follow-ups.
"""
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from sales_activity_test_utils import (  # noqa: F401  (re-exported for the Phase-5 test modules)
    PHANTOM_ID,
    act,
    activities,
    assign,
    create_call,
    create_campaign,
    create_demo,
    create_followup,
    create_lead,
    create_staff,
    create_trial,
    errors_of,
    get_lead,
    later,
    make_custom_role,
    make_personas,
    retire_roles,
    run_db,
    set_stage,
    uniq,
)
from sales_test_utils import api

DAY = timedelta(days=1)


def utc(y, m, d, h=0, mi=0, s=0, us=0) -> datetime:
    return datetime(y, m, d, h, mi, s, us, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def app_today() -> date:
    """The calendar date it is now in the application timezone (Baghdad) - what the server calls "today"."""
    from sales.timezone import app_today as _today
    return _today()


# ---- requests --------------------------------------------------------------------------------------------------------
def get(headers: dict, path: str, expect: int = 200, **params):
    """GET /api/sales/<path> with query params (None values dropped). Returns the JSON body (or the response when expect != 200)."""
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    r = api("GET", f"/api/sales/{path}" + (f"?{query}" if query else ""), headers)
    assert r.status_code == expect, f"GET {path}?{query}: expected {expect}, got {r.status_code} {r.text[:400]}"
    return r.json() if expect == 200 else r


def dash(headers: dict, expect: int = 200, **params):
    return get(headers, "dashboard", expect, **params)


def report(headers: dict, name: str, expect: int = 200, **params):
    return get(headers, f"reports/{name}", expect, **params)


def window_params(window: "Window") -> dict:
    return {"date_from": window.start.isoformat(), "date_to": window.end.isoformat()}


# ---- windows ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Window:
    start: date      # inclusive
    end: date        # inclusive

    @property
    def lo(self) -> datetime:
        """The first instant of the window: midnight of its first day in the APPLICATION timezone (21:00 UTC the day before)."""
        from sales.timezone import day_start
        return day_start(self.start)

    @property
    def hi(self) -> datetime:
        """The first instant after the window: midnight after its last day, in the application timezone."""
        from sales.timezone import day_start
        return day_start(self.end + DAY)

    def contains(self, when: datetime) -> bool:
        return self.lo <= when < self.hi


def empty_window(admin_headers: dict, days: int = 3, pad: int = 3) -> Window:
    """A window of `days` days in 2001-2015 that holds no lead and no work at all - and neither do the `pad` days on each
    side of it, where the boundary probes (a lead one instant outside the window) live. Proved through the API, as Super
    Admin, who sees every team number. Random, so runs and modules do not collide; re-drawn if it is not empty."""
    for _ in range(60):
        start = date(2001, 1, 1) + timedelta(days=random.randint(0, 5400))
        window = Window(start, start + timedelta(days=days - 1))
        padded = Window(start - timedelta(days=pad), window.end + timedelta(days=pad))
        body = dash(admin_headers, **window_params(padded))
        onboarding = body["onboarding"] or {"activated_in_period": 0}
        if body["kpis"]["total_leads"] == 0 and body["activity"]["total"] == 0 and onboarding["activated_in_period"] == 0:
            return window
    raise AssertionError("could not find an empty window in the scratch database")


# ---- direct-DB backdating (scratch only) ---------------------------------------------------------------------------------
def backdate_lead(lead_id: str, created_at: datetime, closed_at: Optional[datetime] = None) -> None:
    from sqlalchemy import update
    from sales.models import SalesLead

    values = {"created_at": created_at}
    if closed_at is not None:
        values["closed_at"] = closed_at

    async def _go(db):
        await db.execute(update(SalesLead).where(SalesLead.id == uuid.UUID(lead_id)).values(**values))

    run_db(_go)


def backdate_onboarding(onboarding_id: str, started_at: datetime) -> None:
    from sqlalchemy import update
    from sales.models import SalesOnboarding

    async def _go(db):
        await db.execute(update(SalesOnboarding).where(SalesOnboarding.id == uuid.UUID(onboarding_id)).values(started_at=started_at))

    run_db(_go)


# ---- the known dataset ------------------------------------------------------------------------------------------------------
@dataclass
class LeadSpec:
    key: str
    owner: Optional[str]                   # "e1" | "e2" | None (nobody)
    stage: str
    source: str
    campaign: Optional[str] = None         # "C1" | "C2" | None
    value: Optional[float] = None
    priority: str = "medium"
    when: str = "in"                       # where it was CREATED: in | start | last | before | after
    closed: bool = False                   # closed (won / lost) INSIDE the window
    archived: bool = False
    lost_reason: Optional[str] = None
    id: str = ""


# where a lead is created, relative to a window [D0, D0+2]
def _created(window: Window, when: str) -> datetime:
    d0 = window.lo
    return {
        "in": d0 + DAY + timedelta(hours=12),                       # D1 12:00
        "start": d0,                                                # D0 00:00:00.000000 - the first instant, INCLUSIVE
        "last": window.hi - timedelta(microseconds=1),              # D2 23:59:59.999999 - the last instant, INCLUSIVE
        "before": d0 - DAY + timedelta(hours=12),                   # the day before
        "after": window.hi,                                         # D3 00:00:00 - the first instant AFTER, EXCLUSIVE
        "long_before": d0 - 30 * DAY,
    }[when]


LEAD_SPECS = [
    LeadSpec("L1", "e1", "assigned", "google_maps", "C1", 100, "high"),
    LeadSpec("L2", "e1", "contacted", "facebook", "C1", 200),
    LeadSpec("L3", "e1", "interested", "facebook", None, None),
    LeadSpec("L4", "e1", "won", "referral", "C1", 1000, closed=True),
    LeadSpec("L5", "e1", "lost", "cold_call", "C2", None, closed=True, lost_reason="too_expensive"),
    LeadSpec("L6", "e2", "assigned", "facebook", "C2", None),
    LeadSpec("L7", "e2", "won", "facebook", "C1", 500, closed=True),
    LeadSpec("L8", "e2", "demo_scheduled", "website", None, 300),
    LeadSpec("L9", None, "new", "import", None, 50),
    LeadSpec("L12", "e1", "assigned", "google_maps", None, None, when="start"),
    LeadSpec("L13", "e1", "assigned", "google_maps", None, None, when="last"),
    LeadSpec("A1", "e1", "assigned", "facebook", None, None, archived=True),                       # archived: counted nowhere
    # outside the window
    LeadSpec("X1", "e1", "assigned", "facebook", "C1", 9000, when="before"),
    LeadSpec("X2", "e1", "assigned", "facebook", "C1", 9000, when="after"),
    # created BEFORE the window, closed INSIDE it: not in the cohort, but a deal closed in the period
    LeadSpec("X3", "e1", "won", "referral", "C1", 700, when="long_before", closed=True),
]


@dataclass
class World:
    window: Window
    campaigns: Dict[str, str]                      # "C1" | "C2" -> id
    leads: Dict[str, LeadSpec]
    people: Dict[str, dict]                        # "manager" | "e1" | "e2" -> persona
    calls_archived_lead: str = ""
    extra: dict = field(default_factory=dict)
    # work still OPEN when the dataset was built ((kind, id): a pending follow-up, a scheduled demo, an active trial) - see wind_down
    open_items: List[tuple] = field(default_factory=list)

    # -- the oracle: what the dataset says, computed here in Python, independent of the code under test --
    def in_cohort(self, spec: LeadSpec) -> bool:
        return (not spec.archived) and self.window.contains(_created(self.window, spec.when))

    def cohort(self, owner: Optional[str] = "*", source: Optional[str] = None, campaign: Optional[str] = "*") -> List[LeadSpec]:
        out = [s for s in self.leads.values() if self.in_cohort(s)]
        if owner != "*":
            out = [s for s in out if s.owner == owner]
        if source is not None:
            out = [s for s in out if s.source == source]
        if campaign != "*":
            out = [s for s in out if s.campaign == campaign]
        return out

    def owner_id(self, owner: Optional[str]) -> Optional[str]:
        return self.people[owner]["id"] if owner else None


def count_stage(leads: List[LeadSpec], *stages: str) -> int:
    return sum(1 for s in leads if s.stage in stages)


def value_sum(leads: List[LeadSpec]) -> float:
    return float(sum(s.value or 0 for s in leads))


def build_world(admin_headers: dict, manager: dict, e1: dict, e2: dict) -> World:
    """Create the known dataset (see LEAD_SPECS and the activities below) inside a verified-empty window."""
    window = empty_window(admin_headers)
    m = manager["headers"]
    campaigns = {
        "C1": create_campaign(m, source="facebook")["id"],
        "C2": create_campaign(m, source="google_maps")["id"],
    }
    world = World(window, campaigns, {}, {"manager": manager, "e1": e1, "e2": e2})

    for template in LEAD_SPECS:
        spec = LeadSpec(**{**template.__dict__})            # a private copy per world
        overrides = {"source": spec.source, "priority": spec.priority}
        if spec.campaign:
            overrides["campaign_id"] = campaigns[spec.campaign]
        if spec.value is not None:
            overrides["estimated_value"] = spec.value
        lead = create_lead(m, **overrides)
        spec.id = lead["id"]
        if spec.owner:
            assign(m, spec.id, world.people[spec.owner]["id"])
        # walk the pipeline the way a real lead moves (won needs a working stage first)
        if spec.stage in ("contacted", "interested", "demo_scheduled"):
            set_stage(m, spec.id, spec.stage)
        elif spec.stage == "won":
            set_stage(m, spec.id, "contacted")
            set_stage(m, spec.id, "won")
        elif spec.stage == "lost":
            set_stage(m, spec.id, "lost", lost_reason=spec.lost_reason)
        world.leads[spec.key] = spec

    # -- work, attributed to the employee who did it; timestamps inside the window (past is accepted by the API) --
    t_in = window.lo + DAY + timedelta(hours=10)
    t_before = window.lo - DAY + timedelta(hours=10)
    L = {k: s.id for k, s in world.leads.items()}
    h1, h2 = e1["headers"], e2["headers"]

    create_call(h1, L["L1"], result="answered", called_at=iso(t_in))
    create_call(h1, L["L2"], result="no_answer", called_at=iso(t_in))
    create_call(h1, L["L3"], result="interested", called_at=iso(t_in))
    create_call(h1, L["L1"], result="busy", called_at=iso(t_before))                                  # outside the window
    create_call(h1, L["A1"], result="answered", called_at=iso(t_in))                                  # its lead is archived below
    create_call(h2, L["L6"], result="answered", called_at=iso(t_in))
    create_call(h2, L["L7"], result="wrong_number", called_at=iso(t_in))

    for lead_key in ("L1", "L2"):
        world.open_items.append(("followups", create_followup(h1, L[lead_key], due_at=iso(t_in))["id"]))
    cancelled_followup = create_followup(h1, L["L3"], due_at=iso(t_in))
    act(h1, "followups", cancelled_followup["id"], "cancel")
    done_followup = create_followup(h2, L["L6"], due_at=iso(t_in))
    act(h2, "followups", done_followup["id"], "complete")
    world.open_items.append(("followups", create_followup(h1, L["L1"], due_at=later(days=1))["id"]))   # upcoming (now-basis)
    world.open_items.append(("followups", create_followup(h1, L["L2"], due_at=iso(window.lo - 40 * DAY))["id"]))   # overdue, outside the window

    world.open_items.append(("demos", create_demo(h1, L["L2"], scheduled_at=iso(t_in))["id"]))
    cancelled_demo = create_demo(h1, L["L3"], scheduled_at=iso(t_in))
    act(h1, "demos", cancelled_demo["id"], "cancel")
    world.open_items.append(("demos", create_demo(h1, L["L1"], scheduled_at=later(days=3))["id"]))    # upcoming (now-basis)
    done_demo = create_demo(h2, L["L7"], scheduled_at=iso(t_in))
    act(h2, "demos", done_demo["id"], "complete")

    world.open_items.append(("trials", create_trial(h1, L["L2"], started_at=iso(t_in), expected_end_at=later(days=14))["id"]))   # active (now-basis)
    ended_trial = create_trial(h2, L["L7"], started_at=iso(t_in), expected_end_at=later(days=14))
    act(h2, "trials", ended_trial["id"], "complete")

    # -- archive, THEN backdate (an archived lead is hidden with its work) --
    assert api("DELETE", f"/api/sales/leads/{L['A1']}", m).status_code == 200
    for spec in world.leads.values():
        created = _created(window, spec.when)
        closed = (window.lo + DAY + timedelta(hours=14)) if spec.closed else None
        backdate_lead(spec.id, created, closed)
    return world


def wind_down(manager_headers: dict, items) -> None:
    """Cancel the work a test dataset still has OPEN ((kind, id) pairs of pending follow-ups, scheduled demos, active trials).

    The scratch database is shared and accumulates: an item with a far-past due time that stays pending sorts to the FRONT of
    every global "overdue" / "past due" list and would push other tests' items off their first page (the Phase 3 tests read
    `?due=overdue&limit=100`). A dataset therefore leaves nothing open behind it. Done as the manager, who sees every lead even
    after one was reassigned. A cancelled item is terminal, so it is in no "open work" list."""
    for kind, item_id in items:
        r = api("POST", f"/api/sales/{kind}/{item_id}/cancel", manager_headers)
        assert r.status_code in (200, 409), f"wind down {kind}/{item_id}: {r.status_code} {r.text[:200]}"


def person_row(rows: List[dict], person_id: Optional[str]) -> Optional[dict]:
    """The row of one employee (None = the unassigned row) in a performance / activity table."""
    for row in rows:
        emp = row["employee"]
        if (emp["id"] if emp else None) == person_id:
            return row
    return None
