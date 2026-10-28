from pydantic import Field

from app.schemas.common import InputSchema, PositiveId, ReadSchema, UtcTimestamp


class CategoryCreate(InputSchema):
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=255)


class CategoryUpdate(InputSchema):
    """Full replacement of editable fields for the planned PUT endpoint."""

    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool


class CategorySummary(ReadSchema):
    id: PositiveId
    name: str


class CategoryRead(CategorySummary):
    description: str | None
    is_active: bool
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
