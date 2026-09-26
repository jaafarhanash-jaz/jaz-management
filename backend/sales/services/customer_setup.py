"""Customer Setup (simplified workflow): the Sales Employee's "Agreed" - Lead -> Customer -> JAZ Company -> Subscription, and
optionally the company's first employees and tasks - in ONE request, without an Onboarding Employee.

Everything the platform already does is done by the platform's own code, never reimplemented here:
  * the company and its owner account     services/admin.create_company       (same as the Super Admin screen / Phase 4)
  * the subscription period               services/admin.activate_subscription (same as the Super Admin's Activate)
  * each employee account                 services/employees.create_employee   (same as the Company Owner's Add employee -
                                                                                 the plan's employee limit included)
  * each task                             services/tasks.create_task           (same as the Company Owner's New task)
Around them this module adds only what Sales needs:

  * WHO. sales.customers.setup AND sales.leads.change_stage (Sales Manager, Sales Employee, Super Admin), on a lead within
    the caller's lead scope - a Sales Employee reaches only the leads assigned to them (403 for anybody else's, 404 unknown).
  * WHICH LEADS. Not archived, not lost, owned by somebody (the owner is who won it), not converted yet. An open lead is
    moved to `won` by the setup itself (the "Agreed" IS the win); a lead that is already won stays as it is.
  * THE SAME GUARDS AS A CONVERSION. The owner's email / phone must be free, an exact JAZ company duplicate BLOCKS, a possible
    one needs confirm_duplicates (services/conversion.py - shared, not copied). The employees get the same account rules
    (a free, valid, unrepeated email and phone; the core password rule; the plan's employee limit).
  * SUBSCRIPTION. `trial`: active on the chosen plan for EXACTLY 7 x 24 hours from the moment it is created (25/09 15:00 ->
    02/10 15:00); the end is an exact instant (companies.subscription_ends_exactly) and the platform's own expiry check
    (services/auth.subscription_has_ended) ends it at that moment. `paid`: active on the chosen plan from today (UTC
    calendar date) to today + the plan's months x 30 days - the platform's own Renew arithmetic and day-level meaning.
    Paid needs NO payment step in Sales (the MVP decision): the subscription is active as soon as the setup completes.
  * OPTIONAL PARTS. Employees and tasks may be added now or left to the customer. A task is given to one of the employees
    added in the same request (by index) - there is nobody else in a brand-new company to give it to.
  * NO ONBOARDING RECORD. The setup IS the onboarding: the customer is created `activated`, and no sales_onboarding row is
    made (the onboarding tables stay, dormant, for a later expansion).
  * IDEMPOTENT, ALL OR NOTHING. The lead row is locked for the whole request and a customer is unique per lead: a repeat
    returns the existing customer (already_converted=true) and creates nothing. Every write - stage change, company, owner,
    subscription, customer, employees, tasks, timeline, audit - is in the request's single transaction.
  * NO SECRETS. The owner's and the employees' initial passwords are SecretStr, unwrapped only here, handed to the core
    services and to nothing else: never returned, logged, put on the timeline or audited.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.subscription_plans as plans_repo
import services.admin as admin_service
import services.employees as employees_service
import services.tasks as tasks_service
from models import User
from sales.constants import (
    DAYS_PER_PLAN_MONTH,
    STAGE_WON,
    TRIAL_DAYS,
    TRIAL_DURATION,
)
from sales.models import SalesCustomer, SalesLead
from sales.repositories import customers as customers_repo
from sales.services import activity as activity_service
from sales.services import audit as audit_service
from sales.services import batches as batches_service
from sales.services import conversion as conversion
from sales.services import customers as customers_service
from sales.services import duplicates as duplicates_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from sales.services.normalize import norm_email, norm_phone
from sales.timezone import app_today
from services.auth import validate_password_strength
from services.identifiers import phone_error

logger = logging.getLogger("sales.customer_setup")

# The customer a setup creates is live from the first moment: the setup was its onboarding.
SETUP_CUSTOMER_STATUS = "activated"

_EMPLOYEE_MESSAGES = {
    "employee_phone_invalid": None,     # the shared phone rule's own message
    "employee_email_registered": "This email is already registered to a JAZ account",
    "employee_phone_registered": "This phone number is already registered to a JAZ account",
    "employee_email_repeated": "This email is used twice in this setup (the owner and each employee need their own)",
    "employee_phone_repeated": "This phone number is used twice in this setup (the owner and each employee need their own)",
    "employee_password_weak": None,     # the core password rule's own message
}


def _today_utc():
    return datetime.now(timezone.utc).date()


def subscription_period(subscription_type: str, plan, now: Optional[datetime] = None) -> Tuple[datetime, datetime, bool]:
    """(start, end, exact) of the subscription a setup starts the company on, as UTC datetimes.
      * trial: start = the creation instant (to the second), end = start + exactly 7 x 24 hours, exact = True;
      * paid:  start = today 00:00 UTC, end = today + plan months x 30 days (00:00 UTC), exact = False - calendar dates,
               the end day included, exactly like the Super Admin's Activate / Renew."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if subscription_type == "trial":
        start = now.replace(microsecond=0)
        return start, start + TRIAL_DURATION, True
    # A paid period is in the PLATFORM's calendar - the UTC date its expiry check compares against (not the Sales reporting
    # day of sales/timezone.py) - so the dates are turned into instants by the platform's own converter, exactly as the Super
    # Admin's Activate does with the dates typed on its screen.
    first_day = now.date()
    last_day = first_day + timedelta(days=DAYS_PER_PLAN_MONTH * int(plan.duration_months))
    return admin_service.parse_dateish(first_day.isoformat()), admin_service.parse_dateish(last_day.isoformat()), False


def _lead_out(lead: SalesLead, owner: Optional[User]) -> dict:
    return {
        "id": str(lead.id), "business_name": lead.business_name, "pipeline_stage": lead.pipeline_stage,
        "archived": lead.archived_at is not None,
        "assigned_to": {"id": str(owner.id), "name": owner.name} if owner is not None else None,
    }


# ---- preflight (read-only) ---------------------------------------------------------------------------------------

async def preflight(db: AsyncSession, ctx: StaffContext, lead_id: str, body) -> dict:
    """What completing the setup with these values would do - without doing any of it."""
    lead = await conversion._load_lead(db, ctx, lead_id, for_update=False)
    owner = await db.get(User, lead.assigned_to) if lead.assigned_to else None
    values = conversion._effective_values(lead, body)
    plans = sorted(await plans_repo.list_active(db), key=lambda p: (float(p.price or 0), p.name))
    common = {
        "lead": _lead_out(lead, owner), "values": values, "today": _today_utc(), "now": datetime.now(timezone.utc),
        "local_today": app_today(),
        "marks_won": lead.pipeline_stage != STAGE_WON,
        "subscription_options": [
            {"type": "trial", "days": TRIAL_DAYS, "hours": int(TRIAL_DURATION.total_seconds() // 3600), "exact": True},
            {"type": "paid", "days": None, "hours": None, "exact": False},
        ],
    }

    existing = await customers_repo.get_row_by_lead(db, lead.id)
    if existing is not None:
        return {
            **common, "already_converted": True, "customer": customers_service.customer_out(existing, ctx),
            "blockers": [conversion._blocker("already_converted", "lead", "This lead has already been converted")],
            "duplicates": conversion._duplicates_out([]), "requires_confirmation": False, "can_setup": False, "plans": [],
        }

    blockers: List[dict] = []
    problem = conversion.setup_lead_blocker(lead)
    if problem:
        blockers.append(conversion._blocker(problem, "lead", conversion.SETUP_LEAD_BLOCKERS[problem]))
    blockers += await conversion._account_blockers(db, values)
    matches = await conversion._company_duplicates(db, values)
    exact = conversion._exact_blocker(matches)
    if exact:
        blockers.append(exact)
    return {
        **common, "already_converted": False, "customer": None, "blockers": blockers,
        "duplicates": conversion._duplicates_out(matches),
        "requires_confirmation": not blockers and any(m.severity == duplicates_service.POSSIBLE for m in matches),
        "can_setup": not blockers,
        "plans": [
            {"id": str(p.id), "name": p.name, "price": float(p.price or 0), "duration_months": p.duration_months, "max_employees": p.max_employees}
            for p in plans
        ],
    }


# ---- validation of the optional parts (before anything is written) ------------------------------------------------

def _problem(index: int, field: str, code: str, message: str) -> dict:
    return {"index": index, "field": field, "code": code, "message": message}


async def _employee_problems(db: AsyncSession, body) -> List[dict]:
    """Every reason an employee of this request could not get an account - all of them at once, so the form can mark each
    input. The owner's email / phone count as taken: every account needs its own."""
    problems: List[dict] = []
    seen_emails: Dict[str, int] = {norm_email(str(body.owner_email)) or "": -1}
    seen_phones: Dict[str, int] = {norm_phone(body.owner_phone) or body.owner_phone: -1}
    for i, employee in enumerate(body.employees):
        email = str(employee.email)
        key = norm_email(email) or email.lower()
        if key in seen_emails:
            problems.append(_problem(i, "email", "employee_email_repeated", _EMPLOYEE_MESSAGES["employee_email_repeated"]))
        elif await customers_repo.email_registered(db, email):
            problems.append(_problem(i, "email", "employee_email_registered", _EMPLOYEE_MESSAGES["employee_email_registered"]))
        seen_emails.setdefault(key, i)

        invalid = phone_error(employee.phone)                       # the shared Phase-1 phone rule
        if invalid is not None:
            problems.append(_problem(i, "phone", "employee_phone_invalid", invalid))
        else:
            phone_key = norm_phone(employee.phone) or employee.phone
            if phone_key in seen_phones:
                problems.append(_problem(i, "phone", "employee_phone_repeated", _EMPLOYEE_MESSAGES["employee_phone_repeated"]))
            elif await customers_repo.phone_registered(db, employee.phone):
                problems.append(_problem(i, "phone", "employee_phone_registered", _EMPLOYEE_MESSAGES["employee_phone_registered"]))
            seen_phones.setdefault(phone_key, i)

        try:
            validate_password_strength(employee.password.get_secret_value())     # the core rule
        except HTTPException as exc:
            problems.append(_problem(i, "password", "employee_password_weak", str(exc.detail)))
    return problems


def _task_problems(body) -> List[dict]:
    problems: List[dict] = []
    today = app_today()
    for i, task in enumerate(body.tasks):
        if task.assignee >= len(body.employees):
            problems.append(_problem(i, "assignee", "task_assignee_invalid", "Give the task to one of the employees added in this setup"))
        if task.due_date < today:
            problems.append(_problem(i, "due_date", "task_due_date_past", "The due date cannot be in the past"))
    return problems


# ---- the setup ---------------------------------------------------------------------------------------------------

async def _mark_won(db: AsyncSession, ctx: StaffContext, audit: AuditContext, lead: SalesLead) -> Optional[str]:
    """The salesperson's "Agreed": an open lead becomes won - the ONLY way a lead is won (there is no manual win) - with the two
    timeline events a lead's win has always had (plus the cause). Returns the stage it left, or None when it was already won."""
    if lead.pipeline_stage == STAGE_WON:
        return None
    old_stage = lead.pipeline_stage
    lead.pipeline_stage = STAGE_WON
    lead.lost_reason = None
    lead.closed_at = func.clock_timestamp()
    await db.flush()
    cause = {"cause": "customer_setup"}
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_STAGE_CHANGED,
        before={"pipeline_stage": old_stage, "lost_reason": None}, after={"pipeline_stage": STAGE_WON, "lost_reason": None},
        metadata=cause,
    )
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_MARKED_WON,
        before={"pipeline_stage": old_stage}, after={"pipeline_stage": STAGE_WON}, metadata=cause,
    )
    # the owner's decision on the lead: accepted (directly, or from their wait list) - services/batches.py
    await batches_service.record_decision(db, ctx, lead, batches_service.OUTCOME_ACCEPTED)
    return old_stage


def _core_employee_error(index: int, exc: HTTPException) -> HTTPException:
    """A core refusal for one employee (a race the pre-checks lost, the plan limit) as the setup's problem shape."""
    detail = exc.detail
    message = detail.get("message") if isinstance(detail, dict) else str(detail)
    if "Email already registered" in message:
        code, field = "employee_email_registered", "email"
    elif "Phone number already registered" in message:
        code, field = "employee_phone_registered", "phone"
    elif "Employee limit" in message:
        code, field = "employees_over_plan_limit", "employees"
    else:
        code, field = "employee_invalid", "employees"
    return field_error(
        "employees", message, exc.status_code, code="employees_invalid", problems=[_problem(index, field, code, message)]
    )


async def complete_setup(db: AsyncSession, ctx: StaffContext, lead_id: str, body, audit: AuditContext) -> dict:
    lead = await conversion._load_lead(db, ctx, lead_id, for_update=True)      # serialises every setup of this lead

    existing = await customers_repo.get_row_by_lead(db, lead.id)
    if existing is not None:                                                  # idempotent: the first setup stands
        return {"customer": customers_service.customer_out(existing, ctx), "already_converted": True}

    problem = conversion.setup_lead_blocker(lead)
    if problem:
        raise field_error("lead", conversion.SETUP_LEAD_BLOCKERS[problem], 409, code=problem)

    # -- every check before any write --
    values = {
        "business_name": body.business_name, "owner_name": body.owner_name, "owner_email": str(body.owner_email),
        "owner_phone": body.owner_phone, "address": body.address,
    }
    blockers = await conversion._account_blockers(db, values)
    if blockers:
        first = blockers[0]
        raise field_error(first["field"], first["message"], 400, code=first["code"], blockers=blockers)

    owner_password = body.owner_password.get_secret_value()
    try:
        validate_password_strength(owner_password)
    except HTTPException as exc:
        raise field_error("owner_password", str(exc.detail), 400, code="owner_password_weak")

    plan = await plans_repo.get_by_id(db, body.subscription_plan_id)
    if plan is None or not plan.is_active:
        raise field_error("subscription_plan_id", "Selected subscription plan is not available", 400, code="plan_not_available")
    if plan.max_employees and len(body.employees) > plan.max_employees:
        raise field_error(
            "employees", f"The selected plan allows at most {plan.max_employees} employees", 400,
            code="employees_over_plan_limit", max_employees=plan.max_employees,
        )
    if body.tasks and not body.employees:
        raise field_error("tasks", "Add at least one employee to give tasks to, or let the customer create tasks later", 400,
                          code="tasks_need_employees")
    employee_problems = await _employee_problems(db, body)
    if employee_problems:
        raise field_error("employees", "Some employees cannot be added. Correct them, or add them later.", 400,
                          code="employees_invalid", problems=employee_problems)
    task_problems = _task_problems(body)
    if task_problems:
        raise field_error("tasks", "Some tasks cannot be created. Correct them, or create them later.", 400,
                          code="tasks_invalid", problems=task_problems)

    matches = await conversion._company_duplicates(db, values)
    exact = conversion._exact_blocker(matches)
    if exact:
        raise field_error(exact["field"], exact["message"], 409, code=exact["code"], duplicates=conversion._duplicates_out(matches))
    override = None
    if matches:
        if not body.confirm_duplicates:
            raise field_error(
                "duplicates", "Possible duplicates found. Review them, then confirm to continue.", 409,
                code="duplicates_found", duplicates=conversion._duplicates_out(matches),
            )
        override = {"possible": len(matches), "company_ids": [str(m.company.company_id) for m in matches]}

    # -- the lead is won --
    won_from = await _mark_won(db, ctx, audit, lead)

    # -- the company and its owner, by the platform's own service (a lost race is a clean error, never a 500) --
    company_input = SimpleNamespace(
        name=values["business_name"], owner_email=values["owner_email"], owner_name=values["owner_name"],
        owner_password=owner_password, owner_phone=values["owner_phone"], address=values["address"],
        subscription_plan_id=str(plan.id),
    )
    try:
        async with db.begin_nested():
            company = await admin_service.create_company(db, company_input)
    except HTTPException as exc:
        raise conversion._from_core_error(exc)
    except IntegrityError as exc:
        raise conversion._translate_integrity_error(exc)
    company_id = uuid.UUID(company["id"])
    owner_id = uuid.UUID(company["owner_id"])

    # -- its subscription period, by the platform's own Activate (a trial's end is an exact instant) --
    start, end, exact = subscription_period(body.subscription_type, plan)
    await admin_service.activate_subscription(
        db, company["id"], SimpleNamespace(subscription_start_date=start.isoformat(), subscription_end_date=end.isoformat()),
        exact=exact,
    )

    # -- the customer: live from the start, no onboarding record --
    customer = SalesCustomer(
        id=uuid.uuid4(), lead_id=lead.id, company_id=company_id, converted_by=ctx.user_id,
        status=SETUP_CUSTOMER_STATUS, subscription_type=body.subscription_type,
    )
    try:
        async with db.begin_nested():
            db.add(customer)
            await db.flush()
    except IntegrityError as exc:
        raise conversion._translate_integrity_error(exc)

    # -- optional: the first employees, by the platform's own service (plan limit included) --
    employee_ids: List[uuid.UUID] = []
    for i, employee in enumerate(body.employees):
        data = SimpleNamespace(
            email=str(employee.email), phone=employee.phone, password=employee.password.get_secret_value(),
            name=employee.name, department=employee.department, position=employee.position, cv=None,
        )
        try:
            async with db.begin_nested():
                created = await employees_service.create_employee(db, company_id, owner_id, data)
        except HTTPException as exc:
            raise _core_employee_error(i, exc)
        except IntegrityError as exc:
            raise _core_employee_error(i, HTTPException(status_code=400, detail=str(getattr(exc, "orig", exc))))
        employee_ids.append(uuid.UUID(created["id"]))

    # -- optional: the first tasks, by the platform's own service - created on the company owner's behalf (the tasks belong
    #    to the company; who in Sales set them up is on the lead's timeline and in the audit trail) --
    for task in body.tasks:
        data = SimpleNamespace(
            assigned_to=employee_ids[task.assignee], title=task.title, description=task.description or "",
            priority=task.priority, due_date=task.due_date.isoformat(), due_time=None, requires_proof=False,
            attachments=None, scheduled_date=None, scheduled_time=None, idempotency_key=None,
        )
        await tasks_service.create_task(db, company_id, owner_id, data)

    # -- the trail --
    subscription = {
        "type": body.subscription_type, "plan_id": str(plan.id), "plan_name": plan.name,
        "start_date": start.date(), "end_date": end.date(), "starts_at": start, "ends_at": end, "ends_exactly": exact,
    }
    await activity_service.record(
        db, ctx, audit, lead.id, activity_service.EVENT_LEAD_CONVERTED,
        after={
            "customer_status": customer.status, "company_name": company["name"], "plan": plan.name,
            "subscription_type": body.subscription_type, "subscription_end_date": end, "subscription_ends_exactly": exact,
            "employees_added": len(employee_ids), "tasks_created": len(body.tasks),
        },
        metadata={
            "flow": "customer_setup", "customer_id": str(customer.id), "company_id": company["id"],
            **({"won_from": won_from} if won_from else {}),
            **({"duplicate_override": override} if override else {}),
        },
    )
    await audit_service.record(
        db, ctx, audit,
        action=audit_service.ACTION_CUSTOMER_SETUP_COMPLETED, target_type=audit_service.TARGET_SALES_CUSTOMER,
        target_id=customer.id, target_user_id=owner_id,
        after={
            "lead_id": str(lead.id), "company_id": company["id"], "subscription_plan_id": str(plan.id),
            "subscription_type": body.subscription_type, "subscription_start_date": start.isoformat(),
            "subscription_end_date": end.isoformat(), "subscription_ends_exactly": exact,
        },
        metadata={
            "employee_user_ids": [str(i) for i in employee_ids], "tasks_created": len(body.tasks),
            **({"duplicate_override": override} if override else {}),
        },
    )
    logger.info(
        "customer_setup_completed actor=%s lead=%s customer=%s company=%s type=%s employees=%d tasks=%d override=%s",
        ctx.user_id, lead.id, customer.id, company["id"], body.subscription_type, len(employee_ids), len(body.tasks),
        override is not None,
    )
    return {
        "customer": await customers_service.one(db, ctx, customer.id), "already_converted": False,
        "subscription": subscription, "employees_added": len(employee_ids), "tasks_created": len(body.tasks),
    }
