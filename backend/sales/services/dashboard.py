"""The Sales dashboard (Phase 5): one response, sections the caller may see, everything aggregated by the database.

The section a caller gets is decided by the permissions of the records it counts (never by role names), on top of the
scope rules of services/report_scope.py:

  kpis / pipeline / sources   sales.leads.view (lead numbers follow the lead scope); the four "now" KPIs need the view
                              permission of their own kind (follow-ups, demos, trials)
  performance                 the team (sales.leads.scope_all) + sales.leads.view - comparing people is a team privilege
  campaigns                   sales.leads.view + sales.campaigns.view
  activity                    the view permission of each kind, counted per kind
  onboarding                  sales.onboarding.view + an onboarding scope, and only when no employee is singled out (an
                              onboarding record belongs to an onboarding employee, not to a salesperson)

A fixed handful of queries however much data there is (tests/test_sales_reports_db.py counts them).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.constants import ONBOARDING_DONE
from sales.metrics import KPI_STAGES, METRIC_BASES, rate
from sales.repositories import reports as repo
from sales.repositories.reports import LeadScope
from sales.services import report_data as data
from sales.services.report_scope import ReportScope, has_onboarding_scope

TOP_EMPLOYEES = 20
TOP_CAMPAIGNS = 10


async def dashboard(db: AsyncSession, scope: ReportScope) -> dict:
    ctx, period = scope.ctx, scope.period
    lead_scope = LeadScope(scope.visibility, period, assignee=scope.lead_owner)
    can_leads = ctx.has(perms.PERM_LEADS_VIEW)

    out = await data.header(db, scope, period)
    out["bases"] = METRIC_BASES
    kpis = {}

    # -- leads: KPIs + pipeline (one grouped query serves both, so the stage counts always add up to the total) --
    if can_leads:
        totals = await repo.stage_totals(db, lead_scope)
        total_leads = sum(t.leads for t in totals.values())
        kpis["total_leads"] = total_leads
        for kpi, stages in KPI_STAGES.items():
            kpis[kpi] = sum(totals[stage].leads for stage in stages)
        kpis["conversion_rate"] = rate(kpis["won"], total_leads)
        out["pipeline"] = data.pipeline_rows(totals)

    # -- "now" readings, each behind the view permission of its own kind --
    if ctx.has(perms.PERM_FOLLOWUPS_VIEW):
        kpis["overdue_followups"], kpis["upcoming_followups"] = await repo.followups_now(db, visibility=scope.visibility, actor=scope.actor)
    if ctx.has(perms.PERM_DEMOS_VIEW):
        kpis["upcoming_demos"] = await repo.upcoming_demos(db, visibility=scope.visibility, actor=scope.actor)
    if ctx.has(perms.PERM_TRIALS_VIEW):
        kpis["active_trials"] = await repo.active_trials(db, visibility=scope.visibility, lead_owner=scope.lead_owner)
    out["kpis"] = kpis

    # -- activity (event basis), and who did it (team only) --
    rollup = await data.activity_rollup(db, scope)
    if rollup.viewable:
        out["activity"] = {**{kind: (k["total"] if k is not None else None) for kind, k in rollup.kinds.items()}, "total": rollup.total}

    if can_leads and scope.team:
        rows = await data.performance_rows(db, await repo.leads_by_assignee(db, lead_scope), rollup, idle=await data.idle_staff(db, scope))
        out["performance"] = {"items": rows[:TOP_EMPLOYEES], "total": len(rows)}

    # -- where the leads came from --
    if can_leads:
        out["sources"] = [{"source": r.key, "name_en": r.name_en, "name_ar": r.name_ar, **data.lead_figures(r.measures)} for r in await repo.leads_by_source(db, lead_scope)]
        if ctx.has(perms.PERM_CAMPAIGNS_VIEW):
            campaigns, total = await repo.leads_by_campaign(db, lead_scope, limit=TOP_CAMPAIGNS, offset=0)
            none = await repo.leads_without_campaign(db, lead_scope)
            out["campaigns"] = {
                "items": [data.campaign_row(c) for c in campaigns], "total": total,
                "no_campaign": {"leads": none.leads, "won": none.won, "lost": none.lost},
            }

    # -- onboarding (now + event) --
    if has_onboarding_scope(ctx) and scope.subject is None:
        out["onboarding"] = await onboarding_section(db, scope)
    return out


async def onboarding_section(db: AsyncSession, scope: ReportScope) -> dict:
    rows = await repo.onboarding_by_stage(db, visibility=scope.onboarding_visibility, period=scope.period)
    total = sum(r.total for r in rows.values())
    return {
        "total": total,
        "active": total - rows[ONBOARDING_DONE].total,
        "activated_in_period": sum(r.activated_in_period for r in rows.values()),
        # the records nobody owns exist only in the scope of somebody who sees every record
        "unassigned": sum(r.unassigned for r in rows.values()) if scope.ctx.has(perms.PERM_ONBOARDING_SCOPE_ALL) else None,
        "by_stage": {stage: r.total for stage, r in rows.items()},
    }
