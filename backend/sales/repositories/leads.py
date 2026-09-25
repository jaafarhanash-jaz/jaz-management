"""Lead persistence.

Every list/detail read joins the campaign and the two user references (assignee, creator) in ONE query, so a
page of leads costs one query for rows + one for the total - never one per lead. The single place that writes a
lead's raw fields is apply_fields(), and it keeps the normalized duplicate-detection columns in step with them.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import Company, User
from sales import permissions as perms
from sales.constants import CLOSED_STAGES, PIPELINE_STAGES, PRIORITY_RANK, STAGE_RANK
from sales.models import SalesCampaign, SalesLead, SalesLeadSource, StaffRole, StaffRolePermission, StaffUserRole
from sales.timezone import day_start
from sales.services.normalize import (
    digits_only,
    norm_email,
    norm_phone,
    norm_text,
    norm_website,
    phone_tail,
)

# The user-editable lead columns (everything a create/update body may carry). Not here on purpose: id, stage,
# lost_reason, assignment, archive and audit columns - those change only through their dedicated operations.
RAW_FIELDS: Tuple[str, ...] = (
    "business_name", "business_type", "description",
    "contact_name", "contact_position", "phone", "whatsapp", "email", "website",
    "country", "city", "address", "latitude", "longitude",
    "source", "campaign_id", "priority", "estimated_value", "notes",
)


def apply_fields(lead: SalesLead, values: Dict[str, Any]) -> None:
    """Set raw lead fields and recompute the normalized columns. The only writer of either."""
    for key, value in values.items():
        if key not in RAW_FIELDS:
            raise ValueError(f"not a lead field: {key}")
        setattr(lead, key, value)
    lead.name_norm = norm_text(lead.business_name) or ""
    lead.city_norm = norm_text(lead.city)
    lead.phone_norm = norm_phone(lead.phone)
    lead.whatsapp_norm = norm_phone(lead.whatsapp)
    lead.email_norm = norm_email(lead.email)
    lead.website_norm = norm_website(lead.website)


# ---- reads with their references ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeadRow:
    lead: SalesLead
    campaign_name: Optional[str]
    campaign_status: Optional[str]
    assignee_name: Optional[str]
    assignee_status: Optional[str]
    creator_name: Optional[str]


_Assignee = aliased(User)
_Creator = aliased(User)


def _row_select():
    return (
        select(
            SalesLead,
            SalesCampaign.name,
            SalesCampaign.status,
            _Assignee.name,
            _Assignee.status,
            _Creator.name,
        )
        .select_from(SalesLead)
        .outerjoin(SalesCampaign, SalesCampaign.id == SalesLead.campaign_id)
        .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
        .outerjoin(_Creator, _Creator.id == SalesLead.created_by)
    )


def _to_row(t) -> LeadRow:
    return LeadRow(lead=t[0], campaign_name=t[1], campaign_status=t[2], assignee_name=t[3], assignee_status=t[4], creator_name=t[5])


async def get_row(db: AsyncSession, lead_id: uuid.UUID) -> Optional[LeadRow]:
    # populate_existing: the lead may already be in the session (a mutation just flushed it); re-read what the
    # database now holds (server-side updated_at, assigned_at, ...) instead of trusting the in-memory copy.
    result = await db.execute(_row_select().where(SalesLead.id == lead_id), execution_options={"populate_existing": True})
    row = result.first()
    return _to_row(row) if row else None


async def get_lead(db: AsyncSession, lead_id: uuid.UUID, *, for_update: bool = False) -> Optional[SalesLead]:
    """The bare lead. for_update=True takes a row lock: every state-changing operation reads, validates and writes
    under it, so two concurrent operations on one lead are applied one after the other, never interleaved."""
    stmt = select(SalesLead).where(SalesLead.id == lead_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_leads_for_update(db: AsyncSession, lead_ids: Sequence[uuid.UUID]) -> List[SalesLead]:
    """Locked in id order, so two overlapping bulk operations cannot deadlock each other."""
    if not lead_ids:
        return []
    result = await db.execute(select(SalesLead).where(SalesLead.id.in_(lead_ids)).order_by(SalesLead.id).with_for_update())
    return list(result.scalars().all())


# ---- sources -----------------------------------------------------------------------------------------------

async def list_sources(db: AsyncSession, *, active_only: bool = True) -> List[SalesLeadSource]:
    stmt = select(SalesLeadSource).order_by(SalesLeadSource.sort_order, SalesLeadSource.key)
    if active_only:
        stmt = stmt.where(SalesLeadSource.is_active.is_(True))
    return list((await db.execute(stmt)).scalars().all())


async def is_active_source(db: AsyncSession, key: str) -> bool:
    return (
        await db.execute(select(SalesLeadSource.key).where(SalesLeadSource.key == key, SalesLeadSource.is_active.is_(True)))
    ).first() is not None


# ---- listing -----------------------------------------------------------------------------------------------

@dataclass
class LeadFilters:
    q: Optional[str] = None
    stages: Optional[Sequence[str]] = None
    assigned_to: Optional[uuid.UUID] = None        # a specific employee...
    unassigned: bool = False                        # ...or nobody
    source: Optional[str] = None
    campaign_id: Optional[uuid.UUID] = None
    priority: Optional[str] = None
    created_from: Optional[date] = None             # inclusive calendar dates, Baghdad time
    created_to: Optional[date] = None               # inclusive
    archived: str = "exclude"                       # exclude | only | all


_PHONE_LOOKING = re.compile(r"[\d\s+().\-]+")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(visibility, filters: LeadFilters, *, with_stage: bool = True) -> list:
    conds = [visibility]
    if filters.archived == "exclude":
        conds.append(SalesLead.archived_at.is_(None))
    elif filters.archived == "only":
        conds.append(SalesLead.archived_at.is_not(None))
    if with_stage and filters.stages:
        conds.append(SalesLead.pipeline_stage.in_(list(filters.stages)))
    if filters.unassigned:
        conds.append(SalesLead.assigned_to.is_(None))
    elif filters.assigned_to is not None:
        conds.append(SalesLead.assigned_to == filters.assigned_to)
    if filters.source:
        conds.append(SalesLead.source == filters.source)
    if filters.campaign_id is not None:
        conds.append(SalesLead.campaign_id == filters.campaign_id)
    if filters.priority:
        conds.append(SalesLead.priority == filters.priority)
    if filters.created_from is not None:
        conds.append(SalesLead.created_at >= day_start(filters.created_from))
    if filters.created_to is not None:
        conds.append(SalesLead.created_at < day_start(filters.created_to + timedelta(days=1)))
    if filters.q:
        needle = filters.q.replace("\x00", "").strip()   # NUL cannot be stored, so it can never match; and must not reach the driver
        if needle:
            pattern = f"%{_escape_like(needle)}%"
            search = [
                SalesLead.business_name.ilike(pattern, escape="\\"),
                SalesLead.contact_name.ilike(pattern, escape="\\"),
                SalesLead.email.ilike(pattern, escape="\\"),
                SalesLead.phone.ilike(pattern, escape="\\"),
                SalesLead.whatsapp.ilike(pattern, escape="\\"),
            ]
            # A query that LOOKS like a phone number (digits and phone punctuation only) is also matched against the
            # digits of stored numbers, so another spacing - or the national form with its leading trunk 0 - still finds
            # it ("0770 555 1234" finds "+964 770 555 1234"). Text that merely contains digits ("Pharmacy 1234") is
            # not treated as a number: it would match unrelated phones.
            digits = digits_only(needle).lstrip("0") if _PHONE_LOOKING.fullmatch(needle) else ""
            if len(digits) >= 3:
                search += [SalesLead.phone_norm.like(f"%{digits}%"), SalesLead.whatsapp_norm.like(f"%{digits}%")]
            conds.append(or_(*search))
    return conds


def _sort_expression(sort: str):
    if sort == "priority":
        return case(*[(SalesLead.priority == p, r) for p, r in PRIORITY_RANK.items()], else_=0)
    if sort == "pipeline_stage":
        return case(*[(SalesLead.pipeline_stage == s, r) for s, r in STAGE_RANK.items()], else_=0)
    return {
        "created_at": SalesLead.created_at,
        "updated_at": SalesLead.updated_at,
        "business_name": SalesLead.name_norm,
        "estimated_value": SalesLead.estimated_value,
    }[sort]


SORT_FIELDS = ("created_at", "updated_at", "business_name", "priority", "estimated_value", "pipeline_stage")


async def list_leads(
    db: AsyncSession, *, visibility, filters: LeadFilters, sort: str, descending: bool, limit: int, offset: int
) -> Tuple[List[LeadRow], int]:
    conds = _conditions(visibility, filters)
    total = (await db.execute(select(func.count()).select_from(SalesLead).where(*conds))).scalar_one()
    expr = _sort_expression(sort)
    ordered = expr.desc() if descending else expr.asc()
    if sort == "estimated_value":
        ordered = ordered.nulls_last()
    stmt = _row_select().where(*conds).order_by(ordered, SalesLead.id.desc() if descending else SalesLead.id).limit(limit).offset(offset)
    return [_to_row(t) for t in (await db.execute(stmt)).all()], total


async def stage_counts(db: AsyncSession, *, visibility, filters: LeadFilters) -> Dict[str, int]:
    conds = _conditions(visibility, filters, with_stage=False)
    result = await db.execute(
        select(SalesLead.pipeline_stage, func.count()).where(*conds).group_by(SalesLead.pipeline_stage)
    )
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    counts.update({stage: n for stage, n in result.all()})
    return counts


# ---- assignees ---------------------------------------------------------------------------------------------

def _may_receive_leads(user_col):
    """EXISTS: the user holds sales.leads.scope_assigned through an ACTIVE grant of an ACTIVE role. A revoked
    grant or a retired role therefore stops a person from receiving assignments, exactly as it stops their access."""
    return exists(
        select(1)
        .select_from(StaffUserRole)
        .join(StaffRole, and_(StaffRole.id == StaffUserRole.role_id, StaffRole.is_active.is_(True)))
        .join(
            StaffRolePermission,
            and_(
                StaffRolePermission.role_id == StaffRole.id,
                StaffRolePermission.permission_key == perms.PERM_LEADS_SCOPE_ASSIGNED,
            ),
        )
        .where(StaffUserRole.user_id == user_col, StaffUserRole.revoked_at.is_(None))
    )


def _is_staff_account_usable():
    return and_(User.role == perms.PLATFORM_ROLE_STAFF, User.status == "active", User.deleted_at.is_(None))


async def get_assignable_user(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> Optional[User]:
    """The user, if - and only if - they may receive lead assignments right now: an ACTIVE `jaz_staff` account,
    not deleted, holding the assigned-leads scope through an active role. lock=True takes a shared row lock, so
    the account cannot be deactivated by a concurrent request between this check and the assignment committing."""
    stmt = select(User).where(User.id == user_id, _is_staff_account_usable(), _may_receive_leads(User.id))
    if lock:
        stmt = stmt.with_for_update(read=True, of=User)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_assignees(db: AsyncSession) -> List[Tuple[User, int]]:
    """Everyone who may receive assignments, with their OPEN workload (not archived, not won/lost)."""
    open_counts = (
        select(SalesLead.assigned_to.label("uid"), func.count().label("n"))
        .where(SalesLead.archived_at.is_(None), SalesLead.pipeline_stage.not_in(list(CLOSED_STAGES)))
        .group_by(SalesLead.assigned_to)
        .subquery()
    )
    result = await db.execute(
        select(User, func.coalesce(open_counts.c.n, 0))
        .outerjoin(open_counts, open_counts.c.uid == User.id)
        .where(_is_staff_account_usable(), _may_receive_leads(User.id))
        .order_by(User.name, User.id)
    )
    return [(user, int(n)) for user, n in result.all()]


# ---- duplicate detection -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class LeadMatchRow:
    id: uuid.UUID
    business_name: str
    city: Optional[str]
    pipeline_stage: str
    created_by: uuid.UUID
    assigned_to: Optional[uuid.UUID]
    assignee_name: Optional[str]
    archived: bool
    created_at: datetime
    name_norm: str
    city_norm: Optional[str]
    phone_norm: Optional[str]
    whatsapp_norm: Optional[str]
    email_norm: Optional[str]
    website_norm: Optional[str]


DUPLICATE_LIMIT = 25  # matches returned per check; the most recent first


async def find_lead_candidates(
    db: AsyncSession,
    *,
    numbers: Iterable[str],
    email: Optional[str],
    website: Optional[str],
    name_city: Optional[Tuple[str, str]],
    exclude_id: Optional[uuid.UUID],
) -> List[LeadMatchRow]:
    """Leads that share at least one identity key with the candidate (archived ones included - restoring one is
    usually the right answer to a duplicate). This is a superset filter; the exact/possible classification is
    done in services/duplicates.py on the normalized values these rows carry."""
    conds = []
    for number in set(numbers):
        conds += [SalesLead.phone_norm == number, SalesLead.whatsapp_norm == number]
        tail = phone_tail(number)
        if tail:
            conds += [
                and_(func.length(SalesLead.phone_norm) >= 9, func.right(SalesLead.phone_norm, 9) == tail),
                and_(func.length(SalesLead.whatsapp_norm) >= 9, func.right(SalesLead.whatsapp_norm, 9) == tail),
            ]
    if email:
        conds.append(SalesLead.email_norm == email)
    if website:
        conds.append(SalesLead.website_norm == website)
    if name_city:
        conds.append(and_(SalesLead.name_norm == name_city[0], SalesLead.city_norm == name_city[1]))
    if not conds:
        return []

    stmt = (
        select(
            SalesLead.id, SalesLead.business_name, SalesLead.city, SalesLead.pipeline_stage, SalesLead.created_by,
            SalesLead.assigned_to, _Assignee.name, SalesLead.archived_at.is_not(None), SalesLead.created_at,
            SalesLead.name_norm, SalesLead.city_norm, SalesLead.phone_norm, SalesLead.whatsapp_norm,
            SalesLead.email_norm, SalesLead.website_norm,
        )
        .select_from(SalesLead)
        .outerjoin(_Assignee, _Assignee.id == SalesLead.assigned_to)
        .where(or_(*conds))
        .order_by(SalesLead.created_at.desc(), SalesLead.id)
        .limit(DUPLICATE_LIMIT)
    )
    if exclude_id is not None:
        stmt = stmt.where(SalesLead.id != exclude_id)
    return [LeadMatchRow(*row) for row in (await db.execute(stmt)).all()]


@dataclass(frozen=True)
class CompanyOwnerRow:
    company_id: uuid.UUID
    company_name: str
    owner_email: Optional[str]
    owner_phone: Optional[str]


async def list_company_owner_rows(db: AsyncSession) -> List[CompanyOwnerRow]:
    """Every live JAZ company with its owner's email and phone - read-only, for the duplicate check that keeps
    a lead from becoming an accidental second customer record. The values are normalized in Python by the same
    functions used for leads, so both sides always agree. (One small query over the companies table; if the
    platform ever holds many thousands of companies, this is the spot to add an indexed prefilter.)"""
    result = await db.execute(
        select(Company.id, Company.name, User.email, User.phone)
        .join(User, User.id == Company.owner_id)
        .where(Company.deleted_at.is_(None), User.deleted_at.is_(None))
    )
    return [CompanyOwnerRow(*row) for row in result.all()]
