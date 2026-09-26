"""JAZ Sales - Phase 5: the eight reports (HTTP integration tier).

Lead, pipeline, employee performance, lead source, campaign, conversion and activity reports (the onboarding report is in
test_sales_reports_onboarding.py): the filters, pagination, aggregation correctness and empty datasets. Expected numbers come
from the Python oracle over the dataset the fixture created (sales_reports_test_utils.py), never from the code under test.
WHO may open which report is in test_sales_reports_rbac.py.
"""
import time
from datetime import date, timedelta

import pytest

from sales_reports_test_utils import (
    DAY,
    api,
    assign,
    backdate_lead,
    build_world,
    count_stage,
    create_campaign,
    create_lead,
    create_staff,
    empty_window,
    errors_of,
    make_custom_role,
    make_personas,
    retire_roles,
    dash,
    person_row,
    report,
    value_sum,
    window_params,
    wind_down,
)
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401

STAGES = ["new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won", "lost"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def personas(admin_h):
    return make_personas(admin_h, "rr")


@pytest.fixture(scope="module")
def world(admin_h, personas):
    made = build_world(admin_h, personas["manager"], personas["employee"], personas["employee2"])
    yield made
    wind_down(personas["manager"]["headers"], made.open_items)          # leave no open work in the shared scratch database


@pytest.fixture(scope="module")
def m(personas):
    return personas["manager"]["headers"]


def figures(leads):
    n, won, lost = len(leads), count_stage(leads, "won"), count_stage(leads, "lost")
    return {
        "leads": n, "won": won, "lost": lost, "open": n - won - lost,
        "conversion_rate": round(won * 100.0 / n, 1) if n else None,
        "value": value_sum(leads), "won_value": value_sum([s for s in leads if s.stage == "won"]),
    }


def ids(world, keys):
    return {world.leads[k].id for k in keys}


# =============================================================================
# 1. Lead report
# =============================================================================
class TestLeadReport:
    def test_lists_exactly_the_leads_created_in_the_period(self, world, m):
        page = report(m, "leads", limit=100, **window_params(world.window))
        assert {item["id"] for item in page["items"]} == ids(world, ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L12", "L13"])
        assert page["total"] == 11 and page["summary"]["total"] == 11 and page["scope"] == "team"
        assert page["summary"]["value"] == value_sum(world.cohort())
        assert page["summary"]["by_stage"] == {s: count_stage(world.cohort(), s) for s in STAGES}

    def test_a_row_carries_what_a_report_needs_and_no_contact_details(self, world, m):
        page = report(m, "leads", limit=100, **window_params(world.window))
        row = next(i for i in page["items"] if i["id"] == world.leads["L1"].id)
        assert set(row) == {"id", "business_name", "city", "pipeline_stage", "priority", "source", "campaign", "assigned_to", "estimated_value", "created_at", "closed_at"}
        assert row["pipeline_stage"] == "assigned" and row["priority"] == "high" and row["source"] == "google_maps"
        assert row["campaign"]["id"] == world.campaigns["C1"] and row["campaign"]["name"]
        assert row["assigned_to"]["id"] == world.owner_id("e1") and row["estimated_value"] == 100.0 and row["closed_at"] is None
        won = next(i for i in page["items"] if i["id"] == world.leads["L4"].id)
        assert won["closed_at"] is not None                                         # a closed lead says when it closed

    def test_pagination_covers_every_lead_once(self, world, m):
        seen, total = [], None
        for offset in (0, 4, 8, 12):
            page = report(m, "leads", limit=4, offset=offset, **window_params(world.window))
            total = page["total"]
            assert page["limit"] == 4 and page["offset"] == offset
            seen += [item["id"] for item in page["items"]]
        assert total == 11 and len(seen) == 11 and len(set(seen)) == 11
        assert set(seen) == ids(world, ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L12", "L13"])

    def test_default_order_is_newest_first(self, world, m):
        items = report(m, "leads", limit=100, **window_params(world.window))["items"]
        assert items[0]["id"] == world.leads["L13"].id and items[-1]["id"] == world.leads["L12"].id      # the last instant first, the first instant last

    def test_sorting_by_value_puts_the_unvalued_leads_last(self, world, m):
        items = report(m, "leads", sort="estimated_value", order="desc", limit=100, **window_params(world.window))["items"]
        values = [i["estimated_value"] for i in items]
        assert values[:6] == [1000.0, 500.0, 300.0, 200.0, 100.0, 50.0] and values[6:] == [None] * 5
        asc = report(m, "leads", sort="estimated_value", order="asc", limit=100, **window_params(world.window))["items"]
        assert [i["estimated_value"] for i in asc][:6] == [50.0, 100.0, 200.0, 300.0, 500.0, 1000.0]

    def test_filters(self, world, m):
        w = window_params(world.window)
        e1, e2 = world.owner_id("e1"), world.owner_id("e2")

        def got(**filters):
            return {i["id"] for i in report(m, "leads", limit=100, **w, **filters)["items"]}

        assert got(source="facebook") == ids(world, ["L2", "L3", "L6", "L7"])
        assert got(campaign_id=world.campaigns["C1"]) == ids(world, ["L1", "L2", "L4", "L7"])
        assert got(employee_id=e1) == ids(world, ["L1", "L2", "L3", "L4", "L5", "L12", "L13"])
        assert got(employee_id=e2) == ids(world, ["L6", "L7", "L8"])
        assert got(pipeline_stage="won,lost") == ids(world, ["L4", "L5", "L7"])
        assert got(pipeline_stage="new") == ids(world, ["L9"])
        assert got(priority="high") == ids(world, ["L1"])
        assert got(source="facebook", employee_id=e1) == ids(world, ["L2", "L3"])                     # filters combine
        assert got(source="import", employee_id=e1) == set()

    def test_the_summary_ignores_the_stage_filter_but_not_the_others(self, world, m):
        page = report(m, "leads", pipeline_stage="won", source="facebook", limit=100, **window_params(world.window))
        assert [i["id"] for i in page["items"]] == [world.leads["L7"].id] and page["total"] == 1
        assert page["summary"]["total"] == 4                                        # all four facebook leads, whatever their stage
        assert page["summary"]["by_stage"]["won"] == 1 and page["summary"]["by_stage"]["assigned"] == 1

    def test_bad_filters_are_400_and_bad_paging_is_422(self, world, m):
        w = window_params(world.window)
        assert errors_of(report(m, "leads", expect=400, pipeline_stage="nope", **w))["field"] == "pipeline_stage"
        assert errors_of(report(m, "leads", expect=400, employee_id="x", **w))["field"] == "employee_id"
        for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"sort": "password"}, {"order": "sideways"}, {"priority": "urgent"}, {"source": "Face book"}):
            assert report(m, "leads", expect=422, **w, **bad).status_code == 422, bad

    def test_an_empty_period(self, admin_h, m):
        page = report(m, "leads", **window_params(empty_window(admin_h)))
        assert page["items"] == [] and page["total"] == 0
        assert page["summary"] == {"total": 0, "value": 0.0, "by_stage": {s: 0 for s in STAGES}}


# =============================================================================
# 2. Pipeline report
# =============================================================================
class TestPipelineReport:
    def test_leads_and_value_per_current_stage(self, world, m):
        body = report(m, "pipeline", **window_params(world.window))
        cohort = world.cohort()
        assert body["basis"] == "created" and body["period"]["days"] == 3
        assert [s["stage"] for s in body["stages"]] == STAGES
        for row in body["stages"]:
            in_stage = [s for s in cohort if s.stage == row["stage"]]
            assert (row["leads"], row["value"]) == (len(in_stage), value_sum(in_stage)), row["stage"]
        assert body["total_leads"] == 11 and body["total_value"] == value_sum(cohort) == 2150.0

    def test_filters(self, world, m):
        w = window_params(world.window)
        by_c1 = report(m, "pipeline", campaign_id=world.campaigns["C1"], **w)
        assert by_c1["total_leads"] == 4 and by_c1["total_value"] == 1800.0
        by_e2 = report(m, "pipeline", employee_id=world.owner_id("e2"), **w)
        assert by_e2["total_leads"] == 3 and {s["stage"]: s["leads"] for s in by_e2["stages"] if s["leads"]} == {"assigned": 1, "won": 1, "demo_scheduled": 1}
        assert report(m, "pipeline", source="import", **w)["total_leads"] == 1

    def test_the_live_pipeline_counts_every_lead_whenever_it_was_created(self, world, m):
        e1 = world.owner_id("e1")
        live = report(m, "pipeline", basis="all", employee_id=e1)
        assert live["basis"] == "all" and live["period"] is None                   # not tied to a period
        mine = [s for s in world.leads.values() if s.owner == "e1" and not s.archived]
        assert live["total_leads"] == len(mine) == 10                              # the 7 in the window + X1, X2 and X3 outside it
        assert {s["stage"]: s["leads"] for s in live["stages"]}["won"] == 2        # L4 and X3
        created = report(m, "pipeline", employee_id=e1, **window_params(world.window))
        assert created["total_leads"] == 7

    def test_an_unknown_basis_is_rejected(self, m):
        assert report(m, "pipeline", expect=422, basis="whenever").status_code == 422

    def test_an_empty_period(self, admin_h, m):
        body = report(m, "pipeline", **window_params(empty_window(admin_h)))
        assert body["total_leads"] == 0 and body["total_value"] == 0.0 and all(s["leads"] == 0 for s in body["stages"])


# =============================================================================
# 3. Employee performance
# =============================================================================
class TestEmployeePerformanceReport:
    def test_rows_totals_and_order(self, world, m):
        body = report(m, "employee-performance", **window_params(world.window))
        e1, e2 = world.owner_id("e1"), world.owner_id("e2")
        rows = body["items"]
        assert [r["employee"]["id"] if r["employee"] else None for r in rows[:3]] == [e1, e2, None]           # the busiest first ...
        assert all((r["leads"], r["activities"]) == (0, 0) for r in rows[3:])                                    # ... then the idle staff, as zero rows
        r1, r2, none = rows[:3]
        assert (r1["leads"], r1["won"], r1["lost"], r1["open"], r1["conversion_rate"]) == (7, 1, 1, 5, 14.3)
        assert (r1["calls"], r1["followups"], r1["demos"], r1["trials"], r1["activities"]) == (3, 2, 1, 1, 7)
        assert (r2["leads"], r2["conversion_rate"], r2["activities"]) == (3, 33.3, 5)
        assert none["employee"] is None and none["leads"] == 1
        t = body["totals"]
        assert (t["leads"], t["won"], t["lost"], t["open"], t["conversion_rate"], t["activities"]) == (11, 2, 1, 8, 18.2, 12)
        assert (t["calls"], t["followups"], t["demos"], t["trials"]) == (5, 3, 2, 2)
        assert body["total"] >= 3                                                    # E1, E2, the unassigned leads, and every idle employee

    def test_pagination_pages_through_the_employees_and_the_totals_cover_them_all(self, world, m):
        w = window_params(world.window)
        first = report(m, "employee-performance", limit=1, offset=0, **w)
        second = report(m, "employee-performance", limit=1, offset=1, **w)
        third = report(m, "employee-performance", limit=1, offset=2, **w)
        assert [len(p["items"]) for p in (first, second, third)] == [1, 1, 1]
        assert first["items"][0]["employee"]["id"] == world.owner_id("e1") and second["items"][0]["employee"]["id"] == world.owner_id("e2")
        assert third["items"][0]["employee"] is None
        assert first["total"] >= 3 and first["totals"]["leads"] == 11              # the totals are of everyone, not of the page
        after = report(m, "employee-performance", limit=5, offset=3, **w)["items"]      # past the three with data: only idle staff, if any
        assert all((r["leads"], r["activities"]) == (0, 0) and r["employee"] for r in after)

    def test_narrowing_to_one_employee_and_to_a_source(self, world, m):
        w = window_params(world.window)
        one = report(m, "employee-performance", employee_id=world.owner_id("e2"), **w)
        assert [r["employee"]["id"] for r in one["items"]] == [world.owner_id("e2")] and one["employee"]["id"] == world.owner_id("e2")
        fb = report(m, "employee-performance", source="facebook", **w)
        r1 = person_row(fb["items"], world.owner_id("e1"))
        assert (r1["leads"], r1["won"]) == (2, 0)                                   # L2, L3
        assert person_row(fb["items"], None) is None or person_row(fb["items"], None)["leads"] == 0

    def test_an_empty_period(self, admin_h, m):
        body = report(m, "employee-performance", **window_params(empty_window(admin_h)))
        assert all((r["leads"], r["activities"], r["conversion_rate"]) == (0, 0, None) for r in body["items"])     # idle staff only: zero rows
        assert (body["totals"]["leads"], body["totals"]["conversion_rate"], body["totals"]["activities"]) == (0, None, 0)


def _find_row(headers, person_id, **params):
    """The performance row of one employee, scanning the pages (a shared scratch database lists hundreds of idle staff)."""
    offset = 0
    while True:
        page = report(headers, "employee-performance", limit=200, offset=offset, **params)
        for row in page["items"]:
            if row["employee"] and row["employee"]["id"] == person_id:
                return row, page["total"]
        offset += 200
        if offset >= page["total"]:
            return None, page["total"]


def _first_by_name(label: str) -> str:
    """A staff label whose account name sorts BEFORE every other name in the shared scratch database. The idle list is the first
    MAX_GROUPS (5000) assignable staff BY NAME, and thousands of test accounts pile up across runs: a fixed label such as "rr-idle"
    sorts among them and is cut off once enough of them sort before it. A leading digit sorts before every lettered name, and the
    number that follows shrinks as time passes, so it also sorts before the same label of every earlier run."""
    return f"0{10 ** 13 - int(time.time() * 1000):013d}-{label}"


class TestIdleEmployeesAreListed:
    """A manager comparing employees must see the one who did nothing, not have them silently missing. The names come from the
    assignment picker's rule (sales.leads.assign): a team caller without that privilege gets no extra names."""

    def test_an_employee_with_nothing_to_report_is_a_zero_row_for_a_manager(self, admin_h, world, m):
        idle = create_staff(admin_h, ["sales_employee"], _first_by_name("rr-idle"))       # always inside the capped list (see _first_by_name)
        w = window_params(world.window)
        for who, headers in (("manager", m), ("super admin", admin_h)):
            row, total = _find_row(headers, idle["id"], **w)
            assert row is not None, who
            assert (row["leads"], row["won"], row["lost"], row["open"], row["conversion_rate"], row["activities"]) == (0, 0, 0, 0, None, 0)
            assert (row["calls"], row["followups"], row["demos"], row["trials"]) == (0, 0, 0, 0)
            assert total >= 4                                                        # E1, E2, the unassigned leads and (at least) this one
        # the dashboard counts them too - the busiest first, so E1 / E2 head the list
        perf = dash(m, **w)["performance"]
        assert perf["total"] >= 4 and perf["items"][0]["employee"]["id"] == world.owner_id("e1")

    def test_a_deactivated_employee_is_not_listed(self, admin_h, world, m):
        gone = create_staff(admin_h, ["sales_employee"], "rr-gone")
        assert api("PATCH", f"/api/sales/team/{gone['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert _find_row(m, gone["id"], **window_params(world.window))[0] is None

    def test_staff_who_cannot_receive_leads_are_not_listed(self, admin_h, world, m):
        """The list is the assignment picker's people: a Lead Data Entry or Onboarding account is nobody's "employee with no leads"."""
        w = window_params(world.window)
        from sales_customer_test_utils import onboarding_worker
        others = {"lead_data_entry": create_staff(admin_h, ["lead_data_entry"], "rr-not-lead"),
                  "onboarding": onboarding_worker(admin_h, "rr-not-onbo")}          # the (retired) onboarding keys, via a custom role
        for role, other in others.items():
            for headers in (m, admin_h):
                assert _find_row(headers, other["id"], **w)[0] is None, role

    def test_a_team_caller_without_the_assignment_privilege_gets_no_extra_names(self, admin_h, world):
        role = make_custom_role({"sales.access", "sales.reports.view", "sales.dashboard.view", "sales.leads.view", "sales.leads.scope_all"})
        try:
            viewer = create_staff(admin_h, [role], "rr-noassign")
            idle = create_staff(admin_h, ["sales_employee"], "rr-idle2")
            w = window_params(world.window)
            body = report(viewer["headers"], "employee-performance", **w)
            assert body["total"] == 3 and _find_row(viewer["headers"], idle["id"], **w)[0] is None      # E1, E2, the unassigned leads - nobody else
            assert dash(viewer["headers"], **w)["performance"]["total"] == 3
        finally:
            retire_roles([role])

    def test_narrowed_to_one_employee_there_is_no_team_to_list(self, world, m):
        narrowed = dash(m, employee_id=world.owner_id("e1"), **window_params(world.window))["performance"]
        assert narrowed["total"] == 1 and narrowed["items"][0]["employee"]["id"] == world.owner_id("e1")


# =============================================================================
# 4. Lead source performance
# =============================================================================
class TestSourceReport:
    def test_per_source_figures(self, world, m):
        body = report(m, "sources", **window_params(world.window))
        cohort = world.cohort()
        by = {row["source"]: row for row in body["items"]}
        for source in {s.source for s in cohort}:
            row = by[source]
            expected = figures([s for s in cohort if s.source == source])
            assert {k: row[k] for k in expected} == expected, source
        assert set(by) == {s.source for s in cohort} and by["facebook"]["leads"] == 4
        assert body["totals"] == figures(cohort)
        assert [r["leads"] for r in body["items"]] == sorted((r["leads"] for r in body["items"]), reverse=True)

    def test_filters(self, world, m):
        w = window_params(world.window)
        c1 = report(m, "sources", campaign_id=world.campaigns["C1"], **w)
        assert {r["source"]: r["leads"] for r in c1["items"]} == {"google_maps": 1, "facebook": 2, "referral": 1}      # L1 | L2 L7 | L4
        e2 = report(m, "sources", employee_id=world.owner_id("e2"), **w)
        assert {r["source"]: r["leads"] for r in e2["items"]} == {"facebook": 2, "website": 1}

    def test_an_empty_period(self, admin_h, m):
        body = report(m, "sources", **window_params(empty_window(admin_h)))
        assert body["items"] == [] and body["totals"]["leads"] == 0 and body["totals"]["conversion_rate"] is None


# =============================================================================
# 5. Campaign performance
# =============================================================================
class TestCampaignReport:
    def test_per_campaign_figures(self, world, m):
        body = report(m, "campaigns", **window_params(world.window))
        c1, c2 = world.campaigns["C1"], world.campaigns["C2"]
        assert [r["campaign_id"] for r in body["items"]] == [c1, c2] and body["total"] == 2          # the busiest first
        r1, r2 = body["items"]
        assert (r1["leads"], r1["won"], r1["lost"], r1["open"], r1["conversion_rate"]) == (4, 2, 0, 2, 50.0)
        assert (r1["value"], r1["won_value"]) == (1800.0, 1500.0)
        assert (r2["leads"], r2["won"], r2["lost"], r2["conversion_rate"]) == (2, 0, 1, 0.0)        # 0 won of 2 leads is 0 %, not "no data"
        assert body["no_campaign"] == {"leads": 5, "won": 0, "lost": 0}

    def test_a_campaign_filter_leaves_out_the_no_campaign_bucket(self, world, m):
        body = report(m, "campaigns", campaign_id=world.campaigns["C2"], **window_params(world.window))
        assert [r["campaign_id"] for r in body["items"]] == [world.campaigns["C2"]] and body["no_campaign"] is None

    def test_pagination_and_the_status_filter(self, admin_h, m, personas):
        window = empty_window(admin_h)
        w = window_params(window)
        owner = personas["employee"]["id"]
        made = {}
        for status in ("active", "paused", "completed"):
            campaign = create_campaign(m, status=status)
            made[status] = campaign["id"]
            for _ in range({"active": 3, "paused": 2, "completed": 1}[status]):              # 3, 2 and 1 leads
                lead = create_lead(m, campaign_id=campaign["id"])
                assign(m, lead["id"], owner)
                backdate_lead(lead["id"], window.lo + DAY + timedelta(hours=3))
        allc = report(m, "campaigns", **w)
        assert [r["campaign_id"] for r in allc["items"]] == [made["active"], made["paused"], made["completed"]] and allc["total"] == 3
        page2 = report(m, "campaigns", limit=2, offset=2, **w)
        assert [r["campaign_id"] for r in page2["items"]] == [made["completed"]] and page2["total"] == 3 and page2["limit"] == 2
        assert [r["campaign_id"] for r in report(m, "campaigns", limit=2, offset=0, **w)["items"]] == [made["active"], made["paused"]]
        for status, campaign_id in made.items():
            only = report(m, "campaigns", campaign_status=status, **w)
            assert [r["campaign_id"] for r in only["items"]] == [campaign_id] and only["total"] == 1 and only["items"][0]["status"] == status
            assert only["no_campaign"] is None
        assert report(m, "campaigns", campaign_status="archived", expect=422, **w).status_code == 422

    def test_an_empty_period(self, admin_h, m):
        body = report(m, "campaigns", **window_params(empty_window(admin_h)))
        assert body["items"] == [] and body["total"] == 0 and body["no_campaign"] == {"leads": 0, "won": 0, "lost": 0}


# =============================================================================
# 6. Conversion report
# =============================================================================
class TestConversionReport:
    def test_the_cohort_the_deals_closed_in_the_period_and_the_lost_reasons(self, world, m):
        body = report(m, "conversion", **window_params(world.window))
        assert body["cohort"] == {"leads": 11, "won": 2, "lost": 1, "open": 8, "conversion_rate": 18.2}
        # X3 was created long before the window but won inside it: a deal closed in the period, not part of the cohort
        assert body["closed_in_period"] == {"won": 3, "lost": 1}
        assert body["lost_reasons"] == [{"reason": "too_expensive", "leads": 1}]
        assert body["customers"] == 0                                               # nothing converted in this dataset

    def test_the_daily_series(self, world, m):
        series = report(m, "conversion", **window_params(world.window))["series"]
        d0 = world.window.start
        assert series["bucket"] == "day"
        assert [(p["start"], p["leads"], p["won"]) for p in series["points"]] == [
            (d0.isoformat(), 1, 0),                                                 # L12, on the first instant
            ((d0 + DAY).isoformat(), 9, 2),                                         # L1-L9 (2 won)
            ((d0 + 2 * DAY).isoformat(), 1, 0),                                     # L13, on the last instant
        ]

    def test_a_long_period_is_bucketed_by_week(self, world, m):
        # narrowed to E1, whose leads are all in this dataset - exact even if some other dataset sits near this window
        d0 = world.window.start
        series = report(m, "conversion", employee_id=world.owner_id("e1"), date_from=(d0 - timedelta(days=10)).isoformat(), date_to=(d0 + timedelta(days=40)).isoformat())["series"]
        assert series["bucket"] == "week" and 1 <= len(series["points"]) <= 2
        assert all(date.fromisoformat(p["start"]).weekday() == 0 for p in series["points"])            # weeks start on Monday
        # E1's leads in [D0-10, D0+40]: X1 (day before), L12, L1-L5, L13, X2 (day after) - X3 was created 30 days before
        assert sum(p["leads"] for p in series["points"]) == 9 and sum(p["won"] for p in series["points"]) == 1

    def test_filters(self, world, m):
        w = window_params(world.window)
        c1 = report(m, "conversion", campaign_id=world.campaigns["C1"], **w)
        assert c1["cohort"] == {"leads": 4, "won": 2, "lost": 0, "open": 2, "conversion_rate": 50.0}
        assert c1["closed_in_period"] == {"won": 3, "lost": 0}                       # L4, L7 and X3 - all in C1
        e2 = report(m, "conversion", employee_id=world.owner_id("e2"), **w)
        assert e2["cohort"]["leads"] == 3 and e2["closed_in_period"] == {"won": 1, "lost": 0}
        assert report(m, "conversion", source="cold_call", **w)["lost_reasons"] == [{"reason": "too_expensive", "leads": 1}]

    def test_an_empty_period_has_no_rate_no_reasons_no_points(self, admin_h, m):
        body = report(m, "conversion", **window_params(empty_window(admin_h)))
        assert body["cohort"] == {"leads": 0, "won": 0, "lost": 0, "open": 0, "conversion_rate": None}
        assert body["closed_in_period"] == {"won": 0, "lost": 0} and body["lost_reasons"] == [] and body["series"]["points"] == []


# =============================================================================
# 7. Activity report
# =============================================================================
class TestActivityReport:
    def test_per_kind_totals_and_breakdowns(self, world, m):
        body = report(m, "activities", **window_params(world.window))
        k = body["kinds"]
        assert body["activities"] == 12
        assert (k["calls"]["total"], k["followups"]["total"], k["demos"]["total"], k["trials"]["total"]) == (5, 3, 2, 2)
        assert k["calls"]["breakdown"] == {"answered": 2, "no_answer": 1, "busy": 0, "wrong_number": 1, "interested": 1, "not_interested": 0, "call_back": 0}
        # cancelled work is shown in the breakdown but is not part of the total
        assert k["followups"]["breakdown"] == {"pending": 2, "completed": 1, "cancelled": 1}
        assert k["demos"]["breakdown"] == {"scheduled": 1, "completed": 1, "cancelled": 1, "no_show": 0}
        assert k["trials"]["breakdown"] == {"active": 1, "completed": 1, "cancelled": 0}

    def test_per_employee_rows(self, world, m):
        body = report(m, "activities", **window_params(world.window))
        e1, e2 = world.owner_id("e1"), world.owner_id("e2")
        assert [r["employee"]["id"] for r in body["items"]] == [e1, e2] and body["total"] == 2
        r1, r2 = body["items"]
        assert (r1["calls"], r1["followups"], r1["demos"], r1["trials"], r1["activities"]) == (3, 2, 1, 1, 7)
        assert (r2["calls"], r2["followups"], r2["demos"], r2["trials"], r2["activities"]) == (2, 1, 1, 1, 5)

    def test_pagination(self, world, m):
        w = window_params(world.window)
        first = report(m, "activities", limit=1, offset=0, **w)
        second = report(m, "activities", limit=1, offset=1, **w)
        assert [p["items"][0]["employee"]["id"] for p in (first, second)] == [world.owner_id("e1"), world.owner_id("e2")]
        assert first["total"] == 2 and first["activities"] == 12                    # the totals are of everyone, not of the page

    def test_narrowed_to_one_employee(self, world, m):
        body = report(m, "activities", employee_id=world.owner_id("e2"), **window_params(world.window))
        assert body["activities"] == 5 and [r["employee"]["id"] for r in body["items"]] == [world.owner_id("e2")]
        assert body["kinds"]["calls"]["total"] == 2 and body["employee"]["id"] == world.owner_id("e2")

    def test_an_employee_sees_only_their_own_work(self, world, personas):
        body = report(personas["employee"]["headers"], "activities", **window_params(world.window))
        assert body["scope"] == "personal" and body["activities"] == 7
        assert [r["employee"]["id"] for r in body["items"]] == [world.owner_id("e1")] and body["total"] == 1

    def test_an_empty_period(self, admin_h, m):
        body = report(m, "activities", **window_params(empty_window(admin_h)))
        assert body["activities"] == 0 and body["items"] == [] and body["total"] == 0
        assert all(kind["total"] == 0 for kind in body["kinds"].values())
