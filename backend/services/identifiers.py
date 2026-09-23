"""Login identifiers: the email and phone namespaces, kept disjoint.

POST /auth/login takes ONE free-text field (`email_or_phone`). It can only resolve to a single account
while no string can be both somebody's email and somebody's phone:

  * every email contains '@'  - EmailStr on every email write path, and ck_users_email_has_at;
  * no phone contains '@'     - validate_phone() on every phone write path, and ck_users_phone_no_at.

So an identifier that contains '@' can only be an email, and one that does not can only be a phone
(is_email_identifier), and login looks in exactly one column.

Phones are otherwise left exactly as they always were: free text, any country's format, letters
allowed. '@' is the only thing rejected, so every existing valid value stays valid.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

PHONE_HAS_AT_SIGN = "Phone number must not contain '@'"
PHONE_NOT_TEXT = "Phone number must be text"
PHONE_TAKEN = "Phone number already registered"


def is_email_identifier(identifier: str) -> bool:
    """A login identifier is an email exactly when it contains '@'."""
    return "@" in identifier


def phone_error(phone) -> Optional[str]:
    """Why `phone` may not be stored, or None if it is acceptable. Callers that raise their own
    error shape (Sales' {"field","message"} details) use this directly."""
    if not isinstance(phone, str):
        return PHONE_NOT_TEXT  # PUT /employee/profile and PUT /employees/{id} take raw dicts
    if "@" in phone:
        return PHONE_HAS_AT_SIGN
    return None


def validate_phone(phone, *, field: Optional[str] = None) -> str:
    """Raise a clean 400 for a phone that may not be stored; return it unchanged otherwise.

    `field` selects the detail shape: given, {"field", "message"} (what services/admin.py and the Sales
    UI attach to an input); omitted, a plain string (what the employee/profile screens toast)."""
    message = phone_error(phone)
    if message is not None:
        raise HTTPException(status_code=400, detail={"field": field, "message": message} if field else message)
    return phone


def translate_phone_integrity_error(exc: IntegrityError, *, field: Optional[str] = None) -> Optional[HTTPException]:
    """The database is the real guarantee behind the pre-checks: a concurrent write can win the race
    they lose. Map the phone constraints to the same clean 400 the pre-check would have raised (never a
    500); None means the IntegrityError is about something else and the caller should re-raise it."""
    text = str(getattr(exc, "orig", exc))
    if "uq_users_phone_active" in text:
        message = PHONE_TAKEN
    elif "ck_users_phone_no_at" in text:
        message = PHONE_HAS_AT_SIGN
    else:
        return None
    return HTTPException(status_code=400, detail={"field": field, "message": message} if field else message)
