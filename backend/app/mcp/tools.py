from pydantic import BaseModel

from app.models.ticket import TicketPriority, TicketStatus
from app.schemas.category import CategoryRead
from app.schemas.comment import CommentCreate, CommentRead
from app.schemas.common import InputSchema, PositiveId
from app.schemas.pagination import Page, PageParams
from app.schemas.ticket import TicketCreate, TicketRead, TicketStatusChange
from app.services.category_service import CategoryService
from app.services.comment_service import CommentService
from app.services.ticket_service import TicketService


class SearchTicketsInput(PageParams):
    """Validate search filters alongside the existing pagination rules."""

    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category_id: PositiveId | None = None
    query: str | None = None


class GetTicketInput(InputSchema):
    """Validate the ticket identifier before invoking services."""

    ticket_id: PositiveId


class AddCommentInput(CommentCreate):
    """Reuse comment validation and require a positive ticket identifier."""

    ticket_id: PositiveId


class TicketDetail(BaseModel):
    """A visible ticket and its public comments."""

    ticket: TicketRead
    comments: list[CommentRead]


def search_tickets(
    service: TicketService,
    *,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    category_id: int | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> Page[TicketRead]:
    """Search tickets visible to the caller represented by the service."""
    filters = SearchTicketsInput(
        status=status,
        priority=priority,
        category_id=category_id,
        query=query,
        page=page,
        page_size=page_size,
    )

    return service.search(
        params=PageParams(
            page=filters.page,
            page_size=filters.page_size,
        ),
        statuses=[filters.status] if filters.status is not None else None,
        priorities=[filters.priority] if filters.priority is not None else None,
        category_id=filters.category_id,
        q=filters.query,
    )


def get_ticket(
    service: TicketService,
    *,
    ticket_id: int,
) -> TicketDetail:
    """Return a visible ticket with public comments, excluding internal notes."""
    data = GetTicketInput(ticket_id=ticket_id)

    ticket = service.get(data.ticket_id)
    comment_service = CommentService(service.session, service.actor_id)
    comments = comment_service.list(data.ticket_id)

    return TicketDetail(
        ticket=ticket,
        comments=[comment for comment in comments if not comment.is_internal],
    )


def create_ticket(
    service: TicketService,
    *,
    title: str,
    description: str,
    category_id: int,
    priority: TicketPriority = TicketPriority.MEDIUM,
) -> TicketRead:
    """Create a ticket for the caller represented by the trusted service."""
    # Reuse REST input validation without duplicating its constraints.
    data = TicketCreate(
        title=title,
        description=description,
        category_id=category_id,
        priority=priority,
    )

    # The service owns requester identity, business rules and the transaction.
    return service.create(data)


def add_comment(
    service: CommentService,
    *,
    ticket_id: int,
    body: str,
    is_internal: bool = False,
) -> CommentRead:
    """Add a comment or permitted internal note as the trusted service caller."""
    data = AddCommentInput(
        ticket_id=ticket_id,
        body=body,
        is_internal=is_internal,
    )

    # AddCommentInput is a CommentCreate subclass accepted by the service.
    # The service owns author identity, permissions and commit/rollback.
    return service.create(data.ticket_id, data)


class UpdateTicketStatusInput(TicketStatusChange):
    """Reuse lifecycle input validation and require a positive ticket ID."""

    ticket_id: PositiveId


class ListCategoriesInput(InputSchema):
    """The categories tool accepts no user-supplied arguments."""


def update_ticket_status(
    service: TicketService,
    *,
    ticket_id: int,
    status: TicketStatus,
    resolution_comment: str | None = None,
) -> TicketRead:
    """Apply a lifecycle change using the caller's trusted service."""
    data = UpdateTicketStatusInput(
        ticket_id=ticket_id,
        status=status,
        resolution_comment=resolution_comment,
    )
    return service.change_status(data.ticket_id, data)


def list_categories(service: CategoryService) -> list[CategoryRead]:
    """Return active categories only, for an authenticated caller."""
    return service.list()
