"""JAZ Sales - Phase 4: customers (HTTP integration tier).

What a converted lead looks like from the customer side: the Lead <-> Customer <-> Company links, the list / filters /
counts / detail, who sees which customer (a customer is visible exactly when the lead it came from is), the customer
status that follows the onboarding stage, and what happens to a customer when its lead is archived or its company is
soft-deleted. Who may do what per role is covered by test_sales_customers_rbac.py.
"""
import uuid

import pytest

from sales.timezone import app_today

from models import Company
from sales.models import SalesCustomer
from sales_customer_test_utils import (
    PHANTOM_ID,
    api,
    assign_onboarding,
    convert,
    converted,
    customer,
    db_row,
    listing,
    make_personas,
    onboarding_id,
    onboarding_worker,
    run_db,
    set_onboarding_stage,
    uniq,
    won_lead,
)
from sales_test_utils import admin_token, assert_scratch_target, auth, owner_token, owner_user  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "cu")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture(scope="module")
def world(p, mgr, admin_h):
    """Five customers sharing one search token, owned by two different Sales Employees, in different statuses."""
    token = f"P4CUST{uniq(8)}"
    rows = []
    for i, owner in enumerate(("employee", "employee", "employee2", "employee2", "employee")):
        lead = won_lead(mgr, p[owner]["id"], business_name=f"{token} Store {i}")
        c = convert(mgr, lead)["customer"]
        rows.append({"lead": lead, "customer": c, "owner": owner, "oid": c["onboarding"]["id"]})
    worker = onboarding_worker(admin_h, "cw")
    for row in rows[:4]:
        assign_onboarding(mgr, row["oid"], worker["id"])                          # -> onboarding
    set_onboarding_stage(worker["headers"], rows[0]["oid"], "activated")          # -> activated
    # statuses now: 0 activated, 1 onboarding, 2 onboarding, 3 onboarding, 4 active
    return {"token": token, "rows": rows, "worker": worker}


def _ids(headers, world, query=""):
    page = listing(headers, f"/api/sales/customers?q={world['token']}&limit=100{query}")
    return page, [c["id"] for c in page["items"]]


# =============================================================================
class TestTheLinks:
    def test_lead_customer_company_are_linked_both_ways(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        c, lead = made["customer"], made["lead"]
        assert c["lead"]["id"] == lead["id"]
        row = db_row(SalesCustomer, id=uuid.UUID(c["id"]))
        assert str(row["lead_id"]) == lead["id"] and str(row["company_id"]) == c["company"]["id"]
        # lead -> customer
        status = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert status["customer"]["id"] == c["id"] and status["customer"]["company"]["id"] == c["company"]["id"]
        # company -> owner account -> the values the customer shows
        company = db_row(Company, id=uuid.UUID(c["company"]["id"]))
        assert company["name"] == c["company"]["name"] == made["body"]["business_name"]

    def test_one_customer_per_lead_and_per_company_in_the_database(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        other = won_lead(mgr, p["employee"]["id"])
        from sqlalchemy.exc import IntegrityError

        async def _try(db, lead_id, company_id):
            db.add(SalesCustomer(id=uuid.uuid4(), lead_id=uuid.UUID(lead_id), company_id=uuid.UUID(company_id), converted_by=uuid.UUID(p["manager"]["id"])))
            await db.flush()

        for lead_id, company_id, which in (
            (made["lead"]["id"], made["customer"]["company"]["id"], "uq_sales_customers_lead"),
            (other["id"], made["customer"]["company"]["id"], "uq_sales_customers_company"),
        ):
            with pytest.raises(IntegrityError) as exc:
                run_db(lambda db, l=lead_id, c=company_id: _try(db, l, c))
            assert which in str(exc.value)

    def test_the_lead_keeps_pointing_at_no_customer_field_of_its_own(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        lead = api("GET", f"/api/sales/leads/{made['lead']['id']}", mgr).json()
        assert "customer" not in lead and "company_id" not in lead                        # Phase-2 lead payloads are untouched

    def test_the_company_is_shown_with_its_owner_and_employee_count(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        c = customer(mgr, made["customer"]["id"])
        assert c["company"] == {
            "id": made["customer"]["company"]["id"], "name": made["body"]["business_name"], "subscription_status": "active", "deleted": False,
            "owner_name": made["body"]["owner_name"], "owner_email": made["body"]["owner_email"], "owner_phone": made["body"]["owner_phone"], "employee_count": 0,
        }
        assert made["customer"]["company"]["employee_count"] is None                     # only the customer's own page counts them

    def test_a_soft_deleted_company_is_flagged_not_hidden(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        company_id = uuid.UUID(made["customer"]["company"]["id"])

        async def _set(db, value):
            from sqlalchemy import update
            from datetime import datetime, timezone
            await db.execute(update(Company).where(Company.id == company_id).values(deleted_at=datetime.now(timezone.utc) if value else None))

        run_db(lambda db: _set(db, True))
        try:
            assert customer(mgr, made["customer"]["id"])["company"]["deleted"] is True
        finally:
            run_db(lambda db: _set(db, False))
        assert customer(mgr, made["customer"]["id"])["company"]["deleted"] is False

    def test_an_archived_lead_keeps_its_customer_visible(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{made['lead']['id']}", mgr)
        c = customer(mgr, made["customer"]["id"])
        assert c["lead"]["archived"] is True
        page = listing(mgr, f"/api/sales/customers?q={made['lead']['business_name']}")
        assert [i["id"] for i in page["items"]] == [made["customer"]["id"]]


# =============================================================================
class TestStatusFollowsTheOnboarding:
    def test_the_whole_life_of_a_customer(self, p, mgr, admin_h):
        made = converted(mgr, p["employee"]["id"])
        cid, oid = made["customer"]["id"], onboarding_id(mgr, made["customer"]["id"])
        assert customer(mgr, cid)["status"] == "active"
        worker = onboarding_worker(admin_h, "st1")
        assign_onboarding(mgr, oid, worker["id"])
        assert customer(mgr, cid)["status"] == "onboarding"
        for stage in ("contacted", "setup_started", "company_configured", "employees_added", "training"):
            set_onboarding_stage(worker["headers"], oid, stage)
            assert customer(mgr, cid)["status"] == "onboarding", stage
        set_onboarding_stage(worker["headers"], oid, "activated")
        assert customer(mgr, cid)["status"] == "activated"
        set_onboarding_stage(worker["headers"], oid, "training")
        assert customer(mgr, cid)["status"] == "onboarding"

    def test_the_status_cannot_be_set_directly(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        cid = made["customer"]["id"]
        for method in ("PATCH", "PUT", "POST", "DELETE"):
            assert api(method, f"/api/sales/customers/{cid}", mgr, {"status": "activated"}).status_code in (404, 405)
        assert customer(mgr, cid)["status"] == "active"

    def test_the_database_refuses_a_status_that_is_not_one_of_the_three(self, p, mgr):
        made = converted(mgr, p["employee"]["id"])
        from sqlalchemy import update
        from sqlalchemy.exc import IntegrityError

        async def _bad(db):
            await db.execute(update(SalesCustomer).where(SalesCustomer.id == uuid.UUID(made["customer"]["id"])).values(status="archived"))

        with pytest.raises(IntegrityError) as exc:
            run_db(_bad)
        assert "ck_sales_customers_status" in str(exc.value)


# =============================================================================
class TestListAndDetail:
    def test_shape_and_pagination(self, world, mgr):
        page, ids = _ids(mgr, world)
        assert page["total"] == 5 and set(ids) == {r["customer"]["id"] for r in world["rows"]}
        item = page["items"][0]
        assert set(item) == {"id", "status", "converted_at", "converted_by", "created_at", "updated_at", "lead", "company", "onboarding", "can"}
        assert set(item["can"]) == {"view_lead", "view_onboarding"}
        second = listing(mgr, f"/api/sales/customers?q={world['token']}&limit=2&offset=2&order=asc")
        assert second["total"] == 5 and len(second["items"]) == 2 and second["limit"] == 2 and second["offset"] == 2

    def test_newest_first_by_default_and_the_order_can_be_reversed(self, world, mgr):
        _, newest_first = _ids(mgr, world)
        _, oldest_first = _ids(mgr, world, "&order=asc")
        assert newest_first == list(reversed(oldest_first)) and oldest_first[0] == world["rows"][0]["customer"]["id"]

    def test_sort_by_business_name(self, world, mgr):
        names = [c["lead"]["business_name"] for c in _ids(mgr, world, "&sort=business_name&order=asc")[0]["items"]]
        assert names == sorted(names, key=str.lower) and len(names) == 5
        assert api("GET", "/api/sales/customers?sort=nope", mgr).status_code == 422
        assert api("GET", "/api/sales/customers?limit=0", mgr).status_code == 422
        assert api("GET", "/api/sales/customers?offset=-1", mgr).status_code == 422

    def test_filter_by_status(self, world, mgr):
        r = world["rows"]
        assert _ids(mgr, world, "&status=active")[1] == [r[4]["customer"]["id"]]
        assert _ids(mgr, world, "&status=activated")[1] == [r[0]["customer"]["id"]]
        assert set(_ids(mgr, world, "&status=onboarding")[1]) == {r[i]["customer"]["id"] for i in (1, 2, 3)}
        assert set(_ids(mgr, world, "&status=active,activated")[1]) == {r[4]["customer"]["id"], r[0]["customer"]["id"]}
        assert api("GET", "/api/sales/customers?status=nope", mgr).status_code == 400

    def test_search_matches_the_business_the_contact_and_the_company(self, world, mgr):
        r = world["rows"]
        assert [c["id"] for c in listing(mgr, f"/api/sales/customers?q={world['token']}%20Store%203")["items"]] == [r[3]["customer"]["id"]]
        assert listing(mgr, f"/api/sales/customers?q={r[1]['lead']['contact_name']}&limit=100")["total"] >= 1
        assert listing(mgr, "/api/sales/customers?q=%25")["total"] == 0                         # LIKE wildcards are literal

    def test_filter_by_conversion_date(self, world, mgr):
        from datetime import date, timedelta
        tomorrow, yesterday = app_today() + timedelta(days=1), app_today() - timedelta(days=1)
        assert _ids(mgr, world, f"&converted_from={yesterday}&converted_to={tomorrow}")[0]["total"] == 5
        assert _ids(mgr, world, f"&converted_from={tomorrow + timedelta(days=1)}")[0]["total"] == 0
        assert _ids(mgr, world, f"&converted_to={yesterday - timedelta(days=1)}")[0]["total"] == 0

    def test_counts_by_status(self, world, mgr):
        counts = listing(mgr, f"/api/sales/customers/counts?q={world['token']}")
        assert counts == {"counts": {"active": 1, "onboarding": 3, "activated": 1}, "total": 5}
        assert listing(mgr, f"/api/sales/customers/counts?q={world['token']}&status=active")["total"] == 5      # the status filter is what is being counted

    def test_the_onboarding_summary_is_shown_to_those_who_may_view_it(self, world, mgr):
        c = customer(mgr, world["rows"][1]["customer"]["id"])
        assert c["onboarding"] == {"id": world["rows"][1]["oid"], "stage": "assigned", "assigned_to": {"id": world["worker"]["id"], "name": "Test p4-cw", "status": "active"}}
        assert c["can"] == {"view_lead": True, "view_onboarding": True}

    def test_detail_404_for_unknown_and_malformed_ids(self, mgr):
        assert api("GET", f"/api/sales/customers/{PHANTOM_ID}", mgr).status_code == 404
        assert api("GET", "/api/sales/customers/not-a-uuid", mgr).status_code == 404


# =============================================================================
class TestWhoSeesWhichCustomer:
    """A customer is visible exactly when the lead it came from is (the lead scope), so a Sales Employee sees the customers of
    the leads assigned to them and nobody else's."""

    def test_a_sales_employee_sees_only_the_customers_of_their_own_leads(self, p, world):
        mine = listing(p["employee"]["headers"], f"/api/sales/customers?q={world['token']}&limit=100")
        theirs = listing(p["employee2"]["headers"], f"/api/sales/customers?q={world['token']}&limit=100")
        rows = world["rows"]
        assert {c["id"] for c in mine["items"]} == {rows[i]["customer"]["id"] for i in (0, 1, 4)} and mine["total"] == 3
        assert {c["id"] for c in theirs["items"]} == {rows[i]["customer"]["id"] for i in (2, 3)} and theirs["total"] == 2

    def test_a_customer_of_somebody_elses_lead_is_403(self, p, world):
        other = world["rows"][2]["customer"]["id"]                                              # employee2's
        assert api("GET", f"/api/sales/customers/{other}", p["employee"]["headers"]).status_code == 403
        assert api("GET", f"/api/sales/customers/{world['rows'][0]['customer']['id']}", p["employee"]["headers"]).status_code == 200

    def test_the_counts_follow_the_same_scope(self, p, world):
        counts = listing(p["employee"]["headers"], f"/api/sales/customers/counts?q={world['token']}")
        assert counts["total"] == 3 and counts["counts"] == {"active": 1, "onboarding": 1, "activated": 1}

    def test_a_sales_employee_sees_the_customer_but_not_the_onboarding(self, p, world):
        c = customer(p["employee"]["headers"], world["rows"][1]["customer"]["id"])
        assert c["onboarding"] is None and c["can"]["view_onboarding"] is False
        assert c["status"] == "onboarding"                                                      # the coarse state is part of the customer
        assert api("GET", f"/api/sales/onboarding/{world['rows'][1]['oid']}", p["employee"]["headers"]).status_code == 403

    def test_reassigning_the_lead_moves_the_customer_with_it(self, p, mgr, admin_h):
        made = converted(mgr, p["employee"]["id"])
        cid = made["customer"]["id"]
        assert customer(p["employee"]["headers"], cid)["id"] == cid
        # a converted (won) lead can be reassigned by a manager; the customer follows the lead's scope
        assert api("POST", f"/api/sales/leads/{made['lead']['id']}/assign", mgr, {"assigned_to": p["employee2"]["id"]}).status_code == 200
        assert api("GET", f"/api/sales/customers/{cid}", p["employee"]["headers"]).status_code == 403
        assert customer(p["employee2"]["headers"], cid)["id"] == cid

    def test_the_manager_and_the_super_admin_see_every_customer(self, world, mgr, admin_h):
        for headers in (mgr, admin_h):
            assert _ids(headers, world)[0]["total"] == 5

    def test_lead_data_entry_and_onboarding_and_unroled_staff_see_no_customer(self, p, world):
        for who in ("data_entry", "onboarding", "no_roles"):
            assert api("GET", "/api/sales/customers", p[who]["headers"]).status_code == 403, who
            assert api("GET", f"/api/sales/customers/{world['rows'][0]['customer']['id']}", p[who]["headers"]).status_code == 403, who
