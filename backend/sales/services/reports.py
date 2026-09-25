"""The Sales reports (Phase 5): lead, pipeline, employee performance, lead source, campaign, conversion, activity and
onboarding. Each is a period + filters + aggregated results (+ pages where the result is a list of unbounded length).

Access, per report (the router declares the permission keys; this module applies the SCOPE):
  leads / pipeline / sources / conversion   sales.reports.view + sales.leads.view; the numbers follow the caller's lead scope
  employee-performance                      + sales.leads.scope_all (the team) - comparing people is a team privilege
  campaigns                                 + sales.campaigns.view
  activities                                sales.reports.view + the view permission of at least one kind of work
  onboarding                                sales.reports.view + sales.onboarding.view; follows the onboarding scope
A caller outside the team is always limited to their own numbers (services/report_scope.py); asking about somebody else is a 403.
"""
from typing import Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.metrics import ACTIVITY_KINDS, rate, series_bucket
from sales.repositories import leads as leads_repo
from sales.repositories import onboarding as onboarding_repo
from sales.repositories import reports as repo
from sales.repositories.onboarding import OnboardingFilters
from sales.repositories.reports import LeadScope
from sales.services import report_data as data
from sales.services.common import access_denied
from sales.services.report_scope import ReportScope, has_onboarding_scope


def _lead_scope(scope: ReportScope, *, period=True, source=None, campaign_id=None, priority=None, stages=None) -> LeadScope:
    return LeadScope(
        scope.visibility, scope.period if period else None, assignee=scope.lead_owner,
        source=source, campaign_id=campaign_id, priority=priority, stages=stages,
    )


# ---- 1. leads -------------------------------------------------------------------------------------------------------
async def lead_report(
    db: AsyncSession, scope: ReportScope, *, source: Optional[str], campaign_id, priority: Optional[str],
    stages: Optional[Sequence[str]], sort: str, descending: bool, limit: int, offset: int,
) -> dict:
    """The leads created in the period, one page of them, with the totals of the whole selection. The page comes from the
    same repository query as the lead list, so the report can never show a lead the list would not."""
    lead_scope = _lead_scope(scope, source=source, campaign_id=campaign_id, priority=priority, stages=stages)
    rows, total = await leads_repo.list_leads(
        db, visibility=scope.visibility, filters=lead_scope.filters(), sort=sort, descending=descending, limit=limit, offset=offset
    )
    by_stage = await repo.stage_totals(db, lead_scope.without_stages())
    out = await data.header(db, scope, scope.period)
    out["summary"] = {
        "total": sum(t.leads for t in by_stage.values()),
        "value": round(sum(t.value for t in by_stage.values()), 2),
        "by_stage": {stage: t.leads for stage, t in by_stage.items()},
    }
    out["items"] = [_lead_row(r) for r in rows]
    out.update(total=total, limit=limit, offset=offset)
    return out


def _lead_row(r: leads_repo.LeadRow) -> dict:
    lead = r.lead
    return {
        "id": str(lead.id), "business_name": lead.business_name, "city": lead.city, "pipeline_stage": lead.pipeline_stage,
        "priority": lead.priority, "source": lead.source,
        "campaign": {"id": str(lead.campaign_id), "name": r.campaign_name or ""} if lead.campaign_id else None,
        "assigned_to": data.person(lead.assigned_to, r.assignee_name),
        "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None,
        "created_at": lead.created_at, "closed_at": lead.closed_at,
    }


# ---- 2. pipeline ------------------------------------------------------------------------------------------------------
async def pipeline_report(
    db: AsyncSession, scope: ReportScope, *, basis: str, source: Optional[str], campaign_id
) -> dict:
    """Leads (and their estimated value) per CURRENT stage. basis 'created' = the leads that entered the period, 'all' =
    every lead in scope whenever it was created (the live pipeline)."""
    totals = await repo.stage_totals(db, _lead_scope(scope, period=basis == "created", source=source, campaign_id=campaign_id))
    out = await data.header(db, scope, scope.period if basis == "created" else None)
    out.update(
        basis=basis, stages=data.pipeline_rows(totals),
        total_leads=sum(t.leads for t in totals.values()), total_value=round(sum(t.value for t in totals.values()), 2),
    )
    return out


# ---- 3. employee performance --------------------------------------------------------------------------------------------
async def employee_performance_report(
    db: AsyncSession, scope: ReportScope, *, source: Optional[str], campaign_id, limit: int, offset: int
) -> dict:
    """Leads, won, lost, conversion and activity per employee. Team callers only (the router requires scope_all)."""
    if not scope.team:                        # defence in depth - the router already refused
        raise access_denied()
    lead_scope = _lead_scope(scope, source=source, campaign_id=campaign_id)
    groups = await repo.leads_by_assignee(db, lead_scope)
    rollup = await data.activity_rollup(db, scope)
    rows = await data.performance_rows(db, groups, rollup, idle=await data.idle_staff(db, scope))
    out = await data.header(db, scope, scope.period)
    out.update(totals=data.performance_totals(rows, rollup), items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)
    return out


# ---- 4. lead sources ----------------------------------------------------------------------------------------------------
async def source_report(db: AsyncSession, scope: ReportScope, *, campaign_id) -> dict:
    """Leads, won, lost, conversion and estimated value per lead source (a fixed list of a dozen sources, so no pages)."""
    rows = await repo.leads_by_source(db, _lead_scope(scope, campaign_id=campaign_id))
    out = await data.header(db, scope, scope.period)
    out["items"] = [{"source": r.key, "name_en": r.name_en, "name_ar": r.name_ar, **data.lead_figures(r.measures)} for r in rows]
    out["totals"] = data.lead_figures(data.add_measures([r.measures for r in rows]))
    return out


# ---- 5. campaigns ---------------------------------------------------------------------------------------------------------
async def campaign_report(
    db: AsyncSession, scope: ReportScope, *, source: Optional[str], campaign_id, campaign_status: Optional[str], limit: int, offset: int
) -> dict:
    lead_scope = _lead_scope(scope, source=source, campaign_id=campaign_id)
    campaigns, total = await repo.leads_by_campaign(db, lead_scope, campaign_status=campaign_status, limit=limit, offset=offset)
    out = await data.header(db, scope, scope.period)
    out.update(items=[data.campaign_row(c) for c in campaigns], total=total, limit=limit, offset=offset, no_campaign=None)
    if campaign_id is None and campaign_status is None:
        none = await repo.leads_without_campaign(db, lead_scope)
        out["no_campaign"] = {"leads": none.leads, "won": none.won, "lost": none.lost}
    return out


# ---- 6. conversion ------------------------------------------------------------------------------------------------------------
async def conversion_report(db: AsyncSession, scope: ReportScope, *, source: Optional[str], campaign_id) -> dict:
    """How many of the leads that entered the period were won, how many closed in it, why the lost were lost, and the trend."""
    lead_scope = _lead_scope(scope, source=source, campaign_id=campaign_id)
    totals = await repo.stage_totals(db, lead_scope)
    leads = sum(t.leads for t in totals.values())
    won, lost = totals["won"].leads, totals["lost"].leads
    closed_won, closed_lost = await repo.closed_in_period(db, lead_scope, scope.period)
    bucket = series_bucket(scope.period)

    out = await data.header(db, scope, scope.period)
    out["cohort"] = {"leads": leads, "won": won, "lost": lost, "open": leads - won - lost, "conversion_rate": rate(won, leads)}
    out["closed_in_period"] = {"won": closed_won, "lost": closed_lost}
    out["customers"] = await repo.converted_customers(db, lead_scope) if scope.ctx.has(perms.PERM_CUSTOMERS_VIEW) else None
    out["lost_reasons"] = [{"reason": reason, "leads": n} for reason, n in await repo.lost_reasons(db, lead_scope)]
    out["series"] = {
        "bucket": bucket,
        "points": [{"start": start.date(), "leads": n, "won": w} for start, n, w in await repo.conversion_series(db, lead_scope, bucket)],
    }
    return out


# ---- 7. activities ------------------------------------------------------------------------------------------------------------
async def activity_report(db: AsyncSession, scope: ReportScope, *, limit: int, offset: int) -> dict:
    """Calls, follow-ups, demos and trials in the period: per kind with its result / status breakdown, and per employee."""
    rollup = await data.activity_rollup(db, scope)
    if not rollup.viewable:
        raise access_denied()
    names = await repo.names_for(db, rollup.per_person)
    rows = []
    for who, counts in rollup.per_person.items():
        row = {kind: (counts.get(kind, 0) if kind in rollup.viewable else None) for kind in ACTIVITY_KINDS}
        rows.append({"employee": data.person(who, names.get(who)), **row, "activities": sum(v for v in row.values() if v is not None)})
    rows.sort(key=lambda r: (-r["activities"], r["employee"]["name"].lower(), r["employee"]["id"]))

    out = await data.header(db, scope, scope.period)
    out.update(kinds=rollup.kinds, activities=rollup.total, items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)
    return out


# ---- 8. onboarding ---------------------------------------------------------------------------------------------------------------
async def onboarding_report(
    db: AsyncSession, scope: ReportScope, *, stages: Optional[Sequence[str]], assigned_to, sort: str, descending: bool, limit: int, offset: int
) -> dict:
    """The onboarding records that STARTED in the period (i.e. whose customer was converted in it) by current stage and by
    onboarding employee, and one page of them. Follows the caller's onboarding scope (an Onboarding Employee: their own)."""
    if not has_onboarding_scope(scope.ctx):
        raise access_denied()
    # like the employee filter of the other reports: a caller who sees only their own records may not ask about somebody else's
    if assigned_to is not None and not scope.ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL) and assigned_to != scope.ctx.user_id:
        raise access_denied()
    visibility = scope.onboarding_visibility
    filters = OnboardingFilters(stages=stages, assigned_to=assigned_to, started_from=scope.period.date_from, started_to=scope.period.date_to)
    everything = OnboardingFilters(assigned_to=assigned_to, started_from=scope.period.date_from, started_to=scope.period.date_to)

    by_stage = await repo.onboarding_cohort_by_stage(db, visibility=visibility, filters=everything)
    owners, owners_total = await repo.onboarding_cohort_by_owner(db, visibility=visibility, filters=everything)
    rows, total = await onboarding_repo.list_rows(db, visibility=visibility, filters=filters, sort=sort, descending=descending, limit=limit, offset=offset)

    all_records = sum(by_stage.values())
    activated = by_stage["activated"]
    out = await data.header(db, scope, scope.period)
    out["summary"] = {
        "total": all_records, "open": all_records - activated, "activated": activated,
        "unassigned": by_stage["won"] if scope.ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL) else None,
        "by_stage": by_stage,
    }
    out["by_owner_total"] = owners_total                                              # the table holds the busiest ONBOARDING_OWNER_ROWS
    out["by_owner"] = [
        {"employee": data.person(who, name), "total": n, "open": open_, "activated": done} for who, name, n, open_, done in owners
    ]
    out["items"] = [
        {
            "id": str(r.onboarding.id), "business_name": r.business_name, "stage": r.onboarding.stage,
            "assigned_to": data.person(r.onboarding.assigned_to, r.assignee_name),
            "started_at": r.onboarding.started_at, "completed_at": r.onboarding.completed_at,
        }
        for r in rows
    ]
    out.update(total=total, limit=limit, offset=offset)
    return out
