import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.work_locations as work_locations_repo
from services.admin import parse_uuid
from services.qr import generate_qr_code

# ---------------------------------------------------------------------------
# Work Locations (Owner CRUD) - named check-in locations (branch, office,
# warehouse, sales zone, ...), distinct from the legacy single
# Company.attendance_* location. Role checks happen in the route handlers
# (server.py), matching services/work_schedules.py's own convention - this
# layer trusts it was already checked. Every location is always provisioned
# a QR code on creation; whether a group actually *requires* scanning it is
# a separate, independent decision (EmployeeGroup.require_qr).
# ---------------------------------------------------------------------------


def _validate_location_fields(data) -> None:
    if not data.name or not data.name.strip():
        raise HTTPException(status_code=400, detail="Location name is required")
    if data.latitude is None or data.longitude is None:
        raise HTTPException(status_code=400, detail="latitude and longitude are required")
    if not (-90 <= data.latitude <= 90) or not (-180 <= data.longitude <= 180):
        raise HTTPException(status_code=400, detail="latitude/longitude are out of range")
    if data.radius_meters is not None and data.radius_meters <= 0:
        raise HTTPException(status_code=400, detail="radius_meters must be greater than zero")


def location_response(location) -> dict:
    return {
        "id": str(location.id),
        "name": location.name,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "radius_meters": location.radius_meters,
        "qr_code": location.qr_code,
        "qr_token": location.qr_token,
        "qr_generated_at": location.qr_generated_at.isoformat() if location.qr_generated_at else None,
        "is_active": location.is_active,
    }


async def list_locations(db: AsyncSession, current_user: dict) -> list:
    company_id = parse_uuid(current_user["company_id"])
    locations = await work_locations_repo.list_by_company(db, company_id)
    return [location_response(loc) for loc in locations]


async def _provision_qr(location) -> None:
    token = uuid.uuid4().hex
    location.qr_token = token
    location.qr_code = generate_qr_code(token)
    location.qr_generated_at = datetime.now(timezone.utc)


async def create_location(db: AsyncSession, current_user: dict, data) -> dict:
    _validate_location_fields(data)
    company_id = parse_uuid(current_user["company_id"])

    location = await work_locations_repo.create(
        db, company_id=company_id,
        name=data.name.strip(), latitude=data.latitude, longitude=data.longitude,
        radius_meters=data.radius_meters or 50.0, is_active=True,
    )
    await _provision_qr(location)
    await db.flush()
    return location_response(location)


async def _get_owned_location_or_404(db: AsyncSession, current_user: dict, location_id: str):
    company_id = parse_uuid(current_user["company_id"])
    location = await work_locations_repo.get_by_id_and_company(db, parse_uuid(location_id), company_id)
    if not location:
        raise HTTPException(status_code=404, detail="Work location not found")
    return location


async def update_location(db: AsyncSession, current_user: dict, location_id: str, data) -> dict:
    _validate_location_fields(data)
    location = await _get_owned_location_or_404(db, current_user, location_id)

    location.name = data.name.strip()
    location.latitude = data.latitude
    location.longitude = data.longitude
    location.radius_meters = data.radius_meters or 50.0
    await db.flush()
    return location_response(location)


async def deactivate_location(db: AsyncSession, current_user: dict, location_id: str) -> dict:
    location = await _get_owned_location_or_404(db, current_user, location_id)
    in_use = await work_locations_repo.count_groups_using_location(db, location.id)
    if in_use > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate a location that is still linked to an active attendance group - "
                   "update those groups first.",
        )
    location.is_active = False
    await db.flush()
    return {"message": "Work location deactivated"}


async def reactivate_location(db: AsyncSession, current_user: dict, location_id: str) -> dict:
    location = await _get_owned_location_or_404(db, current_user, location_id)
    location.is_active = True
    await db.flush()
    return {"message": "Work location reactivated"}


async def regenerate_location_qr(db: AsyncSession, current_user: dict, location_id: str) -> dict:
    location = await _get_owned_location_or_404(db, current_user, location_id)
    await _provision_qr(location)
    await db.flush()
    return {
        "message": "QR code regenerated",
        "qr_code": location.qr_code,
        "qr_generated_at": location.qr_generated_at.isoformat(),
    }
