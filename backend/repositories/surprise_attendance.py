import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SurpriseAttendanceRequest, SurpriseAttendanceResponse


async def create_request(db: AsyncSession, *, company_id, **fields) -> SurpriseAttendanceRequest:
    request = SurpriseAttendanceRequest(id=uuid.uuid4(), company_id=company_id, **fields)
    db.add(request)
    await db.flush()
    return request


async def get_request_and_company(db: AsyncSession, request_id, company_id) -> Optional[SurpriseAttendanceRequest]:
    """Ownership-scoped lookup, same convention as every other Owner
    resource in this codebase - never a bare get-by-id."""
    result = await db.execute(
        select(SurpriseAttendanceRequest).where(
            SurpriseAttendanceRequest.id == request_id, SurpriseAttendanceRequest.company_id == company_id,
        )
    )
    return result.scalar_one_or_none()


async def list_requests_by_company(db: AsyncSession, company_id) -> List[SurpriseAttendanceRequest]:
    result = await db.execute(
        select(SurpriseAttendanceRequest)
        .where(SurpriseAttendanceRequest.company_id == company_id)
        .order_by(SurpriseAttendanceRequest.created_at.desc())
    )
    return list(result.scalars().all())


async def create_response_placeholder(db: AsyncSession, *, request_id, employee_id) -> SurpriseAttendanceResponse:
    """One pending row per resolved recipient, created at request-creation
    time (same "resolve recipients once, track per-recipient outcome"
    shape as CompanyNotification) - lets the Owner see requested-vs-
    responded without re-deriving the target list later."""
    response = SurpriseAttendanceResponse(
        id=uuid.uuid4(), request_id=request_id, employee_id=employee_id, status="pending",
    )
    db.add(response)
    return response


async def get_pending_response_for_employee(db: AsyncSession, request_id, employee_id) -> Optional[SurpriseAttendanceResponse]:
    result = await db.execute(
        select(SurpriseAttendanceResponse).where(
            SurpriseAttendanceResponse.request_id == request_id,
            SurpriseAttendanceResponse.employee_id == employee_id,
        )
    )
    return result.scalar_one_or_none()


async def list_responses_for_request(db: AsyncSession, request_id) -> List[SurpriseAttendanceResponse]:
    result = await db.execute(
        select(SurpriseAttendanceResponse)
        .where(SurpriseAttendanceResponse.request_id == request_id)
        .order_by(SurpriseAttendanceResponse.created_at)
    )
    return list(result.scalars().all())


async def list_pending_requests_for_employee(db: AsyncSession, employee_id) -> List[SurpriseAttendanceRequest]:
    """Every request this employee has a still-pending response to -
    what the employee-side UI polls to know if they've been asked."""
    result = await db.execute(
        select(SurpriseAttendanceRequest)
        .join(SurpriseAttendanceResponse, SurpriseAttendanceResponse.request_id == SurpriseAttendanceRequest.id)
        .where(
            SurpriseAttendanceResponse.employee_id == employee_id,
            SurpriseAttendanceResponse.status == "pending",
            SurpriseAttendanceRequest.status == "pending",
        )
        .order_by(SurpriseAttendanceRequest.created_at.desc())
    )
    return list(result.scalars().all())


async def count_pending_responses(db: AsyncSession, request_id) -> int:
    responses = await list_responses_for_request(db, request_id)
    return sum(1 for r in responses if r.status == "pending")
