"""Duplicate detection for leads - against other leads and against existing JAZ companies.

The point is to stop an accidental second record for a business we already know (or already SELL to), while
never deciding on the user's behalf: nothing is merged, nothing is deleted, nothing is blocked outright. A
duplicate is REPORTED; the caller then explicitly continues (confirm_duplicates) or walks away.

Two grades, deliberately conservative about what counts as certain:

  exact     the same normalized contact identifier: phone, WhatsApp (a number matches a number in EITHER field),
            email, website; and, against JAZ companies, an owner's email or phone.
  possible  weaker evidence: the same business name in the same city; a phone number that matches only after
            ignoring its country-code prefix (same last digits); a lead whose name equals a JAZ company's name.

Continuing past a `possible` duplicate needs only the permission to create/edit the lead. Continuing past an
`exact` one needs sales.leads.override_duplicates (Sales Manager, Super Admin) - a data-entry clerk or a sales
employee cannot knowingly create a second record for the same phone number or an existing customer's email.
Every override is written into the lead's timeline with the ids that were overridden.

What a caller learns about a matching lead depends on whether they may see it (lead_access): full reference for
leads in their scope, and only the bare facts - name, city, stage, that it is owned - for the rest.
"""
import uuid
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from sales import permissions as perms
from sales.repositories import leads as leads_repo
from sales.repositories.leads import CompanyOwnerRow, LeadMatchRow
from sales.services.access import StaffContext
from sales.services.common import field_error
from sales.services.lead_access import can_see
from sales.services.normalize import norm_email, norm_phone, norm_text, norm_website, phone_tail

EXACT = "exact"
POSSIBLE = "possible"

# Which identity checks to run. Editing a lead runs only those whose normalized value actually changed.
CHECK_PHONE = "phone"
CHECK_WHATSAPP = "whatsapp"
CHECK_EMAIL = "email"
CHECK_WEBSITE = "website"
CHECK_NAME_CITY = "name_city"
ALL_CHECKS: FrozenSet[str] = frozenset({CHECK_PHONE, CHECK_WHATSAPP, CHECK_EMAIL, CHECK_WEBSITE, CHECK_NAME_CITY})

# lead field -> the check that reads it
IDENTITY_FIELD_CHECKS: Dict[str, str] = {
    "phone": CHECK_PHONE,
    "whatsapp": CHECK_WHATSAPP,
    "email": CHECK_EMAIL,
    "website": CHECK_WEBSITE,
    "business_name": CHECK_NAME_CITY,
    "city": CHECK_NAME_CITY,
}

MAX_COMPANY_MATCHES = 10


@dataclass(frozen=True)
class Keys:
    """The normalized identity of a candidate lead."""

    name: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None

    @staticmethod
    def of(*, business_name=None, city=None, phone=None, whatsapp=None, email=None, website=None) -> "Keys":
        return Keys(
            name=norm_text(business_name), city=norm_text(city), phone=norm_phone(phone),
            whatsapp=norm_phone(whatsapp), email=norm_email(email), website=norm_website(website),
        )


def checks_for_changed_fields(fields: Iterable[str]) -> FrozenSet[str]:
    return frozenset(IDENTITY_FIELD_CHECKS[f] for f in fields if f in IDENTITY_FIELD_CHECKS)


@dataclass(frozen=True)
class Match:
    field: str      # the candidate's field that matched: phone | whatsapp | email | website | business_name
    kind: str       # exact | possible
    matched_on: str  # what it matched on the other record (see the classifiers)


def _number_matches(field_name: str, number: Optional[str], others: Dict[str, Optional[str]]) -> List[Match]:
    """`number` (one of the candidate's phone/WhatsApp values) against the other record's numbers."""
    out: List[Match] = []
    if not number:
        return out
    tail = phone_tail(number)
    for other_field, other in others.items():
        if not other:
            continue
        if other == number:
            out.append(Match(field_name, EXACT, other_field))
        elif tail is not None and phone_tail(other) == tail:
            out.append(Match(field_name, POSSIBLE, other_field))
    return out


def classify_lead(keys: Keys, row: LeadMatchRow, checks: FrozenSet[str]) -> List[Match]:
    matches: List[Match] = []
    others = {"phone": row.phone_norm, "whatsapp": row.whatsapp_norm}
    if CHECK_PHONE in checks:
        matches += _number_matches("phone", keys.phone, others)
    if CHECK_WHATSAPP in checks:
        matches += _number_matches("whatsapp", keys.whatsapp, others)
    if CHECK_EMAIL in checks and keys.email and keys.email == row.email_norm:
        matches.append(Match("email", EXACT, "email"))
    if CHECK_WEBSITE in checks and keys.website and keys.website == row.website_norm:
        matches.append(Match("website", EXACT, "website"))
    if CHECK_NAME_CITY in checks and keys.name and keys.city and keys.name == row.name_norm and keys.city == row.city_norm:
        matches.append(Match("business_name", POSSIBLE, "business_name_city"))
    return matches


def classify_company(keys: Keys, company: CompanyOwnerRow, checks: FrozenSet[str]) -> List[Match]:
    matches: List[Match] = []
    owner_phone = norm_phone(company.owner_phone)
    if CHECK_PHONE in checks:
        matches += _number_matches("phone", keys.phone, {"owner_phone": owner_phone})
    if CHECK_WHATSAPP in checks:
        matches += _number_matches("whatsapp", keys.whatsapp, {"owner_phone": owner_phone})
    if CHECK_EMAIL in checks and keys.email and keys.email == norm_email(company.owner_email):
        matches.append(Match("email", EXACT, "owner_email"))
    if CHECK_NAME_CITY in checks and keys.name and keys.name == norm_text(company.company_name):
        matches.append(Match("business_name", POSSIBLE, "company_name"))
    return matches


def _severity(matches: List[Match]) -> str:
    return EXACT if any(m.kind == EXACT for m in matches) else POSSIBLE


@dataclass
class LeadMatch:
    row: LeadMatchRow
    matches: List[Match]
    severity: str = field(init=False)

    def __post_init__(self):
        self.severity = _severity(self.matches)


@dataclass
class CompanyMatch:
    company: CompanyOwnerRow
    matches: List[Match]
    severity: str = field(init=False)

    def __post_init__(self):
        self.severity = _severity(self.matches)


@dataclass
class DuplicateReport:
    leads: List[LeadMatch] = field(default_factory=list)
    companies: List[CompanyMatch] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.leads and not self.companies

    @property
    def has_exact(self) -> bool:
        return any(m.severity == EXACT for m in (*self.leads, *self.companies))

    @property
    def has_possible(self) -> bool:
        return any(m.severity == POSSIBLE for m in (*self.leads, *self.companies))


def _exact_first(matches):
    return sorted(matches, key=lambda m: 0 if m.severity == EXACT else 1)  # stable: recency order kept within a grade


async def check(
    db: AsyncSession,
    keys: Keys,
    *,
    exclude_lead_id: Optional[uuid.UUID] = None,
    checks: FrozenSet[str] = ALL_CHECKS,
) -> DuplicateReport:
    """Everything that collides with `keys`, on the given checks. No permission logic here (see enforce/present)."""
    numbers: Set[str] = set()
    if CHECK_PHONE in checks and keys.phone:
        numbers.add(keys.phone)
    if CHECK_WHATSAPP in checks and keys.whatsapp:
        numbers.add(keys.whatsapp)
    name_city = (keys.name, keys.city) if CHECK_NAME_CITY in checks and keys.name and keys.city else None

    candidates = await leads_repo.find_lead_candidates(
        db,
        numbers=numbers,
        email=keys.email if CHECK_EMAIL in checks else None,
        website=keys.website if CHECK_WEBSITE in checks else None,
        name_city=name_city,
        exclude_id=exclude_lead_id,
    )
    lead_matches = [LeadMatch(row, m) for row in candidates if (m := classify_lead(keys, row, checks))]

    company_matches: List[CompanyMatch] = []
    # only worth reading the companies when there is something to compare
    if numbers or (CHECK_EMAIL in checks and keys.email) or (CHECK_NAME_CITY in checks and keys.name):
        for company in await leads_repo.list_company_owner_rows(db):
            if m := classify_company(keys, company, checks):
                company_matches.append(CompanyMatch(company, m))

    return DuplicateReport(
        leads=_exact_first(lead_matches),
        companies=_exact_first(company_matches)[:MAX_COMPANY_MATCHES],
    )


async def check_companies(
    db: AsyncSession, keys: Keys, *, checks: FrozenSet[str] = frozenset({CHECK_PHONE, CHECK_EMAIL, CHECK_NAME_CITY})
) -> List[CompanyMatch]:
    """The JAZ companies that collide with `keys` - and only those (converting a lead is about the company it would
    create, not about other leads). Same classification as the lead checks: an owner's email or phone equal to the
    candidate's is `exact`; the same company name, or a phone that matches only after its country-code prefix, is
    `possible`. Exact matches first. No permission logic here (the conversion service decides what to do with them)."""
    if not (keys.phone or keys.email or keys.name):
        return []
    matches = [CompanyMatch(company, m) for company in await leads_repo.list_company_owner_rows(db) if (m := classify_company(keys, company, checks))]
    return _exact_first(matches)[:MAX_COMPANY_MATCHES]


def can_override(report: DuplicateReport, ctx: StaffContext) -> bool:
    """Possible duplicates: anyone who may write the lead may continue. Exact ones: only with the override permission."""
    return (not report.has_exact) or ctx.has(perms.PERM_LEADS_OVERRIDE_DUPLICATES)


def _match_out(match: Match) -> dict:
    return {"field": match.field, "kind": match.kind, "matched_on": match.matched_on}


def present(report: DuplicateReport, ctx: StaffContext) -> dict:
    """The report as the API returns it, shaped by what the caller may see (see the module docstring)."""
    leads = []
    for item in report.leads:
        row = item.row
        visible = can_see(ctx, created_by=row.created_by, assigned_to=row.assigned_to)
        leads.append({
            "id": str(row.id) if visible else None,
            "business_name": row.business_name,
            "city": row.city,
            "pipeline_stage": row.pipeline_stage,
            "archived": row.archived,
            "assigned": row.assigned_to is not None,
            "assigned_to": {"id": str(row.assigned_to), "name": row.assignee_name}
            if visible and row.assigned_to is not None else None,
            "in_scope": visible,
            "severity": item.severity,
            "matches": [_match_out(m) for m in item.matches],
        })
    companies = [
        {
            "id": str(item.company.company_id),
            "name": item.company.company_name,
            "severity": item.severity,
            "matches": [_match_out(m) for m in item.matches],
        }
        for item in report.companies
    ]
    return {
        "has_exact": report.has_exact,
        "has_possible": report.has_possible,
        "can_override": can_override(report, ctx),
        "leads": leads,
        "companies": companies,
    }


def override_summary(report: DuplicateReport) -> dict:
    """What goes into the lead's timeline when a caller knowingly continued past duplicates."""
    return {
        "exact": sum(1 for m in (*report.leads, *report.companies) if m.severity == EXACT),
        "possible": sum(1 for m in (*report.leads, *report.companies) if m.severity == POSSIBLE),
        "lead_ids": [str(m.row.id) for m in report.leads],
        "company_ids": [str(m.company.company_id) for m in report.companies],
    }


async def enforce(
    db: AsyncSession,
    ctx: StaffContext,
    keys: Keys,
    *,
    confirm: bool,
    exclude_lead_id: Optional[uuid.UUID] = None,
    checks: FrozenSet[str] = ALL_CHECKS,
) -> Optional[dict]:
    """The write-time gate. Returns None when there is nothing to override, else the override summary for the
    timeline. Raises 409 (duplicates found, nothing written) unless the caller confirmed, and 403 when they
    confirmed past an exact duplicate without the override permission."""
    report = await check(db, keys, exclude_lead_id=exclude_lead_id, checks=checks)
    if report.is_empty:
        return None
    payload = present(report, ctx)
    if not confirm:
        kind = "Exact duplicates" if report.has_exact else "Possible duplicates"
        raise field_error(
            "duplicates", f"{kind} found. Review them, then confirm to continue.", 409,
            code="duplicates_found", duplicates=payload,
        )
    if not can_override(report, ctx):
        raise field_error(
            "duplicates",
            "An exact duplicate of an existing lead or JAZ company can only be overridden by a Sales Manager.",
            403, code="duplicate_override_forbidden", duplicates=payload,
        )
    return override_summary(report)
