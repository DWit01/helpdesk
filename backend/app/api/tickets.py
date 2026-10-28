from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import AwareDatetime

from app.api.dependencies import ResourceId, Tickets
from app.models.ticket import TicketPriority, TicketStatus
from app.schemas.pagination import Page, PageParams
from app.schemas.ticket import (
    TicketAssign,
    TicketCreate,
    TicketRead,
    TicketStatusChange,
    TicketUpdate,
)

router = APIRouter(
    prefix="/tickets",
    tags=["Tickets"],
)

SortOrder = Literal[
    "created_at",
    "-created_at",
    "priority",
    "-priority",
    "status",
    "-status",
]


def utc_boundary(value: date | datetime | None) -> datetime | None:
    """Interpret a date as midnight UTC; normalise aware timestamps to UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


@router.get("", summary="Search visible tickets")
def list_tickets(
    service: Tickets,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    statuses: Annotated[list[TicketStatus] | None, Query(alias="status")] = None,
    priorities: Annotated[list[TicketPriority] | None, Query(alias="priority")] = None,
    category_id: Annotated[int | None, Query(gt=0)] = None,
    assignee_id: UUID | None = None,
    assignee: Annotated[Literal["me"] | None, Query()] = None,
    requester_id: UUID | None = None,
    unassigned_only: bool = False,
    q: str | None = None,
    created_from: Annotated[date | AwareDatetime | None, Query()] = None,
    created_to: Annotated[date | AwareDatetime | None, Query()] = None,
    sort: SortOrder = "-created_at",
) -> Page[TicketRead]:
    """Filter and paginate visible tickets; created_to is an exclusive boundary."""
    return service.search(
        params=PageParams(page=page, page_size=page_size),
        statuses=statuses,
        priorities=priorities,
        category_id=category_id,
        assignee_id=assignee_id,
        assignee_me=assignee == "me",
        requester_id=requester_id,
        unassigned_only=unassigned_only,
        q=q,
        created_from=utc_boundary(created_from),
        created_to=utc_boundary(created_to),
        sort=sort,
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a ticket")
def create_ticket(data: TicketCreate, service: Tickets) -> TicketRead:
    """Create an OPEN ticket owned by the current user."""
    return service.create(data)


# Register this static path before the dynamic ticket ID route.
@router.get("/stats", summary="Get visible ticket counts")
def ticket_stats(service: Tickets) -> dict[str, dict[str, int]]:
    """Return scoped counts by status and priority, including zero counts."""
    return service.stats()


@router.get("/{ticket_id}", summary="Get a visible ticket")
def get_ticket(ticket_id: ResourceId, service: Tickets) -> TicketRead:
    """Return a ticket, or 404 when it is absent, deleted or hidden."""
    return service.get(ticket_id)


@router.patch("/{ticket_id}", summary="Update ticket details")
def update_ticket(
    ticket_id: ResourceId,
    data: TicketUpdate,
    service: Tickets,
) -> TicketRead:
    """Update supplied editable fields under the existing permission rules."""
    return service.update(ticket_id, data)


@router.post("/{ticket_id}/assign", summary="Assign or unassign a ticket")
def assign_ticket(
    ticket_id: ResourceId,
    data: TicketAssign,
    service: Tickets,
) -> TicketRead:
    """Assign an active staff user, or explicitly supply null to unassign."""
    return service.assign(ticket_id, data)


@router.post("/{ticket_id}/status", summary="Change ticket status")
def change_ticket_status(
    ticket_id: ResourceId,
    data: TicketStatusChange,
    service: Tickets,
) -> TicketRead:
    """Apply a permitted transition and persist any resolution comment atomically."""
    return service.change_status(ticket_id, data)


@router.delete(
    "/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a ticket",
)
def delete_ticket(ticket_id: ResourceId, service: Tickets) -> Response:
    """Allow an admin to hide a ticket while retaining its database row."""
    service.delete(ticket_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
