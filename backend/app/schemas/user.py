from uuid import UUID

from pydantic import Field

from app.models.user import UserRole
from app.schemas.common import InputSchema, ReadSchema, UtcTimestamp


class UserCreate(InputSchema):
    """Trusted provisioning input, not a public account-registration request."""

    keycloak_sub: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=50)
    email: str = Field(min_length=1, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole


class UserUpdate(InputSchema):
    """Input for the planned admin-only activation PATCH endpoint."""

    is_active: bool


class UserSummary(ReadSchema):
    id: UUID
    username: str


class UserRead(UserSummary):
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
