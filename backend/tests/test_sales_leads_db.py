"""JAZ Sales - Phase 2: database-level tests (no HTTP).

Proves the guarantees at the constraint / trigger / query layer, below the API: the seeded sources, every CHECK and
FK on leads / campaigns / activities, the append-only timeline, the SQL scope predicate agreeing with its Python
mirror, and that a page of leads costs a constant number of queries (no N+1). Refuses to run unless DATABASE_URL is
the scratch database.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import asyncio
import itertools
import random
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, event, func, inspect, select, text, true, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from database import SessionLocal, engine
from models import User
from sales import constants as C
from sales import permissions as P
from sales.models import SalesCampaign, SalesLead, SalesLeadActivity, SalesLeadSource
from sales.repositories import activities as activities_repo
from sales.repositories import campaigns as campaigns_repo
from sales.repositories import leads as leads_repo
from sales.repositories.leads import LeadFilters
from sales.services import leads as leads_service
from sales.services.access import StaffContext
from sales.services.lead_access import can_see, lead_visibility


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _user(db, role="jaz_staff", status="active") -> User:
    user = User(
        id=uuid.uuid4(), email=f"leaddb-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1777{random.randint(1000000, 9999999)}",
        password="not-a-real-hash", name="Lead DB Test", role=role, status=status,
    )
    db.add(user)
    await db.flush()
    return user


def _lead(created_by, **kw) -> SalesLead:
    values = dict(id=uuid.uuid4(), created_by=created_by, business_name="Constraint Test", name_norm="constraint test")
    values.update(kw)
    return SalesLead(**values)


async def _violates(db, constraint: str, obj) -> None:
    """Flushing `obj` must fail on exactly `constraint`, leaving the session usable (savepoint)."""
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            db.add(obj)
            await db.flush()
    assert f'"{constraint}"' in str(exc.value), f"expected {constraint}, got: {str(exc.value)[:300]}"


# =============================================================================
class TestSeed:
    def test_sources_are_exactly_the_standard_eleven_in_display_order(self):
        async def _run():
            async with SessionLocal() as db:
                rows = (await db.execute(select(SalesLeadSource).order_by(SalesLeadSource.sort_order))).scalars().all()
                assert [(r.key, r.name_en, r.name_ar) for r in rows] == list(C.LEAD_SOURCES)
                assert all(r.is_active for r in rows)
        run(_run())


# =============================================================================
class TestLeadConstraints:
    def test_every_check_is_enforced(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                await db.flush()
                u = user.id
                now = func.now()
                await _violates(db, "ck_sales_leads_stage", _lead(u, pipeline_stage="bogus"))
                await _violates(db, "ck_sales_leads_priority", _lead(u, priority="urgent"))
                await _violates(db, "ck_sales_leads_lost_reason", _lead(u, pipeline_stage="lost", lost_reason="bogus", closed_at=now))
                # lost <=> reason
                await _violates(db, "ck_sales_leads_lost_reason_pair", _lead(u, pipeline_stage="lost", closed_at=now))
                await _violates(db, "ck_sales_leads_lost_reason_pair", _lead(u, lost_reason="other"))
                await _violates(db, "ck_sales_leads_lost_reason_pair", _lead(u, pipeline_stage="won", lost_reason="other", closed_at=now))
                # closed_at <=> won/lost
                await _violates(db, "ck_sales_leads_closed_at_pair", _lead(u, pipeline_stage="won"))
                await _violates(db, "ck_sales_leads_closed_at_pair", _lead(u, closed_at=now))
                # owner <=> assignment time, archive <=> who archived
                await _violates(db, "ck_sales_leads_assigned_pair", _lead(u, assigned_to=u))
                await _violates(db, "ck_sales_leads_assigned_pair", _lead(u, assigned_at=now))
                await _violates(db, "ck_sales_leads_archived_pair", _lead(u, archived_at=now))
                await _violates(db, "ck_sales_leads_archived_pair", _lead(u, archived_by=u))
                # coordinates: both or neither, in range
                await _violates(db, "ck_sales_leads_coords_pair", _lead(u, latitude=10.0))
                await _violates(db, "ck_sales_leads_coords_pair", _lead(u, longitude=10.0))
                await _violates(db, "ck_sales_leads_coords_range", _lead(u, latitude=91.0, longitude=10.0))
                await _violates(db, "ck_sales_leads_coords_range", _lead(u, latitude=10.0, longitude=-181.0))
                await _violates(db, "ck_sales_leads_value", _lead(u, estimated_value=-1))
                await _violates(db, "ck_sales_leads_name", _lead(u, business_name="   "))
                await db.rollback()
        run(_run())

    def test_valid_combinations_are_accepted(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                u, now = user.id, func.now()
                for kw in (
                    {},
                    {"pipeline_stage": "won", "closed_at": now},
                    {"pipeline_stage": "lost", "lost_reason": "other", "closed_at": now},
                    {"assigned_to": u, "assigned_at": now, "pipeline_stage": "assigned"},
                    {"archived_at": now, "archived_by": u},
                    {"latitude": -90.0, "longitude": 180.0},
                    {"estimated_value": 0},
                ):
                    db.add(_lead(u, **kw))
                await db.flush()  # none of them may raise
                for stage in C.PIPELINE_STAGES:  # every stage in the vocabulary is accepted by the CHECK
                    extra = {"lost_reason": "other"} if stage == "lost" else {}
                    closed = {"closed_at": now} if stage in ("won", "lost") else {}
                    db.add(_lead(u, pipeline_stage=stage, **extra, **closed))
                for priority in C.PRIORITIES:
                    db.add(_lead(u, priority=priority))
                for reason in C.LOST_REASONS:
                    db.add(_lead(u, pipeline_stage="lost", lost_reason=reason, closed_at=now))
                await db.flush()
                await db.rollback()
        run(_run())

    def test_foreign_keys(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                await _violates(db, "sales_leads_source_fkey", _lead(user.id, source="not_a_source"))
                await _violates(db, "sales_leads_campaign_id_fkey", _lead(user.id, campaign_id=uuid.uuid4()))
                await _violates(db, "sales_leads_created_by_fkey", _lead(uuid.uuid4()))
                await _violates(db, "sales_leads_assigned_to_fkey", _lead(user.id, assigned_to=uuid.uuid4(), assigned_at=func.now()))
                await db.rollback()
        run(_run())

    def test_defaults(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                await db.refresh(lead)
                assert (lead.source, lead.pipeline_stage, lead.priority) == ("manual_entry", "new", "medium")
                assert lead.created_at is not None and lead.updated_at is not None
                assert lead.assigned_to is None and lead.archived_at is None and lead.closed_at is None
                await db.rollback()
        run(_run())


class TestCampaignConstraints:
    def test_checks_and_unique_name(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                u = user.id

                def campaign(**kw):
                    values = dict(id=uuid.uuid4(), name=f"Camp {uuid.uuid4().hex[:8]}", created_by=u)
                    values.update(kw)
                    return SalesCampaign(**values)

                await _violates(db, "ck_sales_campaigns_status", campaign(status="archived"))
                await _violates(db, "ck_sales_campaigns_dates", campaign(start_date=date(2026, 5, 2), end_date=date(2026, 5, 1)))
                await _violates(db, "ck_sales_campaigns_name", campaign(name="   "))
                await _violates(db, "sales_campaigns_source_fkey", campaign(source="nope"))
                await _violates(db, "sales_campaigns_created_by_fkey", campaign(created_by=uuid.uuid4()))

                db.add(campaign(name="Ramadan Promo"))
                await db.flush()
                await _violates(db, "uq_sales_campaigns_name", campaign(name="  ramadan PROMO "))   # case + whitespace insensitive
                db.add(campaign(name="Ramadan Promo 2"))                                              # a different name is fine
                for status in C.CAMPAIGN_STATUSES:
                    db.add(campaign(status=status))
                db.add(campaign(start_date=date(2026, 5, 1), end_date=date(2026, 5, 1)))
                await db.flush()
                await db.rollback()
        run(_run())


class TestRestrictiveForeignKeys:
    def test_a_campaign_with_leads_cannot_be_deleted_and_the_lead_survives(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                campaign = SalesCampaign(id=uuid.uuid4(), name=f"Held {uuid.uuid4().hex[:8]}", created_by=user.id)
                db.add(campaign)
                await db.flush()
                lead = _lead(user.id, campaign_id=campaign.id)
                db.add(lead)
                await db.flush()
                with pytest.raises(IntegrityError) as exc:
                    async with db.begin_nested():
                        await db.execute(delete(SalesCampaign).where(SalesCampaign.id == campaign.id))
                assert "sales_leads_campaign_id_fkey" in str(exc.value)
                assert (await db.execute(select(SalesLead.campaign_id).where(SalesLead.id == lead.id))).scalar_one() == campaign.id
                await db.rollback()
        run(_run())

    def test_a_source_in_use_cannot_be_deleted(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                db.add(_lead(user.id, source="tiktok"))
                await db.flush()
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(delete(SalesLeadSource).where(SalesLeadSource.key == "tiktok"))
                await db.rollback()
        run(_run())

    def test_a_lead_with_history_cannot_be_deleted(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                await activities_repo.insert_event(
                    db, lead_id=lead.id, event_type="lead_created", actor_user_id=user.id,
                    before=None, after={"business_name": "x"}, note=None, metadata={}, correlation_id=uuid.uuid4(),
                )
                with pytest.raises(IntegrityError) as exc:
                    async with db.begin_nested():
                        await db.execute(delete(SalesLead).where(SalesLead.id == lead.id))
                assert "sales_lead_activities_lead_id_fkey" in str(exc.value)
                await db.rollback()
        run(_run())


# =============================================================================
class TestActivityTable:
    async def _event(self, db, user=None, lead=None, **kw):
        user = user or await _user(db)
        if lead is None:
            lead = _lead(user.id)
            db.add(lead)
            await db.flush()
        values = dict(
            lead_id=lead.id, event_type="lead_created", actor_user_id=user.id, before=None, after={"a": 1},
            note=None, metadata={}, correlation_id=uuid.uuid4(),
        )
        values.update(kw)
        await activities_repo.insert_event(db, **values)
        return user, lead

    def test_absent_before_after_are_sql_null_not_json_null(self):
        async def _run():
            async with SessionLocal() as db:
                _, lead = await self._event(db)
                row = (await db.execute(text(
                    "SELECT before_data IS NULL, after_data IS NOT NULL FROM sales_lead_activities WHERE lead_id = :l"
                ), {"l": lead.id})).one()
                assert tuple(row) == (True, True)
                await db.rollback()
        run(_run())

    def test_update_delete_and_truncate_are_rejected(self):
        async def _run():
            async with SessionLocal() as db:
                _, lead = await self._event(db)
                for statement in (
                    update(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id).values(note="rewritten history"),
                    delete(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id),
                    text("TRUNCATE sales_lead_activities"),
                ):
                    with pytest.raises(DBAPIError) as exc:
                        async with db.begin_nested():
                            await db.execute(statement)
                    assert "append-only" in str(exc.value)
                # nothing changed
                assert (await db.execute(select(func.count()).select_from(SalesLeadActivity).where(SalesLeadActivity.lead_id == lead.id))).scalar_one() == 1
                assert (await db.execute(select(SalesLeadActivity.note).where(SalesLeadActivity.lead_id == lead.id))).scalar_one() is None
                await db.rollback()
        run(_run())

    def test_payload_constraints(self):
        async def _run():
            async with SessionLocal() as db:
                user = await _user(db)
                lead = _lead(user.id)
                db.add(lead)
                await db.flush()
                base = dict(id=uuid.uuid4(), lead_id=lead.id, actor_user_id=user.id, correlation_id=uuid.uuid4())

                async def violates(constraint, **kw):
                    with pytest.raises(IntegrityError) as exc:
                        async with db.begin_nested():
                            db.add(SalesLeadActivity(**{**base, "id": uuid.uuid4(), "event_type": "lead_created", **kw}))
                            await db.flush()
                    assert f'"{constraint}"' in str(exc.value), str(exc.value)[:300]

                await violates("ck_sales_lead_activities_type", event_type="Bad Type")
                await violates("ck_sales_lead_activities_type", event_type="ab")
                await violates("ck_sales_lead_activities_metadata", event_metadata=["not", "an", "object"])
                await violates("ck_sales_lead_activities_metadata", event_metadata={"blob": "x" * 20000})
                await violates("ck_sales_lead_activities_before", before_data=["nope"])
                await violates("ck_sales_lead_activities_after", after_data={"blob": "y" * 40000})
                await db.rollback()
        run(_run())

    def test_seq_is_monotonic_and_cannot_be_forged(self):
        async def _run():
            async with SessionLocal() as db:
                user, lead = await self._event(db)
                for _ in range(3):
                    await self._event(db, user=user, lead=lead)
                seqs = (await db.execute(select(SalesLeadActivity.seq).where(SalesLeadActivity.lead_id == lead.id).order_by(SalesLeadActivity.seq))).scalars().all()
                assert len(seqs) == 4 and seqs == sorted(seqs) and len(set(seqs)) == 4
                with pytest.raises(DBAPIError):  # GENERATED ALWAYS: an explicit seq is refused
                    async with db.begin_nested():
                        await db.execute(text(
                            "INSERT INTO sales_lead_activities (id, seq, lead_id, event_type, actor_user_id, correlation_id) "
                            "VALUES (:id, 1, :l, 'lead_created', :u, :c)"
                        ), {"id": uuid.uuid4(), "l": lead.id, "u": user.id, "c": uuid.uuid4()})
                await db.rollback()
        run(_run())

    def test_the_vocabulary_can_grow_without_a_migration(self):
        """event_type is a format check, not a closed list: Phase 3 adds calls/follow-ups by code change only."""
        async def _run():
            async with SessionLocal() as db:
                await self._event(db, event_type="call_logged")
                await db.rollback()
        run(_run())


# =============================================================================
class TestScopePredicate:
    """The SQL predicate (used by listings) and the Python mirror (used for single leads and duplicate matches)
    must agree for every combination of scope permissions and every kind of lead."""

    def test_sql_and_python_agree_for_every_scope_combination(self):
        marker = f"scope-{uuid.uuid4().hex[:10]}"

        async def _setup():
            async with SessionLocal() as db:
                me, other = await _user(db), await _user(db)
                combos = {}
                for created_by_me, assigned in itertools.product((True, False), ("none", "me", "other")):
                    creator = me if created_by_me else other
                    assignee = {"none": None, "me": me, "other": other}[assigned]
                    lead = _lead(creator.id, business_name=f"{marker} {created_by_me}-{assigned}", name_norm=marker)
                    if assignee is not None:
                        lead.assigned_to, lead.assigned_at = assignee.id, func.now()
                    db.add(lead)
                    combos[(created_by_me, assigned)] = lead
                await db.commit()
                return me.id, other.id, {k: (v.id, v.created_by, v.assigned_to) for k, v in combos.items()}

        async def _check(me_id, leads):
            scopes = (P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE)
            async with SessionLocal() as db:
                for r in range(len(scopes) + 1):
                    for subset in itertools.combinations(scopes, r):
                        ctx = StaffContext({"role": "jaz_staff"}, me_id, False, (), frozenset({P.PERM_LEADS_VIEW, *subset}))
                        visible_sql = set((await db.execute(
                            select(SalesLead.id).where(SalesLead.name_norm == marker, lead_visibility(ctx))
                        )).scalars().all())
                        visible_py = {lid for lid, created_by, assigned_to in leads.values()
                                      if can_see(ctx, created_by=created_by, assigned_to=assigned_to)}
                        assert visible_sql == visible_py, f"scopes={subset}"
                        if not subset:
                            assert visible_sql == set()  # holding no scope permission reaches nothing (fail closed)
                        if P.PERM_LEADS_SCOPE_ALL in subset:
                            assert visible_sql == {lid for lid, _, _ in leads.values()}

        async def _run():
            me_id, _, leads = await _setup()
            await _check(me_id, leads)
        run(_run())


# =============================================================================
class TestNoNPlusOne:
    """A page of leads / campaigns / events must cost a constant number of statements, however many rows it holds."""

    def _count_statements(self):
        statements = []

        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", before)
        return statements, lambda: event.remove(engine.sync_engine, "before_cursor_execute", before)

    def test_listing_leads_campaigns_and_events(self):
        marker = f"n1-{uuid.uuid4().hex[:10]}"

        async def _run():
            async with SessionLocal() as db:
                creator, assignee = await _user(db), await _user(db)
                campaign = SalesCampaign(id=uuid.uuid4(), name=f"N1 {marker}", created_by=creator.id)
                db.add(campaign)
                await db.flush()
                for i in range(15):
                    lead = _lead(creator.id, business_name=f"{marker} {i}", name_norm=marker, campaign_id=campaign.id)
                    lead.assigned_to, lead.assigned_at = assignee.id, func.now()
                    db.add(lead)
                    await db.flush()
                    await activities_repo.insert_event(
                        db, lead_id=lead.id, event_type="lead_created", actor_user_id=creator.id, before=None, after={"i": i},
                        note=None, metadata={}, correlation_id=uuid.uuid4(),
                    )
                await db.commit()
                ctx = StaffContext({"role": "jaz_staff"}, creator.id, False, (), frozenset({P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_VIEW, P.PERM_LEADS_CHANGE_STAGE}))
                lead_id = (await db.execute(select(SalesLead.id).where(SalesLead.name_norm == marker).limit(1))).scalar_one()

            statements, unlisten = self._count_statements()
            try:
                async with SessionLocal() as db:
                    page = await leads_service.list_leads(
                        db, ctx, LeadFilters(campaign_id=campaign.id), sort="created_at", descending=True, limit=25, offset=0
                    )
                    assert page["total"] == 15 and len(page["items"]) == 15
                    assert all(item["campaign"]["name"] == f"N1 {marker}" and item["assigned_to"]["id"] == str(assignee.id) for item in page["items"])
                    assert len(statements) == 2, statements          # one COUNT + one page query, joins included
                    del statements[:]

                    campaigns = await campaigns_repo.list_campaigns(db, visibility=true(), status=None, source=None, q=marker, limit=50, offset=0)
                    assert campaigns[1] == 1 and campaigns[0][0].lead_count == 15 and campaigns[0][0].creator_name == "Lead DB Test"
                    assert len(statements) == 2, statements
                    del statements[:]

                    events = await leads_service.list_activities(db, ctx, str(lead_id), 50, 0)
                    assert events["total"] == 1 and events["items"][0]["actor"]["name"] == "Lead DB Test"
                    assert len(statements) == 3, statements          # the lead (scope check) + COUNT + events joined with their actor
                    del statements[:]

                    counts = await leads_service.stage_counts(db, ctx, LeadFilters(campaign_id=campaign.id))
                    assert counts["counts"]["new"] == 15 and len(statements) == 1, statements
            finally:
                unlisten()

        run(_run())


# =============================================================================
class TestNormalizedColumns:
    def test_apply_fields_is_the_single_writer_of_the_normalized_columns(self):
        lead = SalesLead(id=uuid.uuid4(), created_by=uuid.uuid4())
        leads_repo.apply_fields(lead, {
            "business_name": "AL-Amal  Pharmacy", "city": "Baghdad", "phone": "+964 770 123 4567", "whatsapp": "٠٧٧٠١٢٣٤٥٦٧",
            "email": " Info@Amal.COM ", "website": "HTTPS://www.Amal.com/",
        })
        assert (lead.name_norm, lead.city_norm) == ("al amal pharmacy", "baghdad")
        assert (lead.phone_norm, lead.whatsapp_norm) == ("9647701234567", "07701234567")
        assert (lead.email_norm, lead.website_norm) == ("info@amal.com", "amal.com")
        assert lead.phone == "+964 770 123 4567" and lead.email == " Info@Amal.COM "     # raw values are stored untouched

        leads_repo.apply_fields(lead, {"phone": None, "email": None, "website": None, "city": None})
        assert (lead.phone_norm, lead.email_norm, lead.website_norm, lead.city_norm) == (None, None, None, None)
        assert lead.name_norm == "al amal pharmacy"                                     # untouched fields keep their norm

    def test_only_lead_fields_may_be_written(self):
        lead = SalesLead(id=uuid.uuid4(), created_by=uuid.uuid4())
        for forbidden in ("pipeline_stage", "lost_reason", "assigned_to", "archived_at", "created_by", "name_norm", "id"):
            with pytest.raises(ValueError):
                leads_repo.apply_fields(lead, {forbidden: "x"})


# =============================================================================
class TestModelMatchesDatabase:
    """The SQLAlchemy models mirror the migration: every declared column, CHECK, unique constraint and index exists in
    the migrated scratch database (and nothing NOT NULL in the DB is nullable in the model, or vice versa)."""

    def test_columns_constraints_and_indexes_exist(self):
        tables = (SalesLeadSource, SalesCampaign, SalesLead, SalesLeadActivity)

        def _reflect(sync_conn):
            insp = inspect(sync_conn)
            out = {}
            for model in tables:
                name = model.__tablename__
                names = lambda sql: {r[0] for r in sync_conn.execute(text(sql), {"t": name})}  # noqa: E731
                out[name] = {
                    "columns": {c["name"]: c["nullable"] for c in insp.get_columns(name)},
                    # straight from the catalogs: expression indexes are not reliably reflected by the inspector
                    "checks": names("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"),
                    "uniques": names("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'u'"),
                    "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t"),
                }
            return out

        async def _run():
            async with engine.connect() as conn:
                reflected = await conn.run_sync(_reflect)
            for model in tables:
                db_table = reflected[model.__tablename__]
                declared = model.__table__
                assert set(db_table["columns"]) == {c.name for c in declared.columns}, model.__tablename__
                for column in declared.columns:
                    assert db_table["columns"][column.name] == column.nullable, (model.__tablename__, column.name)
                declared_checks = {c.name for c in declared.constraints if c.name and c.name.startswith("ck_")}
                assert declared_checks <= db_table["checks"], declared_checks - db_table["checks"]
                declared_indexes = {i.name for i in declared.indexes}
                assert declared_indexes <= db_table["indexes"], declared_indexes - db_table["indexes"]
                declared_uniques = {c.name for c in declared.constraints if c.name and c.name.startswith("uq_")}
                assert declared_uniques <= db_table["uniques"] | db_table["indexes"], declared_uniques

        run(_run())
