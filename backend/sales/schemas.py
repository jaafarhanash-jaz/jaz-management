from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class _Strict(BaseModel):
    """Request bodies reject unknown fields outright (422). That is what makes it
    impossible for a client to smuggle `role`, `company_id`, `password_hash`,
    `status` (where not offered) etc. into a staff-management call."""

    model_config = ConfigDict(extra="forbid")


def _strip_nonblank(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class StaffCreate(_Strict):
    name: str = Field(max_length=200)
    email: EmailStr
    phone: str = Field(max_length=50)
    password: SecretStr = Field(min_length=1, max_length=200)  # strength enforced by core validate_password_strength; a SecretStr prints as ********** if the model is ever logged
    # Initial roles, by key. Validated server-side against staff_roles (never trusted).
    role_keys: List[str] = Field(default_factory=list, max_length=20)

    _v_name = field_validator("name")(_strip_nonblank)
    _v_phone = field_validator("phone")(_strip_nonblank)


class StaffUpdate(_Strict):
    """Whitelist of editable staff-account fields. There is deliberately no
    `role`, `company_id`, `password` or `id` here."""

    name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=50)
    status: Optional[Literal["active", "inactive"]] = None

    _v_name = field_validator("name")(_strip_nonblank)
    _v_phone = field_validator("phone")(_strip_nonblank)


class PasswordReset(_Strict):
    new_password: SecretStr = Field(min_length=1, max_length=200)


class RoleAssignment(_Strict):
    role_key: str = Field(min_length=1, max_length=100)


# ---- responses (explicit shapes; nothing here can carry a password hash) ----

class RoleRef(BaseModel):
    key: str
    name_en: str
    name_ar: str


class RoleDefinition(RoleRef):
    description: Optional[str] = None
    is_system: bool
    permissions: List[str]


class StaffOut(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    status: str
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    roles: List[RoleRef]


class StaffList(BaseModel):
    items: List[StaffOut]
    total: int
    limit: int
    offset: int


class RoleChangeResult(BaseModel):
    changed: bool
    staff: StaffOut


class MeUser(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    role: str
    status: str


class MeOut(BaseModel):
    user: MeUser
    is_super_admin: bool
    roles: List[RoleRef]
    permissions: List[str]
    modules: List[str]
