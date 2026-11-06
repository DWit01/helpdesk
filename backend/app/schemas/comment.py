from uuid import UUID

from pydantic import Field

from app.schemas.common import InputSchema, PositiveId, ReadSchema, UtcTimestamp
from app.schemas.user import UserSummary


class CommentCreate(InputSchema):
    body: str = Field(min_length=1, max_length=2000)
    is_internal: bool = False


class CommentUpdate(InputSchema):
    """Replace the comment text without changing its author or visibility."""

    body: str = Field(min_length=1, max_length=2000)


class CommentRead(ReadSchema):
    id: PositiveId
    ticket_id: PositiveId
    author_id: UUID
    author: UserSummary
    body: str
    is_internal: bool
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
