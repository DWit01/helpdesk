from uuid import UUID

from pydantic import Field

from app.models.ticket import TicketPriority, TicketStatus
from app.schemas.category import CategorySummary
from app.schemas.common import InputSchema, PartialUpdateSchema, PositiveId, ReadSchema, UtcTimestamp
from app.schemas.user import UserSummary


class TicketCreate(InputSchema):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=5000)
    category_id: PositiveId
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketUpdate(PartialUpdateSchema):
    # None is the omission default; explicitly supplied null is rejected.
    title: str | None = Field(default=None, min_length=5, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=5000)
    category_id: PositiveId | None = None
    priority: TicketPriority | None = None


class TicketAssign(InputSchema):
    # Required field: an explicit null means remove the assignment.
    assignee_id: UUID | None


class TicketStatusChange(InputSchema):
    status: TicketStatus
    resolution_comment: str | None = Field(default=None, min_length=1, max_length=2000)


class TicketRead(ReadSchema):
    id: PositiveId
    ticket_number: str
    title: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    category: CategorySummary
    requester: UserSummary
    assignee: UserSummary | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    resolved_at: UtcTimestamp | None
    closed_at: UtcTimestamp | None
