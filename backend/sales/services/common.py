"""Small helpers shared by the Phase-2 Sales services."""
from typing import Optional

from fastapi import HTTPException


def field_error(
    field: str, message: str, status_code: int = 400, code: Optional[str] = None, **extra
) -> HTTPException:
    """The {"field", "message"} error shape the Sales UI (and services/admin.py) already uses, so the UI can
    attach the message to the right input. `code` is a stable machine-readable identifier for clients and
    tests; `extra` carries structured payloads (e.g. the duplicate report)."""
    detail = {"field": field, "message": message}
    if code:
        detail["code"] = code
    detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


def access_denied() -> HTTPException:
    # Same shape as every other role-denied response in the core API and in Phase 1.
    return HTTPException(status_code=403, detail="Access denied")
