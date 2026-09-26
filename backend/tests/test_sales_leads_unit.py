"""JAZ Sales - Phase 2: pure unit tests (no HTTP, no database rows).

The rules that must hold no matter how the API is wired: value normalization, the pipeline transition table, the
duplicate classifier, the closed vocabularies staying identical across code / schemas / migration, and lead-scope
semantics. Importing the sales package builds (but never connects) the DB engine, so the scratch guard still applies.
"""
from sales_test_utils import (
    ALL_MODULES,
    ALL_PERMISSION_KEYS,
    SYSTEM_ROLE_PERMISSIONS,
    WORKFLOW_PERMISSIONS,
    BATCH_PERMISSIONS,
    assert_scratch_target,
    granted_before_workflow,
)

assert_scratch_target(need_http=False)

import importlib.util
import itertools
import typing
import uuid
from pathlib import Path

import pytest

from sales import constants as C
from sales import lead_schemas as S
from sales import permissions as P
from sales.constants import allowed_stages, transition_error
from sales.repositories.leads import CompanyOwnerRow, LeadMatchRow
from sales.services import duplicates as D
from sales.services.access import StaffContext
from sales.services.lead_access import can_see
from sales.services.normalize import (
    PHONE_TAIL_LEN,
    digits_only,
    norm_email,
    norm_phone,
    norm_text,
    norm_website,
    phone_tail,
)


# =============================================================================
# normalization
# =============================================================================
class TestNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("+964 770 123 4567", "9647701234567"),
        ("00964-770-123-4567", "9647701234567"),
        ("(964) 770.123.4567", "9647701234567"),
        ("0770 123 4567", "07701234567"),
        ("٠٧٧٠ ١٢٣ ٤٥٦٧", "07701234567"),            # Arabic-Indic digits
        ("۰۷۷۰۱۲۳۴۵۶۷", "07701234567"),                # Persian digits
        ("+971 50 123 4567", "971501234567"),
        ("+1 (415) 555-2671", "14155552671"),
        ("call 07701234567 now", "07701234567"),
        ("123", None),                                # too short to identify anyone
        ("abc", None),
        ("", None),
        (None, None),
    ])
    def test_norm_phone(self, raw, expected):
        assert norm_phone(raw) == expected

    def test_national_and_international_spellings_share_a_tail_but_are_not_equal(self):
        national, international = norm_phone("0770 123 4567"), norm_phone("+964 770 123 4567")
        assert national != international
        assert phone_tail(national) == phone_tail(international) == "701234567"
        assert PHONE_TAIL_LEN == 9

    def test_short_numbers_have_no_tail(self):
        assert phone_tail(norm_phone("12345678")) is None and phone_tail(None) is None

    def test_different_operator_prefixes_do_not_share_a_tail(self):
        assert phone_tail(norm_phone("0770 123 4567")) != phone_tail(norm_phone("0750 123 4567"))

    @pytest.mark.parametrize("raw,expected", [
        ("Al-Amal  Pharmacy!!", "al amal pharmacy"),
        ("AL AMAL PHARMACY", "al amal pharmacy"),
        ("  Café   Noir ", "café noir"),
        ("شركة النجاح", "شركه النجاح"),
        ("شَرِكَةُ  النَّجَاح", "شركه النجاح"),         # diacritics dropped
        ("مطعم الأمل", "مطعم الامل"),                    # alef with hamza -> bare alef
        ("مطعم الإمل", "مطعم الامل"),
        ("مطعم الامل ١٢٣", "مطعم الامل 123"),            # digits of any script -> ASCII
        ("Shop_No_5", "shop no 5"),
        ("!!!", None),
        ("", None),
        (None, None),
    ])
    def test_norm_text(self, raw, expected):
        assert norm_text(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("  Foo@Bar.COM ", "foo@bar.com"),
        ("a@b.co", "a@b.co"),
        ("no-at-sign", None),
        ("", None),
        (None, None),
    ])
    def test_norm_email(self, raw, expected):
        assert norm_email(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("https://WWW.Example.com/", "example.com"),
        ("http://example.com", "example.com"),
        ("example.com", "example.com"),
        ("www.example.com/", "example.com"),
        ("example.com/en/?x=1#top", "example.com/en"),
        ("http://user:pw@example.com:8080/a//b/", "example.com/a/b"),
        ("facebook.com/abc", "facebook.com/abc"),            # the path is kept: two Facebook pages are two businesses
        ("https://www.facebook.com/abc/", "facebook.com/abc"),
        ("n/a", None),
        ("localhost", None),
        ("", None),
        (None, None),
    ])
    def test_norm_website(self, raw, expected):
        assert norm_website(raw) == expected

    def test_two_facebook_pages_do_not_collide_but_www_and_scheme_do(self):
        assert norm_website("facebook.com/a") != norm_website("facebook.com/b")
        assert norm_website("HTTPS://www.Example.com") == norm_website("example.com/")

    def test_digits_only(self):
        assert digits_only("+964 (770) 12-3") == "964770123"
        assert digits_only("٠١٢") == "012" and digits_only(None) == ""


# =============================================================================
# the closed vocabularies stay identical everywhere they are written down
# =============================================================================
class TestVocabulary:
    def test_the_spec_lists(self):
        assert C.SOURCE_KEYS == ("google_maps", "website", "facebook", "instagram", "tiktok", "referral", "cold_call",
                                 "inbound", "advertisement", "manual_entry", "import")
        assert C.PIPELINE_STAGES == ("new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed",
                                     "trial", "negotiation", "won", "lost")
        assert C.PRIORITIES == ("low", "medium", "high")
        assert C.CAMPAIGN_STATUSES == ("active", "paused", "completed")
        assert C.LOST_REASONS == ("too_expensive", "not_interested", "already_using_another_system", "no_response",
                                  "wrong_number", "business_closed", "not_suitable", "delayed_decision", "competitor", "other")

    def test_request_schema_literals_match_the_constants(self):
        assert typing.get_args(S.PipelineStage) == C.PIPELINE_STAGES
        assert typing.get_args(S.Priority) == C.PRIORITIES
        assert typing.get_args(S.LostReason) == C.LOST_REASONS
        assert typing.get_args(S.CampaignStatus) == C.CAMPAIGN_STATUSES

    def test_model_check_constraints_match_the_constants(self):
        from sales.models import SalesCampaign, SalesLead
        checks = {c.name: str(c.sqltext) for t in (SalesLead, SalesCampaign) for c in t.__table__.constraints if c.name and c.name.startswith("ck_")}
        for stage in C.PIPELINE_STAGES:
            assert f"'{stage}'" in checks["ck_sales_leads_stage"]
        for reason in C.LOST_REASONS:
            assert f"'{reason}'" in checks["ck_sales_leads_lost_reason"]
        for priority in C.PRIORITIES:
            assert f"'{priority}'" in checks["ck_sales_leads_priority"]
        for status in C.CAMPAIGN_STATUSES:
            assert f"'{status}'" in checks["ck_sales_campaigns_status"]

    def test_migration_frozen_seed_equals_the_code(self):
        """The migration freezes its values as literals (so it never changes meaning). This is the drift guard:
        the code-side catalogs, the test expectations and the migration must all say the same thing."""
        path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "d764d5f44e91_sales_leads_foundation.py"
        spec = importlib.util.spec_from_file_location("mig_d764d5f44e91", path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        assert [(k, en, ar) for k, en, ar, _ in mig._SOURCES] == list(C.LEAD_SOURCES)
        assert [o for *_, o in mig._SOURCES] == sorted(o for *_, o in mig._SOURCES)  # sort_order is display order

        phase2 = {k: d for k, d in mig._PERMISSIONS}
        assert dict(phase2) == {k: d for k, d in P.ALL_PERMISSIONS.items() if k in phase2}
        assert set(P.ALL_PERMISSIONS) == ALL_PERMISSION_KEYS
        phase1 = {"sales.access", "sales.team.view", "sales.staff.create", "sales.staff.update", "sales.staff.assign_roles"}
        phase3 = {k for k in P.ALL_PERMISSIONS if k.split(".")[1] in ("calls", "followups", "demos", "trials")}   # e3a9c5b07d12's own seed
        phase4 = {k for k in P.ALL_PERMISSIONS if k.split(".")[1] in ("customers", "onboarding")}                  # f4c8a1d92b63's own seed
        phase5 = {k for k in P.ALL_PERMISSIONS if k.split(".")[1] in ("dashboard", "reports")}                      # b6d1f3a8c294's own seed
        workflow = set(WORKFLOW_PERMISSIONS)                                                                          # a3f8c2d7e915's own seed
        batches = set(BATCH_PERMISSIONS)                                                                              # f2b6d8a1c4e9's own seed
        assert set(phase2) | phase1 | phase3 | phase4 | phase5 | workflow | batches == set(P.ALL_PERMISSIONS)
        assert not set(phase2) & (phase3 | phase4 | phase5 | workflow | batches)

        granted = {}
        for role, key in mig._GRANTS:
            granted.setdefault(role, set()).add(key)
        for role, keys in granted.items():
            assert keys == granted_before_workflow(role) - {"sales.access", "sales.team.view"} - phase3 - phase4 - phase5 - workflow, role
        assert "onboarding_employee" not in granted  # no lead access for Onboarding Employee

        for stage in C.PIPELINE_STAGES:
            assert f"'{stage}'" in mig._STAGES
        for reason in C.LOST_REASONS:
            assert f"'{reason}'" in mig._LOST_REASONS
        for status in C.CAMPAIGN_STATUSES:
            assert f"'{status}'" in mig._CAMPAIGN_STATUSES
        for priority in C.PRIORITIES:
            assert f"'{priority}'" in mig._PRIORITIES

    def test_permission_modules_reference_real_permissions(self):
        assert {m["permission"] for m in P.MODULES} <= set(P.ALL_PERMISSIONS)
        assert [m["key"] for m in P.MODULES] == ALL_MODULES


# =============================================================================
# the pipeline transition rules
# =============================================================================
def err(current, target, *, has_assignee=True, lost_reason=None):
    result = transition_error(current, target, has_assignee=has_assignee, lost_reason=lost_reason)
    return result[0] if result else None


class TestTransitions:
    @pytest.mark.parametrize("stage", C.PIPELINE_STAGES)
    def test_same_stage_is_rejected(self, stage):
        assert err(stage, stage, lost_reason="other" if stage == "lost" else None) == "same_stage"

    @pytest.mark.parametrize("target", [s for s in C.PIPELINE_STAGES if s != "won"])
    def test_won_is_terminal(self, target):
        assert err("won", target, lost_reason="other" if target == "lost" else None) == "transition_not_allowed"

    def test_new_can_only_be_closed_as_lost(self):
        for target in C.PIPELINE_STAGES:
            if target in ("new", "lost"):
                continue
            assert err("new", target, has_assignee=False) == "transition_not_allowed", target
        assert err("new", "lost", has_assignee=False, lost_reason="wrong_number") is None

    def test_assigned_moves_to_any_working_stage_or_lost_but_not_to_won(self):
        for target in C.WORKING_STAGES:
            assert err("assigned", target) is None, target
        assert err("assigned", "lost", lost_reason="no_response") is None
        assert err("assigned", "won") == "transition_not_allowed"
        assert err("assigned", "new") == "transition_not_allowed"

    @pytest.mark.parametrize("current,target", list(itertools.permutations(C.WORKING_STAGES, 2)))
    def test_working_stages_move_freely_forward_and_back(self, current, target):
        assert err(current, target) is None

    @pytest.mark.parametrize("current", C.WORKING_STAGES)
    def test_working_stages_can_be_lost_but_never_won_by_hand_nor_return_to_new_or_assigned(self, current):
        assert err(current, "won") == "transition_not_allowed"                       # won is reached through the Customer Setup only
        assert err(current, "lost", lost_reason="competitor") is None
        assert err(current, "new", has_assignee=False) == "transition_not_allowed"
        assert err(current, "assigned") == "transition_not_allowed"

    def test_lost_can_be_reopened_to_new_or_assigned_matching_ownership(self):
        assert err("lost", "new", has_assignee=False) is None
        assert err("lost", "new", has_assignee=True) == "unassign_required"
        assert err("lost", "assigned", has_assignee=True) is None
        assert err("lost", "assigned", has_assignee=False) == "assignee_required"
        for target in (*C.WORKING_STAGES, "won"):
            assert err("lost", target) == "transition_not_allowed", target

    def test_lost_needs_a_valid_reason_and_nothing_else_may_carry_one(self):
        assert err("contacted", "lost") == "lost_reason_required"
        assert err("contacted", "lost", lost_reason="not-a-reason") == "invalid_lost_reason"
        for reason in C.LOST_REASONS:
            assert err("contacted", "lost", lost_reason=reason) is None, reason
        assert err("contacted", "interested", lost_reason="other") == "lost_reason_not_allowed"
        assert err("contacted", "negotiation", lost_reason="other") == "lost_reason_not_allowed"

    def test_working_stages_need_an_owner_but_lost_does_not(self):
        for target in C.WORKING_STAGES:
            if target == "contacted":
                continue
            assert err("contacted", target, has_assignee=False) == "assignee_required", target
        assert err("contacted", "lost", has_assignee=False, lost_reason="business_closed") is None

    def test_won_is_nobodys_manual_target(self):
        """`won` is reached through the Customer Setup only: no row of the table lists it, no stage offers it, every manual move to
        it is refused - whether or not the lead has an owner."""
        assert all("won" not in targets for targets in C.STAGE_TRANSITIONS.values())
        for current, has_assignee in itertools.product(C.PIPELINE_STAGES, (True, False)):
            assert "won" not in allowed_stages(current, has_assignee=has_assignee), (current, has_assignee)
            assert err(current, "won", has_assignee=has_assignee) == ("same_stage" if current == "won" else "transition_not_allowed"), current
        assert C.STAGE_TRANSITIONS["won"] == frozenset()                             # ... and a won lead never leaves won by hand

    def test_unknown_stages(self):
        assert err("bogus", "won") == "invalid_stage"
        assert err("new", "bogus") == "invalid_stage"

    def test_every_transition_in_the_table_is_between_known_stages(self):
        assert set(C.STAGE_TRANSITIONS) == set(C.PIPELINE_STAGES)
        for source, targets in C.STAGE_TRANSITIONS.items():
            assert targets <= set(C.PIPELINE_STAGES) and source not in targets

    @pytest.mark.parametrize("current,has_assignee,expected", [
        ("new", False, ("lost",)),
        ("new", True, ("lost",)),
        ("assigned", True, ("contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "lost")),
        ("assigned", False, ("lost",)),
        ("contacted", True, ("interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "lost")),
        ("negotiation", True, ("contacted", "interested", "demo_scheduled", "demo_completed", "trial", "lost")),
        ("won", True, ()),
        ("lost", True, ("assigned",)),
        ("lost", False, ("new",)),
    ])
    def test_allowed_stages(self, current, has_assignee, expected):
        assert allowed_stages(current, has_assignee=has_assignee) == expected

    def test_every_allowed_stage_is_actually_accepted(self):
        for current, has_assignee in itertools.product(C.PIPELINE_STAGES, (True, False)):
            for target in allowed_stages(current, has_assignee=has_assignee):
                reason = "other" if target == "lost" else None
                assert transition_error(current, target, has_assignee=has_assignee, lost_reason=reason) is None

    def test_assignment_only_moves_the_stage_at_the_pipeline_edges(self):
        assert C.AUTO_STAGE_ON_ASSIGN == {"new": "assigned"}
        assert C.AUTO_STAGE_ON_UNASSIGN == {"assigned": "new"}


# =============================================================================
# duplicate classification
# =============================================================================
def match_row(**kw) -> LeadMatchRow:
    base = dict(
        id=uuid.uuid4(), business_name="X", city="C", pipeline_stage="new", created_by=uuid.uuid4(), assigned_to=None,
        assignee_name=None, archived=False, created_at=None, name_norm="x", city_norm="c", phone_norm=None,
        whatsapp_norm=None, email_norm=None, website_norm=None,
    )
    base.update(kw)
    return LeadMatchRow(**base)


def kinds(matches):
    return sorted((m.field, m.kind, m.matched_on) for m in matches)


class TestClassifyLeads:
    def test_identical_normalized_phone_is_exact(self):
        keys = D.Keys.of(phone="+964 770 123 4567")
        assert kinds(D.classify_lead(keys, match_row(phone_norm=norm_phone("00964 7701234567")), D.ALL_CHECKS)) == [("phone", "exact", "phone")]

    def test_national_vs_international_is_only_possible(self):
        keys = D.Keys.of(phone="0770 123 4567")
        assert kinds(D.classify_lead(keys, match_row(phone_norm=norm_phone("+964 770 123 4567")), D.ALL_CHECKS)) == [("phone", "possible", "phone")]

    def test_a_number_matches_a_number_in_either_field(self):
        keys = D.Keys.of(phone="+964 770 123 4567")
        row = match_row(whatsapp_norm=norm_phone("9647701234567"))
        assert kinds(D.classify_lead(keys, row, D.ALL_CHECKS)) == [("phone", "exact", "whatsapp")]
        keys = D.Keys.of(whatsapp="+964 770 123 4567")
        row = match_row(phone_norm=norm_phone("9647701234567"))
        assert kinds(D.classify_lead(keys, row, D.ALL_CHECKS)) == [("whatsapp", "exact", "phone")]

    def test_different_numbers_and_short_numbers_do_not_match(self):
        keys = D.Keys.of(phone="0770 123 4567")
        assert D.classify_lead(keys, match_row(phone_norm=norm_phone("0750 123 4567")), D.ALL_CHECKS) == []
        assert D.classify_lead(D.Keys.of(phone="12345678"), match_row(phone_norm="87654321"), D.ALL_CHECKS) == []
        assert kinds(D.classify_lead(D.Keys.of(phone="12345678"), match_row(phone_norm="12345678"), D.ALL_CHECKS)) == [("phone", "exact", "phone")]

    def test_email_and_website_are_exact_and_case_insensitive(self):
        keys = D.Keys.of(email="Foo@Bar.com", website="HTTPS://www.Example.com/")
        row = match_row(email_norm="foo@bar.com", website_norm="example.com")
        assert kinds(D.classify_lead(keys, row, D.ALL_CHECKS)) == [("email", "exact", "email"), ("website", "exact", "website")]

    def test_name_and_city_together_are_only_possible(self):
        keys = D.Keys.of(business_name="AL-AMAL Pharmacy", city="Baghdad")
        row = match_row(name_norm="al amal pharmacy", city_norm="baghdad")
        assert kinds(D.classify_lead(keys, row, D.ALL_CHECKS)) == [("business_name", "possible", "business_name_city")]

    def test_name_alone_or_a_different_city_is_not_a_match(self):
        row = match_row(name_norm="al amal pharmacy", city_norm="baghdad")
        assert D.classify_lead(D.Keys.of(business_name="Al Amal Pharmacy"), row, D.ALL_CHECKS) == []
        assert D.classify_lead(D.Keys.of(business_name="Al Amal Pharmacy", city="Basra"), row, D.ALL_CHECKS) == []

    def test_only_the_requested_checks_run(self):
        keys = D.Keys.of(phone="+964 770 123 4567", email="a@b.co")
        row = match_row(phone_norm=norm_phone("+964 770 123 4567"), email_norm="a@b.co")
        assert kinds(D.classify_lead(keys, row, frozenset({D.CHECK_EMAIL}))) == [("email", "exact", "email")]
        assert D.classify_lead(keys, row, frozenset()) == []

    def test_severity_is_exact_if_any_match_is_exact(self):
        row = match_row(phone_norm=norm_phone("+964 770 123 4567"), name_norm="n", city_norm="c")
        item = D.LeadMatch(row, D.classify_lead(D.Keys.of(phone="+964 770 123 4567", business_name="n", city="c"), row, D.ALL_CHECKS))
        assert item.severity == "exact"
        weak = D.LeadMatch(row, D.classify_lead(D.Keys.of(business_name="n", city="c"), row, D.ALL_CHECKS))
        assert weak.severity == "possible"

    def test_checks_for_changed_fields(self):
        assert D.checks_for_changed_fields(["phone"]) == {D.CHECK_PHONE}
        assert D.checks_for_changed_fields(["business_name", "city"]) == {D.CHECK_NAME_CITY}
        assert D.checks_for_changed_fields(["notes", "priority"]) == frozenset()


class TestClassifyCompanies:
    company = CompanyOwnerRow(uuid.uuid4(), "شركة النجاح", "Owner@Demo.com", "0501111111")

    def test_owner_email_is_exact(self):
        assert kinds(D.classify_company(D.Keys.of(email="owner@demo.com"), self.company, D.ALL_CHECKS)) == [("email", "exact", "owner_email")]

    def test_owner_phone_exact_and_possible(self):
        assert kinds(D.classify_company(D.Keys.of(phone="050 111 1111"), self.company, D.ALL_CHECKS)) == [("phone", "exact", "owner_phone")]
        assert kinds(D.classify_company(D.Keys.of(whatsapp="+971 50 111 1111"), self.company, D.ALL_CHECKS)) == [("whatsapp", "possible", "owner_phone")]

    def test_company_name_is_possible_and_arabic_variants_fold(self):
        assert kinds(D.classify_company(D.Keys.of(business_name="شركه النجاح"), self.company, D.ALL_CHECKS)) == [("business_name", "possible", "company_name")]
        assert D.classify_company(D.Keys.of(business_name="شركة الأمل"), self.company, D.ALL_CHECKS) == []

    def test_a_website_never_matches_a_company(self):
        assert D.classify_company(D.Keys.of(website="example.com"), self.company, D.ALL_CHECKS) == []


class TestOverridePolicy:
    def ctx(self, *permissions):
        return StaffContext({"role": "jaz_staff"}, uuid.uuid4(), False, (), frozenset(permissions))

    def report(self, kind):
        row = match_row(phone_norm="1", name_norm="n", city_norm="c")
        item = D.LeadMatch(row, [D.Match("phone", kind, "phone")])
        return D.DuplicateReport(leads=[item])

    def test_possible_duplicates_can_be_confirmed_by_anyone(self):
        assert D.can_override(self.report("possible"), self.ctx()) is True

    def test_exact_duplicates_need_the_override_permission(self):
        assert D.can_override(self.report("exact"), self.ctx("sales.leads.create")) is False
        assert D.can_override(self.report("exact"), self.ctx(P.PERM_LEADS_OVERRIDE_DUPLICATES)) is True

    def test_out_of_scope_leads_are_presented_without_identity(self):
        outsider = match_row(created_by=uuid.uuid4(), assigned_to=uuid.uuid4(), assignee_name="Someone")
        ctx = self.ctx(P.PERM_LEADS_VIEW, P.PERM_LEADS_SCOPE_ASSIGNED)
        shown = D.present(D.DuplicateReport(leads=[D.LeadMatch(outsider, [D.Match("phone", "exact", "phone")])]), ctx)["leads"][0]
        assert shown["in_scope"] is False and shown["id"] is None and shown["assigned_to"] is None and shown["assigned"] is True
        insider = match_row(assigned_to=ctx.user_id, assignee_name="Me")
        shown = D.present(D.DuplicateReport(leads=[D.LeadMatch(insider, [D.Match("phone", "exact", "phone")])]), ctx)["leads"][0]
        assert shown["in_scope"] is True and shown["id"] == str(insider.id) and shown["assigned_to"]["name"] == "Me"


# =============================================================================
# lead scope (Python mirror; the SQL predicate is checked against it in test_sales_leads_db.py)
# =============================================================================
def ctx_with(*permissions, user_id=None):
    return StaffContext({"role": "jaz_staff"}, user_id or uuid.uuid4(), False, (), frozenset(permissions))


class TestLeadScope:
    me, other = uuid.uuid4(), uuid.uuid4()

    def test_no_scope_permission_sees_nothing(self):
        ctx = ctx_with(P.PERM_LEADS_VIEW, P.PERM_LEADS_UPDATE, user_id=self.me)
        assert not can_see(ctx, created_by=self.me, assigned_to=self.me)
        assert not can_see(ctx, created_by=self.other, assigned_to=None)

    def test_scope_all_sees_everything(self):
        ctx = ctx_with(P.PERM_LEADS_SCOPE_ALL, user_id=self.me)
        assert can_see(ctx, created_by=self.other, assigned_to=self.other) and can_see(ctx, created_by=self.other, assigned_to=None)

    def test_scope_assigned_sees_only_leads_assigned_to_me(self):
        ctx = ctx_with(P.PERM_LEADS_SCOPE_ASSIGNED, user_id=self.me)
        assert can_see(ctx, created_by=self.other, assigned_to=self.me)
        assert not can_see(ctx, created_by=self.me, assigned_to=self.other)   # I created it, but it is not mine to work
        assert not can_see(ctx, created_by=self.me, assigned_to=None)         # unassigned leads are not in this scope

    def test_scope_intake_sees_my_creations_and_unassigned_leads(self):
        ctx = ctx_with(P.PERM_LEADS_SCOPE_INTAKE, user_id=self.me)
        assert can_see(ctx, created_by=self.me, assigned_to=self.other)       # created by me, even once assigned
        assert can_see(ctx, created_by=self.other, assigned_to=None)          # unassigned
        assert not can_see(ctx, created_by=self.other, assigned_to=self.other)

    def test_scopes_are_a_union(self):
        ctx = ctx_with(P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE, user_id=self.me)
        assert can_see(ctx, created_by=self.other, assigned_to=self.me) and can_see(ctx, created_by=self.other, assigned_to=None)
        assert not can_see(ctx, created_by=self.other, assigned_to=self.other)
