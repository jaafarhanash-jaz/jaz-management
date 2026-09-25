"""JAZ Sales - Phase 5: the onboarding report and the dashboard's onboarding section (HTTP integration tier).

Onboarding numbers follow the ONBOARDING scope (all records, or only the ones assigned to the caller), not the lead scope: an
Onboarding Employee sees their own records' figures and nothing about anybody else's. The fixture converts five leads
(each conversion creates a real JAZ company in the scratch database) and hands them to two onboarding workers.

  R1  assigned to W, moved to `contacted`             started yesterday
  R2  assigned to W, moved to `activated` (now)       started yesterday
  R3  not assigned to anybody (stage `won`)           started yesterday
  R4  assigned to W (stage `assigned`)                started in 2005 - outside every period used below
  R5  assigned to W2, moved to `contacted`            started yesterday
"""
import pytest

from sales_customer_test_utils import (
    assign_onboarding,
    converted,
    onboarding_id,
    onboarding_worker,
    set_onboarding_stage,
)
from sales_reports_test_utils import DAY, backdate_onboarding, create_staff, dash, errors_of, report, app_today, utc
from sales_test_utils import admin_token, assert_scratch_target, auth  # noqa: F401

STAGES = ["won", "assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def mgr(admin_h):
    return create_staff(admin_h, ["sales_manager"], "ro-manager")["headers"]


@pytest.fixture(scope="module")
def setup(admin_h, mgr):
    employee = create_staff(admin_h, ["sales_employee"], "ro-employee")
    w, w2 = onboarding_worker(admin_h, "ro-w"), onboarding_worker(admin_h, "ro-w2")
    yesterday = utc(app_today().year, app_today().month, app_today().day, 12) - DAY
    records = {}
    for key in ("R1", "R2", "R3", "R4", "R5"):
        made = converted(mgr, employee["id"])
        records[key] = {"oid": onboarding_id(mgr, made["customer"]["id"]), "name": made["lead"]["business_name"]}
    for key in ("R1", "R2", "R4"):
        assign_onboarding(mgr, records[key]["oid"], w["id"])
    assign_onboarding(mgr, records["R5"]["oid"], w2["id"])
    set_onboarding_stage(mgr, records["R1"]["oid"], "contacted")
    set_onboarding_stage(mgr, records["R2"]["oid"], "activated")
    set_onboarding_stage(mgr, records["R5"]["oid"], "contacted")
    for key in ("R1", "R2", "R3", "R5"):
        backdate_onboarding(records[key]["oid"], yesterday)
    backdate_onboarding(records["R4"]["oid"], utc(2005, 6, 15, 12))
    today = app_today()
    period = {"date_from": (today - 3 * DAY).isoformat(), "date_to": (today + DAY).isoformat()}
    return {"w": w, "w2": w2, "r": records, "period": period, "employee": employee}


def oids(page):
    return [item["id"] for item in page["items"]]


# =============================================================================
# the manager's view
# =============================================================================
class TestOnboardingReportForTheTeam:
    def test_one_workers_records_by_stage_and_owner(self, mgr, setup):
        r = setup["r"]
        body = report(mgr, "onboarding", assigned_to=setup["w"]["id"], **setup["period"])
        # R1 and R2 started in the period; R4 (2005) did not, R3 / R5 are not W's
        assert body["scope"] == "team" and body["total"] == 2
        assert set(oids(body)) == {r["R1"]["oid"], r["R2"]["oid"]}
        s = body["summary"]
        assert (s["total"], s["open"], s["activated"], s["unassigned"]) == (2, 1, 1, 0)
        assert s["by_stage"] == {**{stage: 0 for stage in STAGES}, "contacted": 1, "activated": 1}
        assert [(o["employee"]["id"], o["total"], o["open"], o["activated"]) for o in body["by_owner"]] == [(setup["w"]["id"], 2, 1, 1)]

    def test_a_row_names_the_customer_the_stage_and_the_owner(self, mgr, setup):
        r = setup["r"]
        body = report(mgr, "onboarding", assigned_to=setup["w"]["id"], **setup["period"])
        row = next(i for i in body["items"] if i["id"] == r["R2"]["oid"])
        assert set(row) == {"id", "business_name", "stage", "assigned_to", "started_at", "completed_at"}
        assert row["business_name"] == r["R2"]["name"] and row["stage"] == "activated" and row["assigned_to"]["id"] == setup["w"]["id"]
        assert row["completed_at"] is not None
        contacted = next(i for i in body["items"] if i["id"] == r["R1"]["oid"])
        assert contacted["stage"] == "contacted" and contacted["completed_at"] is None

    def test_pagination_and_the_stage_filter(self, mgr, setup):
        w, r = setup["w"]["id"], setup["r"]
        first = report(mgr, "onboarding", assigned_to=w, limit=1, offset=0, **setup["period"])
        second = report(mgr, "onboarding", assigned_to=w, limit=1, offset=1, **setup["period"])
        assert first["total"] == second["total"] == 2 and first["limit"] == 1 and second["offset"] == 1
        assert set(oids(first) + oids(second)) == {r["R1"]["oid"], r["R2"]["oid"]}
        only = report(mgr, "onboarding", assigned_to=w, stage="activated", **setup["period"])
        assert oids(only) == [r["R2"]["oid"]] and only["total"] == 1
        assert only["summary"]["total"] == 2                                        # the summary ignores the stage filter, like the lead report
        assert errors_of(report(mgr, "onboarding", expect=400, stage="nope", **setup["period"]))["field"] == "stage"

    def test_sorting(self, mgr, setup):
        asc = report(mgr, "onboarding", assigned_to=setup["w"]["id"], sort="business_name", order="asc", **setup["period"])
        names = [i["business_name"] for i in asc["items"]]
        assert names == sorted(names, key=str.lower)
        assert report(mgr, "onboarding", expect=422, sort="password", **setup["period"]).status_code == 422

    def test_records_nobody_owns_are_counted_for_a_caller_who_sees_every_record(self, mgr, setup):
        body = report(mgr, "onboarding", **setup["period"])
        assert body["summary"]["unassigned"] >= 1                                   # R3 (other datasets may add more)
        assert any(o["employee"] is None for o in body["by_owner"])

    def test_the_owner_table_is_bounded_and_says_how_many_there_are(self, mgr, setup):
        from datetime import date
        from sqlalchemy import true
        from sales.repositories import reports as repo
        from sales.repositories.onboarding import OnboardingFilters
        from sales_reports_test_utils import run_db

        assert repo.ONBOARDING_OWNER_ROWS == 50
        body = report(mgr, "onboarding", **setup["period"])
        assert len(body["by_owner"]) <= 50 and body["by_owner_total"] >= len(body["by_owner"])
        f = OnboardingFilters(started_from=date.fromisoformat(setup["period"]["date_from"]), started_to=date.fromisoformat(setup["period"]["date_to"]))
        rows, total = run_db(lambda db: repo.onboarding_cohort_by_owner(db, visibility=true(), filters=f, limit=1))
        assert len(rows) == 1 and total >= 3                                        # W, W2 and the unowned R3 all started in the period

    def test_a_period_before_any_onboarding_is_empty(self, mgr, setup):
        body = report(mgr, "onboarding", assigned_to=setup["w"]["id"], date_from="2005-06-14", date_to="2005-06-16")
        assert oids(body) == [setup["r"]["R4"]["oid"]] and body["summary"]["total"] == 1            # R4 - the only one that started then
        empty = report(mgr, "onboarding", assigned_to=setup["w"]["id"], date_from="2001-01-01", date_to="2001-01-03")
        assert empty["items"] == [] and empty["total"] == 0 and empty["by_owner"] == [] and empty["by_owner_total"] == 0
        assert empty["summary"] == {"total": 0, "open": 0, "activated": 0, "unassigned": 0, "by_stage": {s: 0 for s in STAGES}}

    def test_the_dashboard_section_covers_the_whole_scope_by_current_stage(self, mgr):
        section = dash(mgr)["onboarding"]
        assert list(section["by_stage"]) == STAGES
        assert section["total"] == sum(section["by_stage"].values())
        assert section["active"] == section["total"] - section["by_stage"]["activated"]
        assert section["unassigned"] >= 1 and section["activated_in_period"] >= 1


# =============================================================================
# an Onboarding Employee's view: their own records, nobody else's
# =============================================================================
class TestOnboardingReportForAnOnboardingEmployee:
    def test_only_my_records_are_in_my_report(self, setup):
        r = setup["r"]
        body = report(setup["w"]["headers"], "onboarding", **setup["period"])
        assert body["scope"] == "personal" and body["total"] == 2
        assert set(oids(body)) == {r["R1"]["oid"], r["R2"]["oid"]}                   # not R3 (unowned), not W2's R5, not R4 (2005)
        assert body["summary"]["total"] == 2 and body["summary"]["unassigned"] is None
        assert [o["employee"]["id"] for o in body["by_owner"]] == [setup["w"]["id"]]

    def test_the_other_workers_report_holds_only_theirs(self, setup):
        body = report(setup["w2"]["headers"], "onboarding", **setup["period"])
        assert oids(body) == [setup["r"]["R5"]["oid"]] and body["summary"]["by_stage"]["contacted"] == 1

    def test_asking_about_another_worker_is_a_403_but_about_myself_is_fine(self, setup):
        h = setup["w"]["headers"]
        assert report(h, "onboarding", expect=403, assigned_to=setup["w2"]["id"], **setup["period"]).status_code == 403
        assert report(h, "onboarding", expect=403, assigned_to="00000000-0000-4000-8000-000000000000", **setup["period"]).status_code == 403
        assert report(h, "onboarding", assigned_to="me", **setup["period"])["total"] == 2
        assert report(h, "onboarding", assigned_to=setup["w"]["id"], **setup["period"])["total"] == 2

    def test_my_dashboard_is_onboarding_only(self, setup):
        body = dash(setup["w"]["headers"])
        assert body["scope"] == "personal"
        # R1, R2 and R4 are mine: stages contacted / activated / assigned; R2 was activated today, R4 is old but still open
        o = body["onboarding"]
        assert (o["total"], o["active"], o["activated_in_period"], o["unassigned"]) == (3, 2, 1, None)
        assert o["by_stage"] == {**{stage: 0 for stage in STAGES}, "assigned": 1, "contacted": 1, "activated": 1}
        # nothing else: no lead, work-item, campaign or team figure of any kind
        assert all(v is None for v in body["kpis"].values())
        for section in ("pipeline", "performance", "sources", "campaigns", "activity"):
            assert body[section] is None, section

    def test_a_manager_narrowed_to_an_employee_gets_no_onboarding_section(self, mgr, setup):
        assert dash(mgr, employee_id=setup["employee"]["id"])["onboarding"] is None
