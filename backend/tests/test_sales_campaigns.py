"""JAZ Sales - Phase 2: campaigns (HTTP integration tier).

CRUD for authorized Sales users, leads that optionally belong to a campaign, and the rule that deleting a campaign can
never delete (or orphan) leads - silently or otherwise.
"""
import concurrent.futures

import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    assign,
    create_campaign,
    create_lead,
    errors_of,
    get_lead,
    lead_body,
    make_personas,
    uniq,
)
from sales_test_utils import admin_token, api, assert_scratch_target, auth  # noqa: F401  (admin_token: mints instead of logging in)


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "camp")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


def get_campaign(h, campaign_id, expect=200):
    r = api("GET", f"/api/sales/campaigns/{campaign_id}", h)
    assert r.status_code == expect, r.text
    return r.json()


def patch_campaign(h, campaign_id, body):
    return api("PATCH", f"/api/sales/campaigns/{campaign_id}", h, body)


# =============================================================================
class TestCreate:
    def test_minimal_campaign(self, p, mgr):
        c = create_campaign(mgr, name=f"Minimal {uniq()}", source=None)
        assert c["status"] == "active" and c["source"] is None and c["description"] is None
        assert c["start_date"] is None and c["end_date"] is None and c["lead_count"] == 0
        assert c["created_by"] == {"id": p["manager"]["id"], "name": "Test camp-manager", "status": None}
        assert c["created_at"] and c["updated_at"] and c["id"]

    def test_full_campaign_round_trips(self, mgr):
        body = {"name": f"Ramadan {uniq()}", "description": "Push during Ramadan", "source": "advertisement", "status": "paused",
                "start_date": "2026-03-01", "end_date": "2026-03-30"}
        r = api("POST", "/api/sales/campaigns", mgr, body)
        assert r.status_code == 201, r.text
        c = r.json()
        for key, value in body.items():
            assert c[key] == value, key
        assert get_campaign(mgr, c["id"]) == c

    def test_whitespace_is_trimmed_and_blank_means_no_value(self, mgr):
        name = f"Padded {uniq()}"
        c = create_campaign(mgr, name=f"   {name}  ", description="   ", source=None)
        assert c["name"] == name and c["description"] is None

    @pytest.mark.parametrize("override,status,field", [
        ({"name": "   "}, 422, None),
        ({"name": "x" * 201}, 422, None),
        ({"name": None}, 422, None),
        ({"source": "myspace"}, 400, "source"),
        ({"status": "archived"}, 422, None),
        ({"start_date": "2026-05-02", "end_date": "2026-05-01"}, 400, "end_date"),
        ({"start_date": "not-a-date"}, 422, None),
        ({"start_date": "2026-13-01"}, 422, None),
        ({"description": "d" * 2001}, 422, None),
    ])
    def test_validation(self, mgr, override, status, field):
        body = {"name": f"V {uniq()}", **override}
        body = {k: v for k, v in body.items() if v is not None}
        r = api("POST", "/api/sales/campaigns", mgr, body)
        assert r.status_code == status, r.text
        if field:
            assert errors_of(r)["field"] == field

    def test_a_campaign_may_start_and_end_on_the_same_day(self, mgr):
        assert create_campaign(mgr, start_date="2026-05-01", end_date="2026-05-01")["start_date"] == "2026-05-01"

    @pytest.mark.parametrize("forbidden", [{"id": PHANTOM_ID}, {"created_by": PHANTOM_ID}, {"lead_count": 5}, {"created_at": "2020-01-01T00:00:00Z"}, {"archived_at": "x"}])
    def test_server_owned_fields_cannot_be_supplied(self, mgr, forbidden):
        assert api("POST", "/api/sales/campaigns", mgr, {"name": f"F {uniq()}", **forbidden}).status_code == 422

    def test_names_are_unique_ignoring_case_and_surrounding_space(self, mgr):
        name = f"Unique {uniq()}"
        create_campaign(mgr, name=name)
        for clash in (name, name.upper(), f"  {name.lower()}  "):
            r = api("POST", "/api/sales/campaigns", mgr, {"name": clash})
            assert r.status_code == 400 and errors_of(r)["field"] == "name", clash

    def test_parallel_creates_of_the_same_name_make_exactly_one(self, mgr):
        name = f"Race {uniq()}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: api("POST", "/api/sales/campaigns", mgr, {"name": name}), range(8)))
        codes = sorted(r.status_code for r in results)
        assert codes == [201] + [400] * 7, codes                                   # never a 500 from the lost race
        assert all(errors_of(r)["field"] == "name" for r in results if r.status_code == 400)


class TestReadAndList:
    def test_get_unknown_and_malformed(self, mgr):
        assert api("GET", f"/api/sales/campaigns/{PHANTOM_ID}", mgr).status_code == 404
        assert api("GET", "/api/sales/campaigns/garbage", mgr).status_code == 404

    def test_pagination_filters_and_search(self, mgr):
        token = uniq()
        made = [create_campaign(mgr, name=f"Listing {token} {i}", source=src, status=status, description=(f"blue widgets {token}" if i == 2 else None))
                for i, (src, status) in enumerate([("facebook", "active"), ("facebook", "paused"), ("website", "completed"), ("website", "active")])]
        url = f"/api/sales/campaigns?q={token}"
        page = api("GET", url, mgr).json()
        assert page["total"] == 4 and [c["id"] for c in page["items"]] == [c["id"] for c in reversed(made)]   # newest first
        assert (page["limit"], page["offset"]) == (50, 0)
        assert [c["id"] for c in api("GET", url + "&limit=2&offset=2", mgr).json()["items"]] == [made[1]["id"], made[0]["id"]]
        assert api("GET", url + "&status=active", mgr).json()["total"] == 2
        assert api("GET", url + "&source=website", mgr).json()["total"] == 2
        assert api("GET", url + "&source=website&status=completed", mgr).json()["total"] == 1
        assert [c["id"] for c in api("GET", f"/api/sales/campaigns?q=BLUE%20widgets%20{token}", mgr).json()["items"]] == [made[2]["id"]]   # searches the description too
        assert api("GET", f"/api/sales/campaigns?q={token}%25", mgr).json()["total"] == 0                                          # '%' is literal

    @pytest.mark.parametrize("query", ["status=archived", "limit=0", "limit=201", "offset=-1", f"q={'x' * 201}"])
    def test_bad_list_parameters(self, mgr, query):
        assert api("GET", f"/api/sales/campaigns?{query}", mgr).status_code == 422

    def test_the_list_carries_the_lead_count_and_creator(self, p, mgr):
        c = create_campaign(mgr)
        create_lead(mgr, campaign_id=c["id"])
        create_lead(mgr, campaign_id=c["id"])
        item = api("GET", f"/api/sales/campaigns?q={c['name']}", mgr).json()["items"][0]
        assert item["lead_count"] == 2 and item["created_by"]["name"] == "Test camp-manager"


# =============================================================================
class TestUpdate:
    def test_partial_updates_and_free_status_changes(self, mgr):
        c = create_campaign(mgr, description="first")
        r = patch_campaign(mgr, c["id"], {"description": "second"})
        assert r.status_code == 200 and r.json()["description"] == "second" and r.json()["name"] == c["name"]
        for status in ("paused", "completed", "active"):
            assert patch_campaign(mgr, c["id"], {"status": status}).json()["status"] == status

    def test_optional_fields_can_be_cleared(self, mgr):
        c = create_campaign(mgr, description="x", source="tiktok", start_date="2026-01-01", end_date="2026-02-01")
        cleared = patch_campaign(mgr, c["id"], {"description": None, "source": None, "start_date": None, "end_date": None}).json()
        assert cleared["description"] is None and cleared["source"] is None and cleared["start_date"] is None and cleared["end_date"] is None

    @pytest.mark.parametrize("field", ["name", "status"])
    def test_required_fields_cannot_be_cleared(self, mgr, field):
        c = create_campaign(mgr)
        r = patch_campaign(mgr, c["id"], {field: None})
        assert r.status_code == 400 and errors_of(r)["field"] == field

    def test_empty_body_is_rejected_and_an_unchanged_value_is_a_quiet_no_op(self, mgr):
        c = create_campaign(mgr)
        assert patch_campaign(mgr, c["id"], {}).status_code == 400
        r = patch_campaign(mgr, c["id"], {"name": c["name"], "status": "active"})
        assert r.status_code == 200 and r.json()["updated_at"] == c["updated_at"]

    def test_dates_are_validated_against_the_stored_ones_too(self, mgr):
        c = create_campaign(mgr, start_date="2026-05-10", end_date="2026-05-20")
        assert errors_of(patch_campaign(mgr, c["id"], {"end_date": "2026-05-09"}))["field"] == "end_date"
        assert errors_of(patch_campaign(mgr, c["id"], {"start_date": "2026-05-21"}))["field"] == "end_date"
        assert patch_campaign(mgr, c["id"], {"start_date": "2026-05-01"}).status_code == 200
        assert get_campaign(mgr, c["id"])["start_date"] == "2026-05-01"

    def test_renaming_respects_uniqueness_but_may_change_only_the_case(self, mgr):
        a, b = create_campaign(mgr), create_campaign(mgr)
        assert errors_of(patch_campaign(mgr, b["id"], {"name": a["name"].upper()}))["field"] == "name"
        assert patch_campaign(mgr, a["id"], {"name": a["name"].upper()}).status_code == 200      # its own name, new case

    def test_other_validation(self, mgr):
        c = create_campaign(mgr)
        assert errors_of(patch_campaign(mgr, c["id"], {"source": "nope"}))["field"] == "source"
        assert patch_campaign(mgr, c["id"], {"status": "archived"}).status_code == 422
        assert patch_campaign(mgr, c["id"], {"created_by": PHANTOM_ID}).status_code == 422
        assert patch_campaign(mgr, PHANTOM_ID, {"status": "paused"}).status_code == 404
        assert patch_campaign(mgr, "garbage", {"status": "paused"}).status_code == 404


# =============================================================================
class TestLeadAssociation:
    def test_leads_optionally_belong_to_a_campaign(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        assert lead["campaign"] == {"id": campaign["id"], "name": campaign["name"], "status": "active"}
        assert create_lead(mgr)["campaign"] is None                                # optional
        assert get_campaign(mgr, campaign["id"])["lead_count"] == 1

    def test_the_campaign_status_shows_on_its_leads(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        patch_campaign(mgr, campaign["id"], {"status": "completed"})
        assert get_lead(mgr, lead["id"])["campaign"]["status"] == "completed"

    def test_moving_a_lead_between_campaigns_moves_the_counts(self, mgr):
        first, second = create_campaign(mgr), create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=first["id"])
        assert (get_campaign(mgr, first["id"])["lead_count"], get_campaign(mgr, second["id"])["lead_count"]) == (1, 0)
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": second["id"]})
        assert (get_campaign(mgr, first["id"])["lead_count"], get_campaign(mgr, second["id"])["lead_count"]) == (0, 1)
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": None})
        assert get_campaign(mgr, second["id"])["lead_count"] == 0

    def test_leads_can_be_listed_by_campaign(self, mgr):
        a, b = create_campaign(mgr), create_campaign(mgr)
        in_a = [create_lead(mgr, campaign_id=a["id"])["id"] for _ in range(3)]
        create_lead(mgr, campaign_id=b["id"])
        assert {i["id"] for i in api("GET", f"/api/sales/leads?campaign_id={a['id']}", mgr).json()["items"]} == set(in_a)

    def test_archived_leads_still_belong_to_their_campaign(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert get_campaign(mgr, campaign["id"])["lead_count"] == 1

    def test_a_lead_cannot_join_a_campaign_that_does_not_exist(self, mgr):
        assert errors_of(api("POST", "/api/sales/leads", mgr, lead_body(campaign_id=PHANTOM_ID)))["field"] == "campaign_id"


# =============================================================================
class TestDelete:
    def test_an_empty_campaign_can_be_deleted(self, mgr):
        c = create_campaign(mgr)
        r = api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr)
        assert r.status_code == 200 and r.json() == {"deleted": True}
        assert get_campaign(mgr, c["id"], expect=404)
        assert api("GET", f"/api/sales/campaigns?q={c['name']}", mgr).json()["total"] == 0
        assert api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr).status_code == 404       # gone

    def test_a_campaign_with_leads_is_refused_and_no_lead_is_touched(self, p, mgr):
        c = create_campaign(mgr)
        leads = [create_lead(mgr, campaign_id=c["id"]) for _ in range(3)]
        assign(mgr, leads[0]["id"], p["employee"]["id"])
        r = api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr)
        assert r.status_code == 409
        detail = errors_of(r)
        assert detail["code"] == "campaign_has_leads" and detail["lead_count"] == 3 and detail["field"] == "campaign"
        assert get_campaign(mgr, c["id"])["lead_count"] == 3
        for lead in leads:                                                                     # every lead survives, still in its campaign
            assert get_lead(mgr, lead["id"])["campaign_id"] == c["id"]
        assert api("GET", f"/api/sales/leads?campaign_id={c['id']}", mgr).json()["total"] == 3

    def test_archived_leads_also_block_the_delete(self, mgr):
        c = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=c["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        r = api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr)
        assert r.status_code == 409 and errors_of(r)["lead_count"] == 1
        assert get_lead(mgr, lead["id"])["campaign_id"] == c["id"]

    def test_the_campaign_can_be_deleted_once_its_leads_have_left(self, mgr):
        c = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=c["id"])
        assert api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr).status_code == 409
        api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": None})
        assert api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr).status_code == 200
        assert get_lead(mgr, lead["id"])["campaign"] is None                                  # the lead is fine without it

    def test_a_campaign_with_leads_can_be_retired_by_completing_it(self, mgr):
        c = create_campaign(mgr)
        create_lead(mgr, campaign_id=c["id"])
        assert patch_campaign(mgr, c["id"], {"status": "completed"}).json()["status"] == "completed"

    def test_unknown_ids(self, mgr):
        assert api("DELETE", f"/api/sales/campaigns/{PHANTOM_ID}", mgr).status_code == 404
        assert api("DELETE", "/api/sales/campaigns/garbage", mgr).status_code == 404

    def test_deleting_a_campaign_while_a_lead_is_being_added_never_loses_or_orphans_a_lead(self, mgr):
        for _ in range(6):
            c = create_campaign(mgr)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                created = pool.submit(lambda: api("POST", "/api/sales/leads", mgr, lead_body(campaign_id=c["id"])))
                deleted = pool.submit(lambda: api("DELETE", f"/api/sales/campaigns/{c['id']}", mgr))
                lead_r, delete_r = created.result(), deleted.result()
            outcome = (lead_r.status_code, delete_r.status_code)
            assert outcome in ((201, 409), (400, 200)), outcome                                # the lead won, or the delete won - never both, never a 500
            if outcome == (201, 409):
                assert get_lead(mgr, lead_r.json()["id"])["campaign_id"] == c["id"]
                assert get_campaign(mgr, c["id"])["lead_count"] == 1
            else:
                assert errors_of(lead_r)["field"] == "campaign_id"
                get_campaign(mgr, c["id"], expect=404)


# =============================================================================
class TestWhoMayManageCampaigns:
    def test_managers_and_super_admin_manage_everyone_else_reads(self, p, admin_h, mgr):
        c = create_campaign(admin_h)                                                          # Super Admin may create too
        for who in ("employee", "data_entry"):
            h = p[who]["headers"]
            assert api("GET", "/api/sales/campaigns", h).status_code == 200
            assert get_campaign(h, c["id"])["id"] == c["id"]
            assert api("POST", "/api/sales/campaigns", h, {"name": f"nope {uniq()}"}).status_code == 403
            assert patch_campaign(h, c["id"], {"status": "paused"}).status_code == 403
            assert api("DELETE", f"/api/sales/campaigns/{c['id']}", h).status_code == 403
        for who in ("onboarding", "no_roles"):
            assert api("GET", "/api/sales/campaigns", p[who]["headers"]).status_code == 403
        assert get_campaign(mgr, c["id"])["status"] == "active"                               # nothing the readers tried took effect
        assert patch_campaign(mgr, c["id"], {"status": "paused"}).status_code == 200
