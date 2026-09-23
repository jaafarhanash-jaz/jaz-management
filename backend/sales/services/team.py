"""Internal staff management (Phase 1).

Every function here operates ONLY on users whose platform role is `jaz_staff`
(enforced in staff_repo.get_staff_user) - it can never read or mutate a Company
Owner, Employee or Super Admin account, and there is no code path that lets a
client choose a platform role: users are always created as `jaz_staff` with no
company.

Authorization (who may call these) lives in the router via require_permission;
by design only Super Admin holds the sales.staff.* permissions today.
"""
import logging
import uuid
from typing import List

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.refresh_tokens as refresh_tokens_repo
import repositories.users as users_repo
from sales.permissions import PLATFORM_ROLE_STAFF
from sales.repositories import staff as staff_repo
from sales.services import audit as audit_service
from sales.services.access import StaffContext, _role_ref
from sales.services.audit import AuditContext
from services.admin import parse_uuid
from services.auth import hash_password, validate_password_strength
from services.identifiers import PHONE_HAS_AT_SIGN, phone_error

logger = logging.getLogger("sales.staff")


# ---- shaping ---------------------------------------------------------------

def _staff_out(user, roles) -> dict:
    # Explicit field list - never spread the user row (it carries the password hash).
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "created_at": user.created_at,
        "last_seen_at": user.last_seen_at,
        "roles": [_role_ref(r) for r in roles],
    }


async def _staff_out_fresh(db: AsyncSession, user) -> dict:
    roles = (await staff_repo.active_roles_for_users(db, [user.id])).get(user.id, [])
    return _staff_out(user, roles)


def _field_error(field: str, message: str, status_code: int = 400) -> HTTPException:
    # Same {"field", "message"} detail shape services/admin.py already uses, so
    # the UI can attach the error to the right input.
    return HTTPException(status_code=status_code, detail={"field": field, "message": message})


async def _get_staff_or_404(db: AsyncSession, user_id: str):
    parsed = parse_uuid(user_id)
    user = await staff_repo.get_staff_user(db, parsed) if parsed else None
    if user is None:
        raise HTTPException(status_code=404, detail="Staff user not found")
    return user


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """A concurrent request can win the race the pre-checks lose; the DB's own
    partial unique indexes are the real guarantee. Turn that into the same clean
    400 the pre-check would have produced, never a 500."""
    text = str(getattr(exc, "orig", exc))
    if "uq_users_email_active" in text:
        return _field_error("email", "Email already registered")
    if "uq_users_phone_active" in text:
        return _field_error("phone", "Phone number already registered")
    if "ck_users_phone_no_at" in text:
        return _field_error("phone", PHONE_HAS_AT_SIGN)
    raise exc


def _validated_password(password: str, field: str) -> str:
    try:
        validate_password_strength(password)
    except HTTPException as exc:
        raise _field_error(field, str(exc.detail))
    return password


def _validated_phone(phone) -> None:
    """The shared phone rule (services/identifiers.py), reported in this module's {"field","message"} shape."""
    message = phone_error(phone)
    if message is not None:
        raise _field_error("phone", message)


async def _record_role_event(
    db: AsyncSession, actor: StaffContext, audit: AuditContext, *, granted: bool, user, role, grant_id: uuid.UUID
) -> None:
    """One staff_role_granted / staff_role_revoked event. Only ever called for a REAL change (the grant
    row was inserted / revoked): an idempotent no-op emits nothing, so the trail has no false events."""
    role_state = {"role_key": role.key}
    await audit_service.record(
        db, actor, audit,
        action=audit_service.ACTION_STAFF_ROLE_GRANTED if granted else audit_service.ACTION_STAFF_ROLE_REVOKED,
        target_type=audit_service.TARGET_STAFF_ROLE_GRANT,
        target_id=grant_id,
        target_user_id=user.id,
        before=None if granted else role_state,
        after=role_state if granted else None,
        metadata={"role_id": str(role.id), "grant_id": str(grant_id)},
    )


# ---- reads -----------------------------------------------------------------

async def list_team(db: AsyncSession, limit: int, offset: int) -> dict:
    users, total = await staff_repo.list_staff_users(db, limit, offset)
    roles_by_user = await staff_repo.active_roles_for_users(db, [u.id for u in users])
    return {
        "items": [_staff_out(u, roles_by_user.get(u.id, [])) for u in users],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def list_roles(db: AsyncSession) -> List[dict]:
    return [
        {**_role_ref(role), "description": role.description, "is_system": role.is_system, "permissions": sorted(perms)}
        for role, perms in await staff_repo.list_active_roles_with_permissions(db)
    ]


# ---- writes ----------------------------------------------------------------

async def create_staff(db: AsyncSession, actor: StaffContext, data, audit: AuditContext) -> dict:
    role_keys = list(dict.fromkeys(data.role_keys))  # de-duplicate, keep order
    roles = await staff_repo.get_active_roles_by_keys(db, role_keys)
    unknown = [k for k in role_keys if k not in roles]
    if unknown:
        raise _field_error("role_keys", f"Unknown or inactive role: {', '.join(unknown)}")

    _validated_phone(data.phone)
    if await users_repo.email_taken(db, data.email):
        raise _field_error("email", "Email already registered")
    if await users_repo.phone_taken(db, data.phone):
        raise _field_error("phone", "Phone number already registered")

    _validated_password(data.password, "password")
    hashed = await hash_password(data.password)  # off the event loop (bcrypt is slow)

    try:
        async with db.begin_nested():  # savepoint: a lost race must not poison the outer transaction
            user = await users_repo.create(
                db,
                email=data.email,
                phone=data.phone,
                password=hashed,
                name=data.name,
                role=PLATFORM_ROLE_STAFF,  # fixed - never client-controlled
                company_id=None,
                status="active",
            )
            grants = [
                (role, await staff_repo.grant_role_id(db, user.id, role.id, actor.user_id)) for role in roles.values()
            ]
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    await db.refresh(user)
    # Audit: same transaction as the account and its grants (nothing here is committed yet), one
    # correlation id for the whole request. before is None - the account did not exist.
    await audit_service.record(
        db, actor, audit,
        action=audit_service.ACTION_STAFF_CREATED,
        target_type=audit_service.TARGET_STAFF_USER,
        target_id=user.id,
        target_user_id=user.id,
        after=audit_service.user_fields(user),
        metadata={"initial_role_keys": role_keys},
    )
    for role, grant_id in grants:
        if grant_id is not None:
            await _record_role_event(db, actor, audit, granted=True, user=user, role=role, grant_id=grant_id)
    logger.info("staff_created actor=%s target=%s roles=%s", actor.user_id, user.id, role_keys)
    return await _staff_out_fresh(db, user)


async def update_staff(db: AsyncSession, actor: StaffContext, user_id: str, data, audit: AuditContext) -> dict:
    user = await _get_staff_or_404(db, user_id)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if changes.get("phone") is not None:
        _validated_phone(changes["phone"])
    if "email" in changes and changes["email"] != user.email:
        if await users_repo.email_taken(db, changes["email"], exclude_id=user.id):
            raise _field_error("email", "Email already registered")
    if "phone" in changes and changes["phone"] != user.phone:
        if await users_repo.phone_taken(db, changes["phone"], exclude_id=user.id):
            raise _field_error("phone", "Phone number already registered")

    # What actually changes, for the audit trail (captured BEFORE the row is touched). Profile fields and
    # status are separate events; a value equal to the current one is not a change.
    profile_before = {
        f: getattr(user, f) for f in audit_service.STAFF_PROFILE_FIELDS
        if changes.get(f) is not None and changes[f] != getattr(user, f)
    }
    profile_after = {f: changes[f] for f in profile_before}
    old_status = user.status
    new_status = changes.get("status")
    status_changed = new_status is not None and new_status != old_status
    deactivating = status_changed and new_status == "inactive"
    try:
        async with db.begin_nested():
            for field in ("name", "email", "phone", "status"):
                if field in changes and changes[field] is not None:
                    setattr(user, field, changes[field])
            await db.flush()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc)

    if deactivating:
        # Ends every refresh-token session. Access tokens can't be recalled (core
        # has no token versioning), but resolve_staff_context denies a non-active
        # staff account on every Sales request, so Sales access ends immediately.
        await refresh_tokens_repo.revoke_all_for_user(db, user.id)

    await db.refresh(user)
    # Audit (same transaction): a profile edit and a status change are separate events, and a request
    # that changed nothing (values equal to the current ones) emits none.
    if profile_after:
        await audit_service.record(
            db, actor, audit,
            action=audit_service.ACTION_STAFF_UPDATED,
            target_type=audit_service.TARGET_STAFF_USER,
            target_id=user.id,
            target_user_id=user.id,
            before=profile_before,
            after=profile_after,
            metadata={"fields": sorted(profile_after)},
        )
    if status_changed:
        await audit_service.record(
            db, actor, audit,
            action=audit_service.ACTION_STAFF_DEACTIVATED if deactivating else audit_service.ACTION_STAFF_REACTIVATED,
            target_type=audit_service.TARGET_STAFF_USER,
            target_id=user.id,
            target_user_id=user.id,
            before={"status": old_status},
            after={"status": new_status},
            metadata={"refresh_tokens_revoked": True} if deactivating else None,
        )
    logger.info("staff_updated actor=%s target=%s fields=%s", actor.user_id, user.id, sorted(changes))
    return await _staff_out_fresh(db, user)


async def reset_password(db: AsyncSession, actor: StaffContext, user_id: str, new_password: str, audit: AuditContext) -> dict:
    user = await _get_staff_or_404(db, user_id)
    _validated_password(new_password, "new_password")
    user.password = await hash_password(new_password)
    await db.flush()
    await refresh_tokens_repo.revoke_all_for_user(db, user.id)  # signs the person out everywhere
    # Audit: the fact of the reset, never the password or its hash (no before/after at all).
    await audit_service.record(
        db, actor, audit,
        action=audit_service.ACTION_STAFF_PASSWORD_RESET,
        target_type=audit_service.TARGET_STAFF_USER,
        target_id=user.id,
        target_user_id=user.id,
        metadata={"refresh_tokens_revoked": True},
    )
    logger.info("staff_password_reset actor=%s target=%s", actor.user_id, user.id)
    return {"message": "Password reset. All active sessions were signed out."}


async def assign_role(db: AsyncSession, actor: StaffContext, user_id: str, role_key: str, audit: AuditContext) -> dict:
    user = await _get_staff_or_404(db, user_id)  # only jaz_staff can ever hold a staff role
    role = await staff_repo.get_role_by_key(db, role_key)
    if role is None or not role.is_active:
        raise _field_error("role_key", "Unknown or inactive role")
    grant_id = await staff_repo.grant_role_id(db, user.id, role.id, actor.user_id)
    changed = grant_id is not None
    if changed:  # a no-op grant (already active) changes nothing and is not audited
        await _record_role_event(db, actor, audit, granted=True, user=user, role=role, grant_id=grant_id)
        logger.info("staff_role_granted actor=%s target=%s role=%s", actor.user_id, user.id, role_key)
    return {"changed": changed, "staff": await _staff_out_fresh(db, user)}


async def revoke_role(db: AsyncSession, actor: StaffContext, user_id: str, role_key: str, audit: AuditContext) -> dict:
    user = await _get_staff_or_404(db, user_id)
    role = await staff_repo.get_role_by_key(db, role_key)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    grant_id = await staff_repo.revoke_role_id(db, user.id, role.id, actor.user_id)
    changed = grant_id is not None
    if changed:  # nothing to revoke -> nothing changed -> no event
        await _record_role_event(db, actor, audit, granted=False, user=user, role=role, grant_id=grant_id)
        logger.info("staff_role_revoked actor=%s target=%s role=%s", actor.user_id, user.id, role_key)
    return {"changed": changed, "staff": await _staff_out_fresh(db, user)}
