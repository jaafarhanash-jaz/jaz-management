from dataclasses import dataclass, field
from typing import List, Optional

import repositories.employee_groups as employee_groups_repo
import services.schedule_resolution as schedule_resolution
from models import EmployeeGroup, WorkLocation
from services.attendance_calc_engine import ScheduleSnapshot

# ---------------------------------------------------------------------------
# Employee Group resolution. An employee may belong to MULTIPLE
# Attendance Groups at once (e.g. "Sales Representatives" AND "Al-Bayaa
# Branch" simultaneously - the approved spec's own example), so
# resolution here always returns a LIST, never a single group shortcut.
# An employee with zero active groups resolves to an empty list at every
# call site - services/attendance.py treats that as "use the legacy
# single Company.attendance_* location, both QR and GPS always
# required," unchanged from before this feature existed. Disambiguating
# WHICH of several active groups applies to one specific attendance
# action lives in services/attendance.py (it needs QR/location matching,
# which already lives there) - this module only resolves the candidate
# set and converts a group to its policy. This module never touches
# Company directly.
# ---------------------------------------------------------------------------

DEFAULT_GRACE_PERIOD_MINUTES = 180


@dataclass(frozen=True)
class LocationSnapshot:
    id: str
    name: str
    latitude: float
    longitude: float
    radius_meters: float
    qr_token: Optional[str]


@dataclass(frozen=True)
class GroupPolicy:
    group_id: str
    require_zone: bool
    require_qr: bool
    photo_proof_required: bool
    grace_period_minutes: int
    schedule: Optional[ScheduleSnapshot]
    locations: List[LocationSnapshot] = field(default_factory=list)


async def list_active_groups_for_employee(db, *, employee_id, company_id) -> List[EmployeeGroup]:
    """Every group this employee currently belongs to (EmployeeGroupAssignment
    is the sole source of truth - see its own docstring). Zero, one, or
    many. Deliberately NOT a company-default fallback the way WorkSchedule
    has one: an employee with no group memberships uses the legacy
    company-wide path, not some other group."""
    assignments = await employee_groups_repo.list_open_assignments_for_employee(db, employee_id)
    groups = []
    for assignment in assignments:
        group = await employee_groups_repo.get_by_id_and_company(db, assignment.group_id, company_id)
        if group and group.is_active:
            groups.append(group)
    return groups


async def to_policy(db, group: Optional[EmployeeGroup]) -> Optional[GroupPolicy]:
    if group is None:
        return None

    schedule = await schedule_resolution.get_effective_schedule_for_employee(
        db, schedule_id=group.work_schedule_id, company_id=group.company_id,
    )
    locations = await employee_groups_repo.list_locations_for_group(db, group.id)

    return GroupPolicy(
        group_id=str(group.id),
        require_zone=group.require_zone,
        require_qr=group.require_qr,
        photo_proof_required=group.photo_proof_required,
        grace_period_minutes=group.grace_period_minutes,
        schedule=schedule_resolution.to_snapshot(schedule),
        locations=[
            LocationSnapshot(
                id=str(loc.id), name=loc.name, latitude=loc.latitude, longitude=loc.longitude,
                radius_meters=loc.radius_meters, qr_token=loc.qr_token,
            )
            for loc in locations
        ],
    )
