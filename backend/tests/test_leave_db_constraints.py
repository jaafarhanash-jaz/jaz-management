"""Direct (non-HTTP) database-level tests for Leave.

These import the backend's own async service/repository functions and run
them with asyncio.run() against the same local Postgres database used by
the live HTTP integration suite (test_leave.py) - mirroring
test_company_notifications_push_delivery.py's own established pattern for
this exact test tier, including its run()/engine.dispose() helper (a
second asyncio.run() reusing database.engine's pool from a torn-down loop
raises "another operation is in progress" otherwise).

This tier is for things the HTTP layer can't directly observe: the
leaves table's own EXCLUDE constraint (bypassing the application-level
overlap check entirely, to prove the DB-level backstop itself works),
audit_log row creation, and services/leave_attendance.py's calculation
functions exercised directly with hand-built Attendance/Leave state.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from database import SessionLocal, engine
from models import AuditLog, Attendance, Leave, Notification, User
import repositories.attendance as attendance_repo
import repositories.leaves as leaves_repo
import services.leave_attendance as leave_attendance_service
import services.leaves as leaves_service


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _get_user(db, email):
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one()


async def _cleanup_leaves(db, employee_id, dates):
    await db.execute(delete(Leave).where(Leave.employee_id == employee_id, Leave.start_date.in_(dates)))


async def _cleanup_attendance(db, employee_id, dates):
    await db.execute(delete(Attendance).where(Attendance.employee_id == employee_id, Attendance.date.in_(dates)))


class TestExcludeConstraint:
    """Bypasses services/leaves.py's application-level overlap check
    entirely (inserting straight through the repository) to prove the
    leaves table's own EXCLUDE constraint is the real, unconditional
    backstop - not just the friendly service-layer error path."""

    def test_overlapping_approved_leaves_rejected_at_db_level(self):
        test_date = date(2027, 3, 1)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                # Captured before any rollback - db.rollback() expires every
                # attribute on every object in the session (same rule that
                # required db.refresh() in services/leaves.py's mutating
                # functions), and a plain sync re-access afterward raises
                # MissingGreenlet even though we're inside an async def.
                employee_id = employee.id
                try:
                    first = await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 1, 10, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 1, 13, 0, tzinfo=timezone.utc),
                        reason="db constraint test", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()

                    with pytest.raises(IntegrityError):
                        await leaves_repo.create(
                            db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                            start_date=test_date, end_date=test_date,
                            start_at=datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc),
                            end_at=datetime(2027, 3, 1, 15, 0, tzinfo=timezone.utc),
                            reason="should be rejected", status="approved", origin="owner",
                            requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                        )
                        await db.flush()
                finally:
                    await db.rollback()
                    await _cleanup_leaves(db, employee_id, [test_date])
                    await db.commit()

        run(_run())

    def test_adjacent_touching_boundary_leaves_both_accepted(self):
        test_date = date(2027, 3, 2)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                employee_id = employee.id
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 2, 8, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 2, 10, 0, tzinfo=timezone.utc),
                        reason="first", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()
                    # Touching boundary (10:00 -> 10:00) - half-open interval
                    # semantics mean this must NOT be treated as overlapping.
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 2, 10, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 2, 12, 0, tzinfo=timezone.utc),
                        reason="second, adjacent", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()
                    count = await db.execute(
                        select(Leave).where(Leave.employee_id == employee_id, Leave.start_date == test_date)
                    )
                    assert len(count.scalars().all()) == 2
                finally:
                    await db.rollback()
                    await _cleanup_leaves(db, employee_id, [test_date])
                    await db.commit()

        run(_run())

    def test_pending_leaves_may_overlap_each_other(self):
        """The EXCLUDE constraint's WHERE clause only covers status='approved'
        - two PENDING leaves for the same employee/interval must be able to
        coexist (only one can ultimately be approved)."""
        test_date = date(2027, 3, 3)

        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    for reason in ("pending A", "pending B"):
                        await leaves_repo.create(
                            db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                            start_date=test_date, end_date=test_date,
                            start_at=datetime(2027, 3, 3, 9, 0, tzinfo=timezone.utc),
                            end_at=datetime(2027, 3, 3, 11, 0, tzinfo=timezone.utc),
                            reason=reason, status="pending", origin="employee", requested_by=employee.id,
                        )
                        await db.flush()
                    rows = await db.execute(
                        select(Leave).where(Leave.employee_id == employee.id, Leave.start_date == test_date)
                    )
                    assert len(rows.scalars().all()) == 2
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())


class TestApprovalTimeRevalidation:
    def test_approving_second_overlapping_pending_request_is_rejected(self):
        """Two pending requests for the same overlapping window are both
        allowed to exist (see above); approving the FIRST must succeed,
        and approving the SECOND must then fail with the friendly
        service-layer 409, not just the raw DB constraint error - proving
        the transactional revalidation (advisory lock + row lock +
        re-check) actually runs at approval time, not only at creation."""
        test_date = date(2027, 3, 4)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                current_user = {"id": str(owner.id), "company_id": str(owner.company_id), "name": owner.name}
                try:
                    first = await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 4, 9, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 4, 12, 0, tzinfo=timezone.utc),
                        reason="first pending", status="pending", origin="employee", requested_by=employee.id,
                    )
                    second = await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 4, 11, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 4, 14, 0, tzinfo=timezone.utc),
                        reason="second pending, overlaps first", status="pending", origin="employee",
                        requested_by=employee.id,
                    )
                    await db.flush()

                    approved_first = await leaves_service.approve(db, current_user, str(first.id))
                    assert approved_first["status"] == "approved"

                    from fastapi import HTTPException
                    with pytest.raises(HTTPException) as exc:
                        await leaves_service.approve(db, current_user, str(second.id))
                    assert exc.value.status_code == 409
                finally:
                    # No db.rollback() here (unlike the EXCLUDE-constraint
                    # tests above) - approve()'s 409 is a plain application-
                    # level HTTPException, not a failed DB statement, so the
                    # transaction was never aborted and employee/leave
                    # attributes are still fresh.
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())


class TestNonWorkingEmployeeDays:
    def test_approved_full_day_leave_counted_as_non_working(self):
        test_date = date(2027, 3, 10)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 10, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 11, 0, 0, tzinfo=timezone.utc),
                        reason="full day off", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()

                    pairs = await leave_attendance_service.get_full_day_leave_employee_date_pairs(
                        db, employee.company_id, [employee.id], test_date, test_date,
                    )
                    assert (str(employee.id), test_date.isoformat()) in pairs

                    count = await leave_attendance_service.compute_non_working_employee_days(
                        db, employee.company_id, [employee.id], test_date, test_date,
                    )
                    # At least 1 (the approved leave) - could be 0 additional
                    # if test_date also happens to be a configured weekly
                    # holiday, in which case it's already counted via the
                    # holiday branch and not double-counted (this asserts
                    # the leave contributes, not an exact literal count that
                    # would be fragile against seeded company config).
                    assert count >= 1
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())

    def test_pending_full_day_leave_not_counted(self):
        test_date = date(2027, 3, 11)

        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 11, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 12, 0, 0, tzinfo=timezone.utc),
                        reason="pending full day", status="pending", origin="employee", requested_by=employee.id,
                    )
                    await db.flush()

                    pairs = await leave_attendance_service.get_full_day_leave_employee_date_pairs(
                        db, employee.company_id, [employee.id], test_date, test_date,
                    )
                    assert (str(employee.id), test_date.isoformat()) not in pairs
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())

    def test_rejected_full_day_leave_not_counted(self):
        test_date = date(2027, 3, 12)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 12, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 13, 0, 0, tzinfo=timezone.utc),
                        reason="rejected full day", status="rejected", origin="employee", requested_by=employee.id,
                        rejected_by=owner.id, rejected_at=datetime.now(timezone.utc),
                    )
                    await db.flush()

                    pairs = await leave_attendance_service.get_full_day_leave_employee_date_pairs(
                        db, employee.company_id, [employee.id], test_date, test_date,
                    )
                    assert (str(employee.id), test_date.isoformat()) not in pairs
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())


class TestAnnotateLeaveDatesPartialShift:
    def test_partial_shift_leave_reduces_effective_missing_minutes(self):
        """Scenario A from the approved design: 08:00-16:00 schedule,
        10:00-13:00 approved leave -> only 10:00-13:00 is exempt; the
        stored Attendance row's OWN missing_minutes is untouched (never
        mutated), but the read-time annotation reflects the leave-reduced
        requirement."""
        test_date = date(2027, 3, 20)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="time_based",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 20, 10, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 20, 13, 0, tzinfo=timezone.utc),
                        reason="partial shift leave", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()

                    # A record that checked in on time and checked out
                    # right at 13:00 (i.e. left 3 hours early relative to
                    # the 16:00 scheduled end) - WITHOUT the leave, this
                    # would show a real missing-minutes shortfall; WITH the
                    # approved leave covering exactly that gap, the
                    # effective shortfall should be ~0.
                    record_dict = {
                        "employee_id": str(employee.id),
                        "date": test_date.isoformat(),
                        "check_in_time": datetime(2027, 3, 20, 8, 0, tzinfo=timezone.utc).isoformat(),
                        "check_out_time": datetime(2027, 3, 20, 13, 0, tzinfo=timezone.utc).isoformat(),
                        "scheduled_start_time": "08:00",
                        "scheduled_end_time": "16:00",
                        "required_minutes": 480.0,
                        "net_minutes": 300.0,  # 08:00 -> 13:00 = 5h worked
                        "missing_minutes": 180.0,  # what the naive/stored figure would say
                    }
                    records = [record_dict]
                    await leave_attendance_service.annotate_leave_dates(db, records)

                    annotated = records[0]
                    assert annotated["leave_covered_minutes"] == 180.0
                    assert annotated["effective_required_minutes"] == 300.0  # 480 - 180
                    # net_minutes (300) now meets effective_required_minutes
                    # (300) exactly - no shortfall.
                    assert annotated.get("effective_missing_minutes") is None
                    assert len(annotated["leave_intervals"]) == 1
                    assert annotated["leave_intervals"][0]["duration_type"] == "time_based"
                    # The underlying stored figure must remain untouched -
                    # this is a read-time annotation, never a mutation.
                    assert record_dict["missing_minutes"] == 180.0
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())

    def test_no_leave_means_no_annotation_fields_added(self):
        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee1@demo.com")
                record_dict = {
                    "employee_id": str(employee.id), "date": date(2027, 3, 21).isoformat(),
                    "check_in_time": None, "check_out_time": None,
                    "scheduled_start_time": "08:00", "scheduled_end_time": "16:00", "required_minutes": 480.0,
                }
                records = [record_dict]
                await leave_attendance_service.annotate_leave_dates(db, records)
                assert records[0]["leave_intervals"] == []
                assert records[0]["leave_covered_minutes"] == 0.0
                assert "effective_missing_minutes" not in records[0]

        run(_run())


class TestOvernightShiftFullDayExemption:
    def test_full_day_leave_exempts_overnight_shift_by_owning_date_not_checkout_calendar_day(self):
        """The Attendance row's owning date (D) is the check-in date; its
        checkout timestamp falls on D+1 (22:00 D -> 06:00 D+1). An
        approved full_day leave covering ONLY date D must still fully
        exempt this shift - proving the exemption is a
        start_date<=Attendance.date<=end_date membership test, not a
        midnight-to-midnight instant-overlap test (which would incorrectly
        miss the 06:00 D+1 checkout)."""
        shift_date = date(2027, 3, 25)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=shift_date, end_date=shift_date,
                        start_at=datetime(2027, 3, 25, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 3, 26, 0, 0, tzinfo=timezone.utc),
                        reason="overnight shift day off", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    attendance_row = await attendance_repo.create(
                        db, employee_id=employee.id, company_id=employee.company_id, date=shift_date,
                        check_in_time=datetime(2027, 3, 25, 22, 0, tzinfo=timezone.utc),
                        check_out_time=datetime(2027, 3, 26, 6, 0, tzinfo=timezone.utc),
                        status="present", working_duration_minutes=480.0,
                    )
                    await db.flush()

                    from services.dashboard import _filter_out_exempt_attendance
                    filtered = await _filter_out_exempt_attendance(db, employee.company_id, [attendance_row])
                    assert filtered == [], "an approved full_day leave must exclude the overnight shift owned by that date"
                finally:
                    await _cleanup_leaves(db, employee.id, [shift_date])
                    await _cleanup_attendance(db, employee.id, [shift_date])
                    await db.commit()

        run(_run())


class TestAuditLogging:
    def test_create_for_employee_writes_audit_entry(self):
        test_date = date(2027, 3, 30)

        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee1@demo.com")
                current_user = {"id": str(employee.id), "company_id": str(employee.company_id), "name": employee.name}
                from types import SimpleNamespace
                data = SimpleNamespace(
                    duration_type="full_day", reason="audit test", start_date=test_date.isoformat(),
                    end_date=None, date=None, start_time=None, end_time=None, group_id=None,
                )
                try:
                    result = await leaves_service.create_for_employee(db, current_user, data)
                    await db.flush()

                    logs = await db.execute(
                        select(AuditLog).where(
                            AuditLog.entity_type == "leave", AuditLog.entity_id == uuid.UUID(result["id"]),
                        )
                    )
                    entries = logs.scalars().all()
                    assert len(entries) == 1
                    assert entries[0].action == "requested"
                    assert entries[0].user_id == employee.id
                    assert entries[0].reason == "audit test"
                    assert entries[0].new_values == {"status": "pending"}
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())

    def test_approve_writes_audit_entry_with_old_and_new_status(self):
        test_date = date(2027, 3, 31)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                owner_user = {"id": str(owner.id), "company_id": str(owner.company_id), "name": owner.name}
                try:
                    leave = await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 3, 31, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 4, 1, 0, 0, tzinfo=timezone.utc),
                        reason="to be approved", status="pending", origin="employee", requested_by=employee.id,
                    )
                    await db.flush()

                    await leaves_service.approve(db, owner_user, str(leave.id))
                    await db.flush()

                    logs = await db.execute(
                        select(AuditLog).where(AuditLog.entity_type == "leave", AuditLog.entity_id == leave.id, AuditLog.action == "approved")
                    )
                    entries = logs.scalars().all()
                    assert len(entries) == 1
                    assert entries[0].old_values == {"status": "pending"}
                    assert entries[0].new_values == {"status": "approved"}
                    assert entries[0].user_id == owner.id
                finally:
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())


class TestNotifications:
    def test_owner_notified_on_employee_leave_request(self):
        test_date = date(2027, 4, 5)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                current_user = {"id": str(employee.id), "company_id": str(employee.company_id), "name": employee.name}
                from types import SimpleNamespace
                data = SimpleNamespace(
                    duration_type="full_day", reason="notif test", start_date=test_date.isoformat(),
                    end_date=None, date=None, start_time=None, end_time=None, group_id=None,
                )
                leave_id = None
                try:
                    result = await leaves_service.create_for_employee(db, current_user, data)
                    leave_id = uuid.UUID(result["id"])
                    await db.flush()

                    notifs = await db.execute(
                        select(Notification).where(
                            Notification.user_id == owner.id, Notification.category == "leave",
                            Notification.type == "leave_requested", Notification.entity_id == leave_id,
                        )
                    )
                    entries = notifs.scalars().all()
                    assert len(entries) == 1
                finally:
                    if leave_id:
                        await db.execute(delete(Notification).where(Notification.entity_id == leave_id))
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())

    def test_employee_notified_on_approval(self):
        test_date = date(2027, 4, 6)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                owner_user = {"id": str(owner.id), "company_id": str(owner.company_id), "name": owner.name}
                leave_id = None
                try:
                    leave = await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=test_date, end_date=test_date,
                        start_at=datetime(2027, 4, 6, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 4, 7, 0, 0, tzinfo=timezone.utc),
                        reason="to be approved for notif test", status="pending", origin="employee",
                        requested_by=employee.id,
                    )
                    leave_id = leave.id
                    await db.flush()

                    await leaves_service.approve(db, owner_user, str(leave.id))
                    await db.flush()

                    notifs = await db.execute(
                        select(Notification).where(
                            Notification.user_id == employee.id, Notification.category == "leave",
                            Notification.type == "leave_approved", Notification.entity_id == leave.id,
                        )
                    )
                    entries = notifs.scalars().all()
                    assert len(entries) == 1
                finally:
                    if leave_id:
                        await db.execute(delete(Notification).where(Notification.entity_id == leave_id))
                    await _cleanup_leaves(db, employee.id, [test_date])
                    await db.commit()

        run(_run())


class TestRemainingOfDayResolution:
    def test_resolves_against_open_attendance_session_not_client_input(self):
        """START = now(); END = the OPEN SESSION's own resolved shift end -
        never a client-supplied value, never a naive 23:59:59 default. A
        far-future check_in_time guarantees this is the row
        get_latest_open_for_employee picks (ORDER BY check_in_time DESC),
        regardless of any other open session real usage may have created
        for this account."""
        shift_date = date(2027, 6, 1)

        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee2@demo.com")
                employee_id = employee.id
                try:
                    await attendance_repo.create(
                        db, employee_id=employee.id, company_id=employee.company_id, date=shift_date,
                        check_in_time=datetime(2027, 6, 1, 9, 0, tzinfo=timezone.utc),
                        check_out_time=None, status="present",
                        scheduled_start_time="08:00", scheduled_end_time="17:00", required_minutes=480.0,
                    )
                    await db.flush()

                    current_user = {"id": str(employee.id), "company_id": str(employee.company_id), "name": employee.name}
                    from types import SimpleNamespace
                    data = SimpleNamespace(
                        duration_type="remaining_of_day", reason="not feeling well", date=None,
                        start_time=None, end_time=None, start_date=None, end_date=None, group_id=None,
                    )
                    result = await leaves_service.create_for_employee(db, current_user, data)

                    assert result["duration_type"] == "remaining_of_day"
                    assert result["start_date"] == shift_date.isoformat()
                    # end_at must equal the OPEN SESSION's own resolved shift
                    # end (08:00-17:00 shift dated 2027-06-01 -> 17:00 UTC),
                    # never a client value (none was even sent) and never a
                    # naive 23:59:59 default.
                    assert result["end_at"] == "2027-06-01T17:00:00+00:00"
                finally:
                    await _cleanup_leaves(db, employee_id, [shift_date])
                    await _cleanup_attendance(db, employee_id, [shift_date])
                    await db.commit()

        run(_run())


class TestHolidayAndLeaveInteraction:
    def test_approved_leave_on_a_weekly_holiday_date_not_double_counted(self):
        """2027-08-06 is a Friday - a weekly holiday for the demo company
        (working_days=[0,1,2,3,4], i.e. Sun-Thu; confirmed directly against
        the live company row before writing this test). An approved
        full_day leave for the SAME employee on that SAME date must
        contribute at most one non-working day for that employee, not two
        (one for the holiday, one for the leave) - compute_non_working_
        employee_days' de-dup logic (services/leave_attendance.py) is what
        this asserts."""
        holiday_date = date(2027, 8, 6)

        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    # Sanity-check the premise before asserting on it - fail
                    # loudly if the company's working_days config ever
                    # changes, rather than silently asserting something
                    # that's no longer true.
                    import repositories.companies as companies_repo
                    import services.holidays as holidays_service
                    working_hours = await companies_repo.get_working_hours(db, employee.company_id)
                    day_off = await holidays_service.get_day_off_info(db, employee.company_id, holiday_date, working_hours=working_hours)
                    assert day_off is not None, "test premise broken: 2027-08-06 is no longer a configured holiday for this company"

                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=holiday_date, end_date=holiday_date,
                        start_at=datetime(2027, 8, 6, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 8, 7, 0, 0, tzinfo=timezone.utc),
                        reason="leave landing on a weekly holiday", status="approved", origin="owner",
                        requested_by=owner.id, approved_by=owner.id, approved_at=datetime.now(timezone.utc),
                    )
                    await db.flush()

                    count = await leave_attendance_service.compute_non_working_employee_days(
                        db, employee.company_id, [employee.id], holiday_date, holiday_date,
                    )
                    assert count == 1, f"expected exactly 1 (deduped), got {count} - holiday and leave were double-counted"
                finally:
                    await _cleanup_leaves(db, employee.id, [holiday_date])
                    await db.commit()

        run(_run())

    def test_pending_leave_on_a_weekly_holiday_still_counts_only_the_holiday(self):
        """A PENDING (not approved) leave contributes nothing on its own -
        the holiday alone must still count as exactly 1 non-working day,
        proving the dedup path doesn't accidentally suppress the holiday's
        own contribution when a non-approved leave also exists for that
        date."""
        holiday_date = date(2027, 8, 13)  # also a Friday

        async def _run():
            async with SessionLocal() as db:
                employee = await _get_user(db, "employee1@demo.com")
                try:
                    await leaves_repo.create(
                        db, company_id=employee.company_id, employee_id=employee.id, duration_type="full_day",
                        start_date=holiday_date, end_date=holiday_date,
                        start_at=datetime(2027, 8, 13, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2027, 8, 14, 0, 0, tzinfo=timezone.utc),
                        reason="pending, should not affect the count", status="pending", origin="employee",
                        requested_by=employee.id,
                    )
                    await db.flush()

                    count = await leave_attendance_service.compute_non_working_employee_days(
                        db, employee.company_id, [employee.id], holiday_date, holiday_date,
                    )
                    assert count == 1
                finally:
                    await _cleanup_leaves(db, employee.id, [holiday_date])
                    await db.commit()

        run(_run())
