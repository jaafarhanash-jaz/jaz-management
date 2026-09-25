"""JAZ Sales - Phase 5: the Sales dashboard (HTTP integration tier).

Manager / Super Admin (team) numbers against a known dataset in an empty far-past window, an employee's personal numbers, the
period presets and their validation, the boundary instants of a period, and empty datasets. WHO may see what is in
test_sales_reports_rbac.py; the reports are in test_sales_reports_reports.py.

The expected numbers are computed by `World`'s Python oracle from the dataset that was created (sales_reports_test_utils.py),
never by the code under test.
"""
import calendar
from datetime import date, timedelta

import pytest

from sales_reports_test_utils import (
    DAY,
    api,
    assign,
    backdate_lead,
    build_world,
    count_stage,
    create_call,
    create_demo,
    create_followup,
    create_lead,
    create_staff,
    create_trial,
    dash,
    empty_window,
    errors_of,
    iso,
    report,
    later,
    make_personas,
    person_row,
    app_today,
    utc,
    value_sum,
    window_params,
    wind_down,
)
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401

STAGES = ["new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won", "lost"]
KPI_FIELDS = {
    "total_leads", "new_leads", "assigned_leads", "contacted_leads", "interested_leads", "demos", "negotiations", "won", "lost",
    "conversion_rate", "active_trials", "upcoming_demos", "overdue_followups", "upcoming_followups",
}


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def personas(admin_h):
    return make_personas(admin_h, "rd")


@pytest.fixture(scope="module")
def world(admin_h, personas):
    made = build_world(admin_h, personas["manager"], personas["employee"], personas["employee2"])
    yield made
    wind_down(personas["manager"]["headers"], made.open_items)          # leave no open work in the shared scratch database


def figures(leads):
    n, won, lost = len(leads), count_stage(leads, "won"), count_stage(leads, "lost")
    return {
        "leads": n, "won": won, "lost": lost, "open": n - won - lost,
        "conversion_rate": round(won * 100.0 / n, 1) if n else None,
        "value": value_sum(leads), "won_value": value_sum([s for s in leads if s.stage == "won"]),
    }


# =============================================================================
# team dashboard (Sales Manager and Super Admin)
# =============================================================================
class TestTeamDashboard:
    def test_the_dataset_is_what_the_oracle_says(self, world):
        cohort = world.cohort()
        # 11 in the window: L1-L9, L12 (the first instant of the window) and L13 (the last); X1 / X2 are one instant outside,
        # X3 was created long before, A1 is archived
        assert sorted(s.key for s in cohort) == sorted(["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L12", "L13"])

    @pytest.mark.parametrize("who", ["manager", "admin"])
    def test_kpis_match_the_dataset_exactly(self, world, personas, admin_h, who):
        headers = personas["manager"]["headers"] if who == "manager" else admin_h
        body = dash(headers, **window_params(world.window))
        cohort = world.cohort()
        k = body["kpis"]
        assert set(k) == KPI_FIELDS
        assert k["total_leads"] == 11
        assert k["new_leads"] == count_stage(cohort, "new") == 1
        assert k["assigned_leads"] == count_stage(cohort, "assigned") == 4          # L1, L6, L12, L13
        assert k["contacted_leads"] == count_stage(cohort, "contacted") == 1
        assert k["interested_leads"] == count_stage(cohort, "interested") == 1
        assert k["demos"] == count_stage(cohort, "demo_scheduled", "demo_completed") == 1
        assert k["negotiations"] == 0
        assert k["won"] == count_stage(cohort, "won") == 2 and k["lost"] == count_stage(cohort, "lost") == 1
        assert k["conversion_rate"] == 18.2                                          # 2 won / 11 leads that entered the period
        assert body["scope"] == "team" and body["employee"] is None

    def test_the_period_is_echoed(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        assert body["period"] == {"range": "custom", "date_from": world.window.start.isoformat(), "date_to": world.window.end.isoformat(), "days": 3}

    def test_pipeline_counts_and_values_by_current_stage_add_up_to_the_total(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        cohort = world.cohort()
        assert [row["stage"] for row in body["pipeline"]] == STAGES              # every stage, in board order, empty ones as zero
        for row in body["pipeline"]:
            in_stage = [s for s in cohort if s.stage == row["stage"]]
            assert row["leads"] == len(in_stage), row["stage"]
            assert row["value"] == value_sum(in_stage), row["stage"]
            assert row["valued"] == sum(1 for s in in_stage if s.value is not None), row["stage"]
        assert sum(row["leads"] for row in body["pipeline"]) == body["kpis"]["total_leads"]
        assert sum(row["value"] for row in body["pipeline"]) == value_sum(cohort)

    def test_a_lead_on_the_first_or_last_instant_of_the_period_counts_and_one_instant_outside_does_not(self, world, personas):
        m = personas["manager"]["headers"]
        w = world.window
        # L12 is created at 00:00:00.000000 of the first day, L13 at 23:59:59.999999 of the last day (both inside);
        # X1 the day before, X2 exactly at 00:00:00 of the day AFTER (both outside)
        first = dash(m, date_from=w.start.isoformat(), date_to=w.start.isoformat())["kpis"]["total_leads"]
        last = dash(m, date_from=w.end.isoformat(), date_to=w.end.isoformat())["kpis"]["total_leads"]
        before = dash(m, date_from=(w.start - DAY).isoformat(), date_to=(w.start - DAY).isoformat())["kpis"]["total_leads"]
        after = dash(m, date_from=(w.end + DAY).isoformat(), date_to=(w.end + DAY).isoformat())["kpis"]["total_leads"]
        assert (first, last, before, after) == (1, 1, 1, 1)                     # L12 | L13 | X1 | X2 - each in exactly one day
        assert dash(m, date_from=w.start.isoformat(), date_to=w.end.isoformat())["kpis"]["total_leads"] == 11

    def test_archived_leads_and_their_work_are_counted_nowhere(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        assert world.leads["A1"].archived and not world.in_cohort(world.leads["A1"])
        assert body["kpis"]["total_leads"] == 11                                   # A1 (assigned, in the window) is not in it
        # ... and neither is the call E1 logged on it before it was archived: 3 calls of E1 in the window + 2 of E2
        assert body["activity"]["calls"] == 5

    def test_activity_is_calls_followups_demos_trials_without_cancelled_work(self, world, personas):
        a = dash(personas["manager"]["headers"], **window_params(world.window))["activity"]
        # calls 3+2; follow-ups 2 pending + 1 completed (the cancelled one is NOT activity); demos 1 scheduled + 1 completed
        # (the cancelled one is not); trials 1 active + 1 completed
        assert a == {"calls": 5, "followups": 3, "demos": 2, "trials": 2, "total": 12}

    def test_performance_rows_per_employee(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        rows = body["performance"]["items"]
        e1, e2 = world.owner_id("e1"), world.owner_id("e2")
        r1, r2, none = person_row(rows, e1), person_row(rows, e2), person_row(rows, None)
        # E1, E2 and the unassigned leads come first (the busiest); after them only staff with NOTHING to report - idle employees
        # are listed, at the end, as zero rows (the manager did nothing here and is not among them: managers hold no leads role)
        assert body["performance"]["total"] >= 3
        assert [(r["employee"] or {"id": None})["id"] for r in rows[:3]] == [e1, e2, None]
        assert all((r["leads"], r["activities"]) == (0, 0) for r in rows[3:])
        # E1: L1 L2 L3 L4 L5 L12 L13 = 7 leads, 1 won, 1 lost; 3 calls + 2 follow-ups + 1 demo + 1 trial
        assert (r1["leads"], r1["won"], r1["lost"], r1["open"], r1["conversion_rate"]) == (7, 1, 1, 5, 14.3)
        assert (r1["calls"], r1["followups"], r1["demos"], r1["trials"], r1["activities"]) == (3, 2, 1, 1, 7)
        # E2: L6 L7 L8 = 3 leads, 1 won; 2 calls + 1 follow-up + 1 demo + 1 trial
        assert (r2["leads"], r2["won"], r2["lost"], r2["open"], r2["conversion_rate"]) == (3, 1, 0, 2, 33.3)
        assert (r2["calls"], r2["followups"], r2["demos"], r2["trials"], r2["activities"]) == (2, 1, 1, 1, 5)
        # nobody's leads: L9 - no activity, no employee
        assert none["employee"] is None and (none["leads"], none["won"], none["activities"]) == (1, 0, 0)
        assert [r["leads"] for r in rows] == sorted((r["leads"] for r in rows), reverse=True)     # the busiest first

    def test_sources(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        cohort = world.cohort()
        by_source = {row["source"]: row for row in body["sources"]}
        expected = {}
        for s in cohort:
            expected.setdefault(s.source, []).append(s)
        assert set(by_source) == set(expected)
        for source, leads in expected.items():
            row = by_source[source]
            assert {k: row[k] for k in ("leads", "won", "lost", "open", "conversion_rate", "value", "won_value")} == figures(leads), source
        assert by_source["facebook"]["leads"] == 4 and by_source["facebook"]["won"] == 1     # L2 L3 L6 L7; L7 won
        assert by_source["facebook"]["name_en"] == "Facebook" and by_source["facebook"]["name_ar"]
        assert [r["leads"] for r in body["sources"]] == sorted((r["leads"] for r in body["sources"]), reverse=True)

    def test_campaigns(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        c1, c2 = world.campaigns["C1"], world.campaigns["C2"]
        rows = {row["campaign_id"]: row for row in body["campaigns"]["items"]}
        assert set(rows) == {c1, c2} and body["campaigns"]["total"] == 2
        # C1: L1 L2 L4 L7 (2 won: L4, L7);  C2: L5 L6 (1 lost)
        assert (rows[c1]["leads"], rows[c1]["won"], rows[c1]["lost"], rows[c1]["value"], rows[c1]["won_value"]) == (4, 2, 0, 1800.0, 1500.0)
        assert (rows[c2]["leads"], rows[c2]["won"], rows[c2]["lost"]) == (2, 0, 1)
        assert rows[c1]["source"] == "facebook" and rows[c1]["status"] == "active" and rows[c1]["name"]
        # L3 L8 L9 L12 L13 have no campaign
        assert body["campaigns"]["no_campaign"] == {"leads": 5, "won": 0, "lost": 0}

    def test_every_number_states_its_basis(self, world, personas):
        body = dash(personas["manager"]["headers"], **window_params(world.window))
        assert set(body["kpis"]) - {"conversion_rate"} <= set(body["bases"])
        assert body["bases"]["conversion_rate"] == "cohort" and body["bases"]["overdue_followups"] == "now" and body["bases"]["activity"] == "event"
        assert set(body["bases"].values()) == {"cohort", "event", "now"}

    def test_a_team_dashboard_narrowed_to_one_employee_is_that_employees_dashboard(self, world, personas):
        m, e1 = personas["manager"]["headers"], personas["employee"]
        narrowed = dash(m, employee_id=e1["id"], **window_params(world.window))
        own = dash(e1["headers"], **window_params(world.window))
        assert narrowed["scope"] == "team" and narrowed["employee"]["id"] == e1["id"] and own["scope"] == "personal"
        for key in ("kpis", "pipeline", "sources", "activity"):
            assert narrowed[key] == own[key], key
        assert narrowed["kpis"]["total_leads"] == 7
        # the employee's onboarding numbers are not a salesperson's: the section is left out when somebody is singled out
        assert narrowed["onboarding"] is None


# =============================================================================
# an employee's own dashboard
# =============================================================================
class TestEmployeeDashboard:
    def test_my_numbers_are_exactly_mine(self, world, personas):
        e1 = personas["employee"]
        body = dash(e1["headers"], **window_params(world.window))
        mine = world.cohort(owner="e1")
        k = body["kpis"]
        assert body["scope"] == "personal" and body["employee"] is None
        assert k["total_leads"] == len(mine) == 7
        assert (k["assigned_leads"], k["contacted_leads"], k["interested_leads"], k["won"], k["lost"]) == (3, 1, 1, 1, 1)   # L1 L12 L13 | L2 | L3 | L4 | L5
        assert k["conversion_rate"] == 14.3
        assert body["activity"] == {"calls": 3, "followups": 2, "demos": 1, "trials": 1, "total": 7}

    def test_my_now_readings(self, world, personas):
        k = dash(personas["employee"]["headers"], **window_params(world.window))["kpis"]
        # pending follow-ups: 2 in the window + 1 forty days earlier are overdue, 1 due tomorrow is upcoming (a cancelled one is neither)
        assert (k["overdue_followups"], k["upcoming_followups"]) == (3, 1)
        assert k["upcoming_demos"] == 1                                             # the one 3 days ahead (the past scheduled one is not "upcoming")
        assert k["active_trials"] == 1

    def test_now_readings_do_not_depend_on_the_period(self, world, personas):
        h = personas["employee"]["headers"]
        inside = dash(h, **window_params(world.window))["kpis"]
        today = dash(h, range="today")["kpis"]
        for key in ("overdue_followups", "upcoming_followups", "upcoming_demos", "active_trials"):
            assert inside[key] == today[key], key
        assert today["total_leads"] == 0 and today["conversion_rate"] is None       # ... while the cohort numbers do

    def test_another_employees_numbers_are_not_in_mine(self, world, personas):
        body = dash(personas["employee2"]["headers"], **window_params(world.window))
        assert body["kpis"]["total_leads"] == 3                                     # L6 L7 L8 - none of E1's seven, not the unassigned one
        assert body["kpis"]["won"] == 1 and body["activity"]["total"] == 5
        assert sorted(row["source"] for row in body["sources"]) == ["facebook", "website"]
        assert body["campaigns"]["no_campaign"]["leads"] == 1                      # L8 only

    def test_no_team_comparison_for_an_employee(self, world, personas):
        body = dash(personas["employee"]["headers"], **window_params(world.window))
        assert body["performance"] is None                                          # comparing people is a team privilege
        assert body["onboarding"] is None                                           # a Sales Employee holds no onboarding permission

    def test_asking_about_myself_is_fine(self, world, personas):
        e1 = personas["employee"]
        plain = dash(e1["headers"], **window_params(world.window))
        for value in ("me", e1["id"]):
            assert dash(e1["headers"], employee_id=value, **window_params(world.window))["kpis"] == plain["kpis"]


# =============================================================================
# the period: presets, custom ranges, validation
# =============================================================================
def _month_bounds(day: date):
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


class TestPeriod:
    def test_the_default_is_this_month(self, personas):
        body = dash(personas["employee"]["headers"])
        first, last = _month_bounds(app_today())
        assert body["period"]["range"] == "this_month"
        assert (body["period"]["date_from"], body["period"]["date_to"], body["period"]["days"]) == (first.isoformat(), last.isoformat(), (last - first).days + 1)

    def test_the_presets(self, personas):
        h = personas["employee"]["headers"]
        today = app_today()
        monday = today - timedelta(days=today.weekday())
        first, last = _month_bounds(today)
        prev_last = first - DAY
        prev_first = prev_last.replace(day=1)
        expected = {
            "today": (today, today),
            "this_week": (monday, monday + timedelta(days=6)),
            "this_month": (first, last),
            "last_month": (prev_first, prev_last),
        }
        for name, (lo, hi) in expected.items():
            p = dash(h, range=name)["period"]
            assert (p["range"], p["date_from"], p["date_to"]) == (name, lo.isoformat(), hi.isoformat()), name

    def test_dates_alone_mean_a_custom_range(self, personas):
        p = dash(personas["employee"]["headers"], date_from="2026-01-01", date_to="2026-01-31")["period"]
        assert p == {"range": "custom", "date_from": "2026-01-01", "date_to": "2026-01-31", "days": 31}

    def test_a_year_is_the_longest_period(self, personas):
        h = personas["employee"]["headers"]
        assert dash(h, date_from="2025-01-01", date_to="2025-12-31")["period"]["days"] == 365
        assert dash(h, date_from="2024-01-01", date_to="2024-12-31")["period"]["days"] == 366      # a leap year
        assert errors_of(dash(h, expect=400, date_from="2025-01-01", date_to="2026-01-02"))["code"] == "period_too_long"      # 367 days

    @pytest.mark.parametrize("params,code,field", [
        ({"range": "custom"}, "period_incomplete", "date_from"),
        ({"range": "custom", "date_from": "2026-01-01"}, "period_incomplete", "date_to"),
        ({"date_to": "2026-01-01"}, "period_incomplete", "date_from"),
        ({"date_from": "2026-02-01", "date_to": "2026-01-01"}, "period_reversed", "date_to"),
        ({"range": "today", "date_from": "2026-01-01", "date_to": "2026-01-02"}, "period_conflict", "date_from"),
        ({"range": "this_week", "date_from": "2026-01-01"}, "period_conflict", "date_from"),
        ({"range": "next_year"}, "invalid_range", "range"),
        ({"date_from": "1999-12-31", "date_to": "2000-01-05"}, "period_out_of_bounds", "date_from"),
        ({"date_from": "2099-12-30", "date_to": "2100-01-02"}, "period_out_of_bounds", "date_from"),
    ])
    def test_bad_periods_are_400_in_the_usual_error_shape(self, personas, params, code, field):
        r = dash(personas["employee"]["headers"], expect=400, **params)
        detail = errors_of(r)
        assert detail["code"] == code and detail["field"] == field and detail["message"]

    def test_an_unparseable_date_is_a_validation_error(self, personas):
        r = dash(personas["employee"]["headers"], expect=422, date_from="not-a-date", date_to="2026-01-01")
        assert r.status_code == 422

    def test_a_malformed_employee_id_is_a_400(self, personas):
        detail = errors_of(dash(personas["manager"]["headers"], expect=400, employee_id="not-a-uuid"))
        assert detail["field"] == "employee_id"

    def test_the_presets_pick_up_leads_by_their_creation_date(self, admin_h):
        """A fresh employee with one lead created now, one moved to the middle of last month and one to the middle of
        this month's start: exactly the right period sees each."""
        emp = create_staff(admin_h, ["sales_employee"], "rd-preset")
        mgr = create_staff(admin_h, ["sales_manager"], "rd-preset-mgr")["headers"]
        today = app_today()
        first, _ = _month_bounds(today)
        prev_last = first - DAY
        mid_last_month = utc(prev_last.year, prev_last.month, 15, 12)

        def lead(created=None):
            made = create_lead(mgr)
            assign(mgr, made["id"], emp["id"])
            if created:
                backdate_lead(made["id"], created)
            return made

        lead()                                              # now
        lead(mid_last_month)
        h = emp["headers"]
        assert dash(h, range="today")["kpis"]["total_leads"] == 1
        assert dash(h, range="this_month")["kpis"]["total_leads"] == 1
        assert dash(h, range="last_month")["kpis"]["total_leads"] == 1
        assert dash(h, date_from=(prev_last.replace(day=1)).isoformat(), date_to=today.isoformat())["kpis"]["total_leads"] == 2


# =============================================================================
# no data
# =============================================================================
class TestEmptyDatasets:
    def test_an_empty_period_is_all_zeros_and_no_data_is_not_zero_percent(self, admin_h, personas):
        window = empty_window(admin_h)
        body = dash(personas["manager"]["headers"], **window_params(window))
        k = body["kpis"]
        assert k["total_leads"] == 0 and k["won"] == 0 and k["lost"] == 0 and k["new_leads"] == 0
        assert k["conversion_rate"] is None                                         # a rate over nothing is "no data", never 0 %
        assert [(r["stage"], r["leads"], r["value"]) for r in body["pipeline"]] == [(s, 0, 0.0) for s in STAGES]
        # nothing happened - but the team is still listed: every row is a zero row (an idle employee is not a missing one)
        assert body["performance"]["total"] >= len(body["performance"]["items"])
        assert all((r["leads"], r["won"], r["lost"], r["activities"], r["conversion_rate"]) == (0, 0, 0, 0, None) for r in body["performance"]["items"])
        assert body["sources"] == []
        assert body["campaigns"] == {"items": [], "total": 0, "no_campaign": {"leads": 0, "won": 0, "lost": 0}}
        assert body["activity"] == {"calls": 0, "followups": 0, "demos": 0, "trials": 0, "total": 0}

    def test_a_brand_new_employee_sees_zeros(self, admin_h):
        body = dash(create_staff(admin_h, ["sales_employee"], "rd-blank")["headers"], range="this_month")
        k = body["kpis"]
        assert (k["total_leads"], k["won"], k["conversion_rate"]) == (0, 0, None)
        assert (k["overdue_followups"], k["upcoming_followups"], k["upcoming_demos"], k["active_trials"]) == (0, 0, 0, 0)
        assert body["activity"]["total"] == 0 and body["sources"] == [] and body["campaigns"]["items"] == []


# =============================================================================
# whose work it is - and whether it is still theirs to see
# =============================================================================
class TestWhoseWorkItIs:
    """Activity is counted for whoever did or owns it (a call: who made it; a follow-up or demo: its assignee; a trial: who
    started it) and only while the lead is one the caller may open. The two rules are different questions, and a dataset in
    which only a lead's owner ever works it cannot tell them apart - so this one has a manager working an employee's lead, and
    then the lead moving to somebody else."""

    def _window_numbers(self, headers, window, **extra):
        body = dash(headers, **window_params(window), **extra)
        return body["activity"], body["kpis"]

    def test_the_work_a_manager_did_on_my_lead_is_not_mine(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        window = empty_window(admin_h)
        ea = create_staff(admin_h, ["sales_employee"], "rd-work-ea")
        t = window.lo + DAY + timedelta(hours=10)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], ea["id"])
        create_call(ea["headers"], lead["id"], result="answered", called_at=iso(t))              # Ea's own call
        create_call(mgr, lead["id"], result="busy", called_at=iso(t))                             # the manager's calls on Ea's lead
        create_call(mgr, lead["id"], result="no_answer", called_at=iso(t))
        opened = [                                                                                # (wound down at the end, below)
            ("followups", create_followup(mgr, lead["id"], due_at=iso(t))["id"]),                   # for the manager
            ("demos", create_demo(mgr, lead["id"], scheduled_at=iso(t))["id"]),                     # given by the manager
            ("trials", create_trial(mgr, lead["id"], started_at=iso(t), expected_end_at=later(days=10))["id"]),   # started by the manager
        ]

        mine, kpis = self._window_numbers(ea["headers"], window)
        assert mine == {"calls": 1, "followups": 0, "demos": 0, "trials": 0, "total": 1}          # only the one call Ea made
        assert kpis["overdue_followups"] == 0                                                      # the follow-up is the manager's
        assert kpis["active_trials"] == 1                                                          # ... but a trial belongs to its LEAD, whoever started it
        narrowed, _ = self._window_numbers(mgr, window, employee_id=ea["id"])
        assert narrowed == mine                                                                    # a manager looking at Ea sees Ea's numbers
        team, _ = self._window_numbers(mgr, window)
        assert team == {"calls": 3, "followups": 1, "demos": 1, "trials": 1, "total": 6}
        rows = dash(mgr, **window_params(window))["performance"]["items"]
        assert person_row(rows, ea["id"])["activities"] == 1 and person_row(rows, personas["manager"]["id"])["activities"] == 5
        wind_down(mgr, opened)

    def test_a_lead_that_moves_to_a_colleague_takes_its_work_out_of_my_numbers(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        window = empty_window(admin_h)
        ea = create_staff(admin_h, ["sales_employee"], "rd-move-ea")
        eb = create_staff(admin_h, ["sales_employee"], "rd-move-eb")
        t = window.lo + DAY + timedelta(hours=10)
        past = iso(window.lo - 40 * DAY)
        lead = create_lead(mgr)
        assign(mgr, lead["id"], ea["id"])
        create_call(ea["headers"], lead["id"], result="answered", called_at=iso(t))              # in the window (an event)
        opened = [
            ("followups", create_followup(ea["headers"], lead["id"], due_at=past)["id"]),          # overdue now
            ("demos", create_demo(ea["headers"], lead["id"], scheduled_at=later(days=2))["id"]),   # upcoming now
            ("trials", create_trial(ea["headers"], lead["id"], expected_end_at=later(days=10))["id"]),   # active now
        ]

        before, kpis = self._window_numbers(ea["headers"], window)
        assert before["calls"] == 1 and (kpis["overdue_followups"], kpis["upcoming_demos"], kpis["active_trials"]) == (1, 1, 1)

        assign(mgr, lead["id"], eb["id"])                                                         # the lead moves to Eb

        after, kpis = self._window_numbers(ea["headers"], window)
        # Ea can no longer open the lead, so nothing about it may reach Ea's numbers - not the call they made, not what they own
        assert after == {"calls": 0, "followups": 0, "demos": 0, "trials": 0, "total": 0}
        assert (kpis["overdue_followups"], kpis["upcoming_followups"], kpis["upcoming_demos"], kpis["active_trials"]) == (0, 0, 0, 0)
        # Eb owns the lead now but did none of that work: no activity, no follow-up / demo (they are Ea's) - but the trial
        # follows the lead
        eb_activity, eb_kpis = self._window_numbers(eb["headers"], window)
        assert eb_activity["total"] == 0 and (eb_kpis["overdue_followups"], eb_kpis["upcoming_demos"], eb_kpis["active_trials"]) == (0, 0, 1)
        # the team still sees the records, under the people they belong to
        team_ea, team_kpis = self._window_numbers(mgr, window, employee_id=ea["id"])
        assert team_ea["calls"] == 1 and (team_kpis["overdue_followups"], team_kpis["upcoming_demos"]) == (1, 1)
        # ... and a trial belongs to its LEAD: Ea started it, but the lead is Eb's now, so a manager looking at Eb sees it there
        assert team_kpis["active_trials"] == 0
        assert self._window_numbers(mgr, window, employee_id=eb["id"])[1]["active_trials"] == 1
        wind_down(mgr, opened)


# =============================================================================
# a day is a BAGHDAD day - for the dashboard, the lists behind it and the trend chart alike
# =============================================================================
class TestTheDayIsABaghdadDay:
    """Stored timestamps are UTC; what a calendar DATE means is decided in one place (sales/timezone.py): the day that began at
    midnight in Baghdad (UTC+3). Instants are placed either side of that midnight - two of them on a different UTC date than
    their Baghdad date - and every reader must put them on the same day."""

    def test_the_dashboard_the_lead_list_and_the_trend_agree_on_what_day_a_lead_was_created(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        window = empty_window(admin_h)
        d = window.lo + DAY                                     # midnight at the start of the window's second day (Baghdad), i.e. D0+1 00:00 local
        instants = {
            "before": d - timedelta(microseconds=1),            # 23:59:59.999999 on D0     (20:59:59.999999 UTC)
            "early": d + timedelta(hours=1, minutes=30),        # 01:30 on D0+1 local       = 22:30 UTC on D0: a DIFFERENT UTC date
            "late": d + timedelta(hours=23, minutes=30),        # 23:30 on D0+1 local       = 20:30 UTC on D0+1
            "next": d + DAY,                                    # 00:00 on D0+2 local       = 21:00 UTC on D0+1: a DIFFERENT UTC date
        }
        made = {}
        for name, when in instants.items():
            lead = create_lead(mgr)
            backdate_lead(lead["id"], when)
            made[name] = lead["id"]
        d0, d1, d2 = window.start, window.start + DAY, window.start + 2 * DAY

        def total(day):
            return dash(mgr, date_from=day.isoformat(), date_to=day.isoformat())["kpis"]["total_leads"]

        assert (total(d0), total(d1), total(d2)) == (1, 2, 1)   # before | early + late | next  (UTC dates would give 2 / 2 / 0)

        def listed(day):
            r = api("GET", f"/api/sales/leads?created_from={day}&created_to={day}&limit=100", mgr)
            assert r.status_code == 200
            return {item["id"] for item in r.json()["items"]}

        assert listed(d1) == {made["early"], made["late"]}       # the list behind the number says the same
        assert listed(d0) == {made["before"]} and listed(d2) == {made["next"]}
        points = report(mgr, "conversion", **window_params(window))["series"]["points"]
        assert [(p["start"], p["leads"]) for p in points] == [(d0.isoformat(), 1), (d1.isoformat(), 2), (d2.isoformat(), 1)]

    def test_calls_made_either_side_of_midnight_are_on_the_right_day_in_the_list_and_the_activity_count(self, admin_h, personas):
        mgr = personas["manager"]["headers"]
        emp = create_staff(admin_h, ["sales_employee"], "rd-tz-emp")
        window = empty_window(admin_h)
        d = window.lo + DAY
        lead = create_lead(mgr)
        assign(mgr, lead["id"], emp["id"])
        create_call(emp["headers"], lead["id"], result="answered", called_at=iso(d + timedelta(hours=23, minutes=30)))   # 23:30 local on D
        create_call(emp["headers"], lead["id"], result="busy", called_at=iso(d + DAY))                                      # 00:00 local on D+1
        day, nxt = (window.start + DAY).isoformat(), (window.start + 2 * DAY).isoformat()

        def calls_on(day_):
            r = api("GET", f"/api/sales/calls?called_from={day_}&called_to={day_}&limit=100", emp["headers"])
            assert r.status_code == 200
            return [c["result"] for c in r.json()["items"]]

        assert calls_on(day) == ["answered"] and calls_on(nxt) == ["busy"]
        for day_, expected in ((day, 1), (nxt, 1)):
            assert dash(emp["headers"], date_from=day_, date_to=day_)["activity"]["calls"] == expected
