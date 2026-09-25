"""JAZ Sales API (Phase 1: workspace context, roles, internal staff management;
Phase 2: lead sources, campaigns, leads, assignment, duplicate detection, pipeline, activity timeline;
Phase 3: calls, follow-ups, demos, trials;
Phase 4: conversion of won leads, customers, onboarding;
Phase 5: dashboard and reports).

Mounted by server.py under /api/sales. Access rules:
  * router-level dependency get_staff_context: only an ACTIVE `jaz_staff` or a
    `super_admin` gets past the door - Company Owners/Employees/anyone else: 403;
  * each endpoint additionally declares the permission it needs via
    require_permission(...) - Super Admin holds all of them implicitly, a staff
    user only what their active roles grant.
Adding an endpoint without a guard fails tests/test_sales_route_sweep.py.
"""
import uuid
from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from sales import permissions as perms
from sales.activity_params import (
    ENDING_SOON_QUERY,
    CallFilterParams,
    DemoFilterParams,
    FollowupFilterParams,
    TrialFilterParams,
    _person,
)
from sales.activity_schemas import (
    ActionNote,
    CallCreate,
    CallList,
    CallOut,
    CallResultCounts,
    CallUpdate,
    DemoCounts,
    DemoCreate,
    DemoList,
    DemoOut,
    DemoReschedule,
    DemoUpdate,
    FollowupCounts,
    FollowupCreate,
    FollowupList,
    FollowupOut,
    FollowupUpdate,
    TrialComplete,
    TrialCounts,
    TrialCreate,
    TrialList,
    TrialOut,
    TrialUpdate,
)
from sales.constants import PIPELINE_STAGES
from sales.customer_params import CustomerFilterParams, OnboardingFilterParams
from sales.customer_schemas import (
    ConversionPreflightOut,
    ConversionStatusOut,
    ConversionValues,
    ConvertLead,
    ConvertResult,
    CustomerCounts,
    CustomerList,
    CustomerOut,
    OnboardingAssign,
    OnboardingAssigneeOut,
    OnboardingCounts,
    OnboardingList,
    OnboardingOut,
    OnboardingStageChange,
    OnboardingUpdate,
)
from sales.deps import SalesRoute, get_audit_context, get_staff_context, require_permission
from sales.lead_schemas import (
    ActivityList,
    AssigneeOut,
    AssignRequest,
    BulkAssignRequest,
    BulkAssignResult,
    CampaignCreate,
    CampaignList,
    CampaignOut,
    CampaignUpdate,
    Deleted,
    DuplicateCheckRequest,
    DuplicateReportOut,
    LeadCreate,
    LeadDetail,
    LeadList,
    LeadResult,
    LeadUpdate,
    SourceOut,
    StageChange,
    StageCounts,
)
from sales.repositories.customers import SORT_FIELDS as CUSTOMER_SORT_FIELDS
from sales.repositories.leads import SORT_FIELDS, LeadFilters
from sales.repositories.onboarding import SORT_FIELDS as ONBOARDING_SORT_FIELDS
from sales.report_params import DashboardParams, LeadListParams, LeadReportParams, OnboardingReportParams
from sales.report_schemas import (
    ActivityReportOut,
    CampaignReportOut,
    ConversionReportOut,
    DashboardOut,
    EmployeePerformanceOut,
    LeadReportOut,
    OnboardingReportOut,
    PipelineReportOut,
    SourceReportOut,
)
from sales.schemas import (
    MeOut,
    PasswordReset,
    RoleAssignment,
    RoleChangeResult,
    RoleDefinition,
    StaffCreate,
    StaffList,
    StaffOut,
    StaffUpdate,
)
from sales.services import calls as calls_service
from sales.services import campaigns as campaigns_service
from sales.services import conversion as conversion_service
from sales.services import customers as customers_service
from sales.services import dashboard as dashboard_service
from sales.services import demos as demos_service
from sales.services import followups as followups_service
from sales.services import leads as leads_service
from sales.services import onboarding as onboarding_service
from sales.services import reports as reports_service
from sales.services import team as team_service
from sales.services import trials as trials_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from sales.services.report_scope import build_scope

sales_router = APIRouter(prefix="/api/sales", tags=["sales"], dependencies=[Depends(get_staff_context)], route_class=SalesRoute)


# ---- workspace context -----------------------------------------------------

@sales_router.get("/me", response_model=MeOut)
async def get_me(ctx: StaffContext = Depends(get_staff_context)):
    """Who am I in JAZ Sales: roles, effective permissions and the workspace
    sections I may see. Needs only staff status - a staff user with no roles
    gets a valid response with empty roles/permissions (the UI shows a clean
    'no role assigned' state)."""
    user = ctx.user
    return {
        "user": {k: user[k] for k in ("id", "name", "email", "phone", "role", "status")},
        "is_super_admin": ctx.is_super_admin,
        "roles": list(ctx.roles),
        "permissions": sorted(ctx.permissions),
        "modules": ctx.visible_modules(),
    }


@sales_router.get("/roles", response_model=list[RoleDefinition])
async def list_roles(
    ctx: StaffContext = Depends(require_permission(perms.PERM_TEAM_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await team_service.list_roles(db)


# ---- team (internal staff) -------------------------------------------------

@sales_router.get("/team", response_model=StaffList)
async def list_team(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_TEAM_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await team_service.list_team(db, limit, offset)


@sales_router.post("/team", response_model=StaffOut, status_code=201)
async def create_staff(
    body: StaffCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_CREATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    # Creating an account and choosing its roles are separate privileges.
    if body.role_keys and not ctx.has(perms.PERM_STAFF_ASSIGN_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    return await team_service.create_staff(db, ctx, body, audit)


@sales_router.patch("/team/{user_id}", response_model=StaffOut)
async def update_staff(
    user_id: str,
    body: StaffUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_UPDATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.update_staff(db, ctx, user_id, body, audit)


@sales_router.post("/team/{user_id}/reset-password")
async def reset_staff_password(
    user_id: str,
    body: PasswordReset,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_UPDATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.reset_password(db, ctx, user_id, body.new_password, audit)


@sales_router.post("/team/{user_id}/roles", response_model=RoleChangeResult)
async def assign_staff_role(
    user_id: str,
    body: RoleAssignment,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_ASSIGN_ROLES)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.assign_role(db, ctx, user_id, body.role_key, audit)


@sales_router.delete("/team/{user_id}/roles/{role_key}", response_model=RoleChangeResult)
async def revoke_staff_role(
    user_id: str,
    role_key: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_STAFF_ASSIGN_ROLES)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await team_service.revoke_role(db, ctx, user_id, role_key, audit)


# =============================================================================
# Phase 2 - lead sources, campaigns, leads
#
# Every route below states the ACTION permission it needs. Which leads a caller may touch is decided
# separately, per lead, from the lead-scope permissions (services/lead_access.py): lists are silently limited to
# the caller's scope, a single lead outside it answers 403. Static paths (/leads/duplicate-check, ...) are
# declared before /leads/{lead_id} so they are never swallowed by the path parameter.
# =============================================================================

# A source is a lowercase snake_case key. Anything else cannot match a source, and (a NUL character in particular)
# must never reach the database driver.
_SOURCE_KEY_PATTERN = r"^[a-z0-9_]+$"


class LeadFilterParams:
    """The filters shared by GET /leads and GET /leads/stage-counts."""

    def __init__(
        self,
        q: Optional[str] = Query(None, max_length=200, description="Search business name, contact name, email, phone, WhatsApp"),
        pipeline_stage: Optional[str] = Query(None, max_length=200, description="One stage, or several separated by commas"),
        assigned_to: Optional[str] = Query(None, max_length=40, description="An employee id, 'me' or 'unassigned'"),
        source: Optional[str] = Query(None, max_length=50, pattern=_SOURCE_KEY_PATTERN),
        campaign_id: Optional[uuid.UUID] = Query(None),
        priority: Optional[Literal["low", "medium", "high"]] = Query(None),
        created_from: Optional[date] = Query(None, description="Created on or after (date, Baghdad time)"),
        created_to: Optional[date] = Query(None, description="Created on or before (date, Baghdad time)"),
        archived: Literal["exclude", "only", "all"] = Query("exclude"),
    ):
        self.q, self.pipeline_stage, self.assigned_to = q, pipeline_stage, assigned_to
        self.source, self.campaign_id, self.priority = source, campaign_id, priority
        self.created_from, self.created_to, self.archived = created_from, created_to, archived

    def to_filters(self, ctx: StaffContext) -> LeadFilters:
        stages = None
        if self.pipeline_stage:
            stages = [part.strip() for part in self.pipeline_stage.split(",") if part.strip()]
            unknown = [stage for stage in stages if stage not in PIPELINE_STAGES]
            if unknown:
                raise field_error("pipeline_stage", f"Unknown pipeline stage: {', '.join(unknown)}")
        assignee, unassigned = None, False
        if self.assigned_to == "unassigned":
            unassigned = True
        elif self.assigned_to == "me":
            assignee = ctx.user_id
        elif self.assigned_to:
            try:
                assignee = uuid.UUID(self.assigned_to)
            except ValueError:
                raise field_error("assigned_to", "Invalid employee id")
        return LeadFilters(
            q=self.q, stages=stages, assigned_to=assignee, unassigned=unassigned, source=self.source,
            campaign_id=self.campaign_id, priority=self.priority, created_from=self.created_from,
            created_to=self.created_to, archived=self.archived,
        )


# ---- sources ---------------------------------------------------------------

@sales_router.get("/sources", response_model=List[SourceOut])
async def list_sources(
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The standard lead sources (fixed reference data), in display order."""
    return await leads_service.list_sources(db)


# ---- assignment candidates -------------------------------------------------

@sales_router.get("/assignees", response_model=List[AssigneeOut])
async def list_assignees(
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_ASSIGN)),
    db: AsyncSession = Depends(get_db),
):
    """Everyone who can receive lead assignments right now (active staff whose active role works assigned leads),
    with their open workload - the manager's picker and workload view."""
    return await leads_service.list_assignees(db)


# ---- campaigns -------------------------------------------------------------

@sales_router.get("/campaigns", response_model=CampaignList)
async def list_campaigns(
    status: Optional[Literal["active", "paused", "completed"]] = Query(None),
    source: Optional[str] = Query(None, max_length=50, pattern=_SOURCE_KEY_PATTERN),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_CAMPAIGNS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await campaigns_service.list_campaigns(db, ctx, status=status, source=source, q=q, limit=limit, offset=offset)


@sales_router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CAMPAIGNS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await campaigns_service.create_campaign(db, ctx, body)


@sales_router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CAMPAIGNS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await campaigns_service.get_campaign(db, ctx, campaign_id)


@sales_router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CAMPAIGNS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await campaigns_service.update_campaign(db, ctx, campaign_id, body)


@sales_router.delete("/campaigns/{campaign_id}", response_model=Deleted)
async def delete_campaign(
    campaign_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CAMPAIGNS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a campaign that has no leads. With leads it answers 409 - a campaign delete never removes leads."""
    return await campaigns_service.delete_campaign(db, ctx, campaign_id)


# ---- leads: collection -----------------------------------------------------

@sales_router.get("/leads", response_model=LeadList)
async def list_leads(
    params: LeadFilterParams = Depends(),
    sort: Literal[SORT_FIELDS] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, filterable, searchable, sortable - limited to the leads the caller may see."""
    return await leads_service.list_leads(
        db, ctx, params.to_filters(ctx), sort=sort, descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.post("/leads", response_model=LeadDetail, status_code=201)
async def create_lead(
    body: LeadCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_CREATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Duplicates are checked first: 409 with the report unless `confirm_duplicates` is true (and 403 when an
    exact duplicate is confirmed without sales.leads.override_duplicates). Nothing is created in either case."""
    return await leads_service.create_lead(db, ctx, body, audit)


@sales_router.post("/leads/duplicate-check", response_model=DuplicateReportOut)
async def check_lead_duplicates(
    body: DuplicateCheckRequest,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Read-only preview of what creating/saving a lead with these values would collide with (other leads and
    existing JAZ companies)."""
    return await leads_service.duplicate_check(db, ctx, body)


@sales_router.get("/leads/stage-counts", response_model=StageCounts)
async def lead_stage_counts(
    params: LeadFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Leads per pipeline stage within the caller's scope (the stage filter itself is ignored)."""
    return await leads_service.stage_counts(db, ctx, params.to_filters(ctx))


@sales_router.post("/leads/bulk-assign", response_model=BulkAssignResult)
async def bulk_assign_leads(
    body: BulkAssignRequest,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_ASSIGN)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """All-or-nothing: if any lead is missing, out of scope or archived, nothing changes."""
    return await leads_service.bulk_assign(db, ctx, body.lead_ids, body.assigned_to, audit)


# ---- leads: one lead -------------------------------------------------------

@sales_router.get("/leads/{lead_id}", response_model=LeadDetail)
async def get_lead(
    lead_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await leads_service.get_lead(db, ctx, lead_id)


@sales_router.patch("/leads/{lead_id}", response_model=LeadDetail)
async def update_lead(
    lead_id: str,
    body: LeadUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_UPDATE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Edits lead details only. Stage, owner and archive state have their own endpoints."""
    return await leads_service.update_lead(db, ctx, lead_id, body, audit)


@sales_router.delete("/leads/{lead_id}", response_model=LeadResult)
async def archive_lead(
    lead_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_DELETE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Archives the lead (hidden from listings; nothing is removed and its history stays). Idempotent."""
    return await leads_service.archive_lead(db, ctx, lead_id, audit)


@sales_router.post("/leads/{lead_id}/restore", response_model=LeadResult)
async def restore_lead(
    lead_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_DELETE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await leads_service.restore_lead(db, ctx, lead_id, audit)


@sales_router.post("/leads/{lead_id}/assign", response_model=LeadResult)
async def assign_lead(
    lead_id: str,
    body: AssignRequest,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_ASSIGN)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Assigns the lead to one Sales Employee, or reassigns it. The assignee must be an active staff member whose
    active role works assigned leads - a deactivated or revoked person cannot receive it."""
    return await leads_service.assign_lead(db, ctx, lead_id, body.assigned_to, audit)


@sales_router.post("/leads/{lead_id}/unassign", response_model=LeadResult)
async def unassign_lead(
    lead_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_ASSIGN)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await leads_service.unassign_lead(db, ctx, lead_id, audit)


@sales_router.post("/leads/{lead_id}/stage", response_model=LeadDetail)
async def change_lead_stage(
    lead_id: str,
    body: StageChange,
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_CHANGE_STAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Moves the lead along the pipeline. The transition is validated here (400 for a bad request such as a
    missing lost reason, 409 for a move the lead's current state does not allow)."""
    return await leads_service.change_stage(db, ctx, lead_id, body, audit)


@sales_router.get("/leads/{lead_id}/activities", response_model=ActivityList)
async def list_lead_activities(
    lead_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The lead's immutable timeline, newest first."""
    return await leads_service.list_activities(db, ctx, lead_id, limit, offset)


# =============================================================================
# Phase 3 - calls, follow-ups, demos, trials
#
# Same two layers as the lead routes: each route states the ACTION permission it needs (sales.<kind>.view to read,
# sales.<kind>.manage to change), and which items a caller may touch is decided from the lead-scope permissions -
# an item is visible and actionable exactly when its lead is (lists are silently limited to the caller's scope, one
# item on a lead outside it answers 403, an unknown or malformed id answers 404). Static paths (/calls/result-counts,
# /followups/counts, ...) are declared before /<kind>/{id} so they are never swallowed by the path parameter.
# There is no DELETE anywhere: calls are records, and follow-ups, demos and trials end in a terminal status.
# Nothing below moves a lead in the pipeline - that stays the explicit POST /leads/{id}/stage.
# =============================================================================

# ---- calls -----------------------------------------------------------------

@sales_router.get("/calls", response_model=CallList)
async def list_calls(
    params: CallFilterParams = Depends(),
    order: Literal["asc", "desc"] = Query("desc", description="By time of the call"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_CALLS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Recent calls: paginated, filterable, newest first - limited to the leads the caller may see."""
    return await calls_service.list_calls(
        db, ctx, params.to_filters(ctx), descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.get("/calls/result-counts", response_model=CallResultCounts)
async def call_result_counts(
    params: CallFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_CALLS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Calls per result within the caller's scope (the result filter itself is ignored)."""
    return await calls_service.result_counts(db, ctx, params.to_filters(ctx))


@sales_router.post("/calls", response_model=CallOut, status_code=201)
async def create_call(
    body: CallCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CALLS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Logs a call to a lead, made by the caller. Never moves the lead in the pipeline."""
    return await calls_service.create_call(db, ctx, body, audit)


@sales_router.get("/calls/{call_id}", response_model=CallOut)
async def get_call(
    call_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CALLS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await calls_service.get_call(db, ctx, call_id)


@sales_router.patch("/calls/{call_id}", response_model=CallOut)
async def update_call(
    call_id: str,
    body: CallUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CALLS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Corrects a call's result, time, duration or notes. The lead and the employee never change."""
    return await calls_service.update_call(db, ctx, call_id, body, audit)


# ---- follow-ups ------------------------------------------------------------

@sales_router.get("/followups", response_model=FollowupList)
async def list_followups(
    params: FollowupFilterParams = Depends(),
    sort: Literal["due_at", "completed_at", "created_at"] = Query("due_at"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated and filterable, limited to the leads the caller may see. The views are filters: My follow-ups =
    assigned_to=me&status=pending, Upcoming = due=upcoming, Overdue = due=overdue, Completed = status=completed."""
    return await followups_service.list_followups(
        db, ctx, params.to_filters(ctx), sort=sort, descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.get("/followups/counts", response_model=FollowupCounts)
async def followup_counts(
    params: FollowupFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The numbers behind the tabs, within the caller's scope (the status and due filters are ignored)."""
    return await followups_service.counts(db, ctx, params.to_filters(ctx))


@sales_router.post("/followups", response_model=FollowupOut, status_code=201)
async def create_followup(
    body: FollowupCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Creates a follow-up on a lead. For the caller unless `assigned_to` names somebody else (needs
    sales.leads.assign); the assignee must be an active staff member who can work follow-ups on this lead."""
    return await followups_service.create_followup(db, ctx, body, audit)


@sales_router.get("/followups/{followup_id}", response_model=FollowupOut)
async def get_followup(
    followup_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await followups_service.get_followup(db, ctx, followup_id)


@sales_router.patch("/followups/{followup_id}", response_model=FollowupOut)
async def update_followup(
    followup_id: str,
    body: FollowupUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Edits a PENDING follow-up's due time, notes or assignee (changing the assignee needs sales.leads.assign)."""
    return await followups_service.update_followup(db, ctx, followup_id, body, audit)


@sales_router.post("/followups/{followup_id}/complete", response_model=FollowupOut)
async def complete_followup(
    followup_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await followups_service.complete_followup(db, ctx, followup_id, body.note if body else None, audit)


@sales_router.post("/followups/{followup_id}/cancel", response_model=FollowupOut)
async def cancel_followup(
    followup_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_FOLLOWUPS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await followups_service.cancel_followup(db, ctx, followup_id, body.note if body else None, audit)


# ---- demos -----------------------------------------------------------------

@sales_router.get("/demos", response_model=DemoList)
async def list_demos(
    params: DemoFilterParams = Depends(),
    order: Literal["asc", "desc"] = Query("asc", description="By scheduled time"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated and filterable, limited to the leads the caller may see. Upcoming = status=scheduled (a scheduled
    demo whose time has passed is flagged `is_past_due`), Completed = status=completed, cancelled / no-show =
    status=cancelled,no_show."""
    return await demos_service.list_demos(db, ctx, params.to_filters(ctx), descending=order == "desc", limit=limit, offset=offset)


@sales_router.get("/demos/counts", response_model=DemoCounts)
async def demo_counts(
    params: DemoFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The numbers behind the tabs, within the caller's scope (the status and date filters are ignored)."""
    return await demos_service.counts(db, ctx, params.to_filters(ctx))


@sales_router.post("/demos", response_model=DemoOut, status_code=201)
async def schedule_demo(
    body: DemoCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Schedules a demo for a lead. For the caller unless `assigned_to` names somebody else (needs
    sales.leads.assign). Never moves the lead in the pipeline."""
    return await demos_service.schedule_demo(db, ctx, body, audit)


@sales_router.get("/demos/{demo_id}", response_model=DemoOut)
async def get_demo(
    demo_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await demos_service.get_demo(db, ctx, demo_id)


@sales_router.patch("/demos/{demo_id}", response_model=DemoOut)
async def update_demo(
    demo_id: str,
    body: DemoUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Edits a SCHEDULED demo's notes or assignee (changing the assignee needs sales.leads.assign)."""
    return await demos_service.update_demo(db, ctx, demo_id, body, audit)


@sales_router.post("/demos/{demo_id}/reschedule", response_model=DemoOut)
async def reschedule_demo(
    demo_id: str,
    body: DemoReschedule,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await demos_service.reschedule_demo(db, ctx, demo_id, body, audit)


@sales_router.post("/demos/{demo_id}/complete", response_model=DemoOut)
async def complete_demo(
    demo_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await demos_service.complete_demo(db, ctx, demo_id, body.note if body else None, audit)


@sales_router.post("/demos/{demo_id}/cancel", response_model=DemoOut)
async def cancel_demo(
    demo_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await demos_service.cancel_demo(db, ctx, demo_id, body.note if body else None, audit)


@sales_router.post("/demos/{demo_id}/no-show", response_model=DemoOut)
async def mark_demo_no_show(
    demo_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_DEMOS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await demos_service.mark_no_show(db, ctx, demo_id, body.note if body else None, audit)


# ---- trials (Sales-side tracking only - NOT the platform's subscription trial) ----

@sales_router.get("/trials", response_model=TrialList)
async def list_trials(
    params: TrialFilterParams = Depends(),
    sort: Literal["expected_end_at", "started_at", "actual_end_at"] = Query("expected_end_at"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated and filterable, limited to the leads the caller may see. Active = status=active, Ending soon =
    ending_within_days=3 (active trials expected to end within 3 days, or already past it), Completed =
    status=completed."""
    return await trials_service.list_trials(
        db, ctx, params.to_filters(ctx), sort=sort, descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.get("/trials/counts", response_model=TrialCounts)
async def trial_counts(
    params: TrialFilterParams = Depends(),
    ending_soon_days: int = ENDING_SOON_QUERY,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The numbers behind the tabs, within the caller's scope (the status and date filters are ignored)."""
    return await trials_service.counts(db, ctx, params.to_filters(ctx), ending_soon_days)


@sales_router.post("/trials", response_model=TrialOut, status_code=201)
async def start_trial(
    body: TrialCreate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Starts a Sales-side trial on a lead. 409 while the lead already has an active one. Never moves the lead in
    the pipeline, and never touches the platform's subscriptions."""
    return await trials_service.start_trial(db, ctx, body, audit)


@sales_router.get("/trials/{trial_id}", response_model=TrialOut)
async def get_trial(
    trial_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await trials_service.get_trial(db, ctx, trial_id)


@sales_router.patch("/trials/{trial_id}", response_model=TrialOut)
async def update_trial(
    trial_id: str,
    body: TrialUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Edits an ACTIVE trial's expected end or notes."""
    return await trials_service.update_trial(db, ctx, trial_id, body, audit)


@sales_router.post("/trials/{trial_id}/complete", response_model=TrialOut)
async def complete_trial(
    trial_id: str,
    body: Optional[TrialComplete] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await trials_service.complete_trial(db, ctx, trial_id, body, audit)


@sales_router.post("/trials/{trial_id}/cancel", response_model=TrialOut)
async def cancel_trial(
    trial_id: str,
    body: Optional[ActionNote] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_TRIALS_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    return await trials_service.cancel_trial(db, ctx, trial_id, body.note if body else None, audit)


# =============================================================================
# Phase 4 - conversion, customers, onboarding
#
# Conversion: a WON lead becomes a customer and a JAZ company (sales.customers.convert, on a lead within the caller's lead
# scope). GET /leads/{id}/conversion says where the lead stands, POST .../convert/preflight shows what converting would do
# (including any JAZ company it could duplicate) without doing it, POST .../convert does it - idempotently.
# Customers follow the LEAD scope (a customer is visible exactly when its lead is). Onboarding has its OWN scope
# (sales.onboarding.scope_all / scope_assigned): an Onboarding Employee reaches the records assigned to them and nothing
# else - no lead, no customer, no company. Static paths (/customers/counts, /onboarding/counts, /onboarding/assignees) are
# declared before /<kind>/{id} so they are never swallowed by the path parameter. There is no DELETE anywhere: a
# customer is a permanent record, and an onboarding ends at its `activated` stage.
# =============================================================================

# ---- conversion ------------------------------------------------------------

@sales_router.get("/leads/{lead_id}/conversion", response_model=ConversionStatusOut)
async def get_lead_conversion(
    lead_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Whether the lead has been converted (and to which customer), whether THIS caller can convert it, and why not."""
    return await conversion_service.conversion_status(db, ctx, lead_id)


@sales_router.post("/leads/{lead_id}/convert/preflight", response_model=ConversionPreflightOut)
async def preflight_lead_conversion(
    lead_id: str,
    body: Optional[ConversionValues] = None,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_CONVERT)),
    db: AsyncSession = Depends(get_db),
):
    """Read-only. The values the company would be created from (what the body gives, else what the lead holds), what would
    stop the conversion (lead state, an email or phone a JAZ account already uses, an exact JAZ company duplicate), any
    POSSIBLE duplicate the caller would have to confirm, and the subscription plans on offer."""
    return await conversion_service.preflight(db, ctx, lead_id, body or ConversionValues())


@sales_router.post("/leads/{lead_id}/convert", response_model=ConvertResult, status_code=201)
async def convert_lead(
    lead_id: str,
    body: ConvertLead,
    response: Response,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_CONVERT)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Converts a WON lead: creates the JAZ company (through the platform's own company service), its owner account, the
    customer and its onboarding, in one transaction. IDEMPOTENT: a lead that was already converted answers 200 with the
    existing customer and `already_converted: true` and creates nothing. 409 when the lead is not won / is archived, when
    a JAZ company already exists for the owner, and (until `confirm_duplicates`) when only a possible duplicate does."""
    result = await conversion_service.convert_lead(db, ctx, lead_id, body, audit)
    if result["already_converted"]:
        response.status_code = 200
    return result


# ---- customers -------------------------------------------------------------

@sales_router.get("/customers", response_model=CustomerList)
async def list_customers(
    params: CustomerFilterParams = Depends(),
    sort: str = Query("converted_at", pattern="^(" + "|".join(CUSTOMER_SORT_FIELDS) + ")$"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated and filterable, limited to the customers whose lead the caller may see."""
    return await customers_service.list_customers(
        db, ctx, params.to_filters(ctx), sort=sort, descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.get("/customers/counts", response_model=CustomerCounts)
async def customer_counts(
    params: CustomerFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The numbers behind the status tabs, within the caller's scope (the status filter is ignored)."""
    return await customers_service.status_counts(db, ctx, params.to_filters(ctx))


@sales_router.get("/customers/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_CUSTOMERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await customers_service.get_customer(db, ctx, customer_id)


# ---- onboarding ------------------------------------------------------------

@sales_router.get("/onboarding", response_model=OnboardingList)
async def list_onboarding(
    params: OnboardingFilterParams = Depends(),
    sort: str = Query("started_at", pattern="^(" + "|".join(ONBOARDING_SORT_FIELDS) + ")$"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Paginated and filterable, limited to the onboarding records in the caller's onboarding scope (all of them, or only
    the ones assigned to the caller). The views are filters: My onboarding = assigned_to=me&open=true, Unassigned =
    unassigned=true, one stage = stage=training."""
    return await onboarding_service.list_onboarding(
        db, ctx, params.to_filters(ctx), sort=sort, descending=order == "desc", limit=limit, offset=offset
    )


@sales_router.get("/onboarding/counts", response_model=OnboardingCounts)
async def onboarding_counts(
    params: OnboardingFilterParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The numbers behind the tabs, within the caller's scope (the stage and owner filters are ignored)."""
    return await onboarding_service.counts(db, ctx, params.to_filters(ctx))


@sales_router.get("/onboarding/assignees", response_model=List[OnboardingAssigneeOut])
async def list_onboarding_assignees(
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_ASSIGN)),
    db: AsyncSession = Depends(get_db),
):
    """Everyone who can be given an onboarding right now (an active staff member who works onboarding records assigned
    to them), with their open workload."""
    return await onboarding_service.list_assignees(db)


@sales_router.get("/onboarding/{onboarding_id}", response_model=OnboardingOut)
async def get_onboarding(
    onboarding_id: str,
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    return await onboarding_service.get_onboarding(db, ctx, onboarding_id)


@sales_router.patch("/onboarding/{onboarding_id}", response_model=OnboardingOut)
async def update_onboarding(
    onboarding_id: str,
    body: OnboardingUpdate,
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Edits the working notes. The stage and the owner have their own endpoints."""
    return await onboarding_service.update_onboarding(db, ctx, onboarding_id, body, audit)


@sales_router.post("/onboarding/{onboarding_id}/assign", response_model=OnboardingOut)
async def assign_onboarding(
    onboarding_id: str,
    body: OnboardingAssign,
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_ASSIGN)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Assigns the onboarding to an onboarding employee, or reassigns it. The assignee must be an active staff member who
    works onboarding records assigned to them - a deactivated or revoked person cannot be given one."""
    return await onboarding_service.assign_onboarding(db, ctx, onboarding_id, body, audit)


@sales_router.post("/onboarding/{onboarding_id}/stage", response_model=OnboardingOut)
async def change_onboarding_stage(
    onboarding_id: str,
    body: OnboardingStageChange,
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_MANAGE)),
    db: AsyncSession = Depends(get_db),
    audit: AuditContext = Depends(get_audit_context),
):
    """Moves the onboarding along its workflow. The transition is validated here (409 for a move the record's current
    state does not allow - e.g. before anybody owns it)."""
    return await onboarding_service.change_stage(db, ctx, onboarding_id, body, audit)


@sales_router.get("/onboarding/{onboarding_id}/timeline", response_model=ActivityList)
async def list_onboarding_timeline(
    onboarding_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_ONBOARDING_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """The onboarding's own immutable timeline (assignments, stage changes, notes), newest first."""
    return await onboarding_service.list_timeline(db, ctx, onboarding_id, limit, offset)


# =============================================================================
# Phase 5 - dashboard and reports
#
# Read-only, and every number is aggregated by the database (repositories/reports.py). Each route states the permission keys
# that open it; WHAT it counts is limited by the same scope rules as the records themselves (services/report_scope.py):
# a caller who is not in the team (sales.leads.scope_all) gets only their own numbers - the lead scope for leads, the work they
# did or own for calls / follow-ups / demos / trials - and asking about another employee is a 403, because even a one-person
# aggregate is that person's private performance. Sections the caller may not see are `null` in the response. The period is
# `range` (today | this_week | this_month | last_month | custom, default this_month) or a custom date_from + date_to in Baghdad time,
# at most 366 days. Static paths only - there is nothing to collide with /<kind>/{id}.
# =============================================================================

@sales_router.get("/dashboard", response_model=DashboardOut)
async def sales_dashboard(
    params: DashboardParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_DASHBOARD_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """KPIs, pipeline, per-employee performance (team only), sources, campaigns, activity and onboarding - each section only
    when the caller may see the records behind it."""
    return await dashboard_service.dashboard(db, build_scope(ctx, params.to_period(), params.employee_id))


@sales_router.get("/reports/leads", response_model=LeadReportOut)
async def report_leads(
    params: LeadListParams = Depends(),
    sort: Literal[SORT_FIELDS] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Lead report: the leads created in the period, paginated, with the totals of the whole selection."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.lead_report(
        db, scope, source=params.source, campaign_id=params.campaign_id, priority=params.priority, stages=params.stages(),
        sort=sort, descending=order == "desc", limit=limit, offset=offset,
    )


@sales_router.get("/reports/pipeline", response_model=PipelineReportOut)
async def report_pipeline(
    params: LeadReportParams = Depends(),
    basis: Literal["created", "all"] = Query("created", description="created = the leads that entered the period; all = every lead in scope, whenever created"),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Pipeline report: leads and estimated value per CURRENT stage."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.pipeline_report(db, scope, basis=basis, source=params.source, campaign_id=params.campaign_id)


@sales_router.get("/reports/employee-performance", response_model=EmployeePerformanceOut)
async def report_employee_performance(
    params: LeadReportParams = Depends(),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW, perms.PERM_LEADS_SCOPE_ALL)),
    db: AsyncSession = Depends(get_db),
):
    """Employee performance: leads, won, lost, conversion and activity per employee. Team only (sales.leads.scope_all)."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.employee_performance_report(db, scope, source=params.source, campaign_id=params.campaign_id, limit=limit, offset=offset)


@sales_router.get("/reports/sources", response_model=SourceReportOut)
async def report_sources(
    params: LeadReportParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Lead source performance: leads, won, lost, conversion and estimated value per source."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.source_report(db, scope, campaign_id=params.campaign_id)


@sales_router.get("/reports/campaigns", response_model=CampaignReportOut)
async def report_campaigns(
    params: LeadReportParams = Depends(),
    campaign_status: Optional[Literal["active", "paused", "completed"]] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW, perms.PERM_CAMPAIGNS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Campaign performance: leads, won, lost, conversion and estimated value per campaign that received leads."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.campaign_report(
        db, scope, source=params.source, campaign_id=params.campaign_id, campaign_status=campaign_status, limit=limit, offset=offset
    )


@sales_router.get("/reports/conversion", response_model=ConversionReportOut)
async def report_conversion(
    params: LeadReportParams = Depends(),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_LEADS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Conversion: won / leads that entered the period, leads closed in it, lost reasons and the trend."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.conversion_report(db, scope, source=params.source, campaign_id=params.campaign_id)


@sales_router.get("/reports/activities", response_model=ActivityReportOut)
async def report_activities(
    params: DashboardParams = Depends(),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Activity: calls, follow-ups, demos and trials in the period, per kind and per employee. A kind the caller may not
    view is `null`; a caller who may view none of them gets 403."""
    scope = build_scope(ctx, params.to_period(), params.employee_id)
    return await reports_service.activity_report(db, scope, limit=limit, offset=offset)


@sales_router.get("/reports/onboarding", response_model=OnboardingReportOut)
async def report_onboarding(
    params: OnboardingReportParams = Depends(),
    sort: str = Query("started_at", pattern="^(" + "|".join(ONBOARDING_SORT_FIELDS) + ")$"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: StaffContext = Depends(require_permission(perms.PERM_REPORTS_VIEW, perms.PERM_ONBOARDING_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Onboarding: the records that started in the period by stage and by onboarding employee, paginated - limited to the
    caller's onboarding scope."""
    scope = build_scope(ctx, params.to_period(), None)
    return await reports_service.onboarding_report(
        db, scope, stages=params.stages(), assigned_to=_person(params.assigned_to, ctx, "assigned_to"),
        sort=sort, descending=order == "desc", limit=limit, offset=offset,
    )
