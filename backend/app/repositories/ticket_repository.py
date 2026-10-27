from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.ticket import Ticket, TicketPriority, TicketStatus


class TicketRepository:
    def __init__(self, session: Session) -> None:
        # Retain the Session supplied by the caller.
        self._session = session

    def get_by_id(self, ticket_id: int) -> Ticket | None:
        # Match the ID and exclude tickets marked as deleted.
        statement = select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.deleted_at.is_(None),
        )

        # Return the matching Ticket, or None when no visible ticket matches.
        return self._session.scalars(statement).one_or_none()

    def add(self, ticket: Ticket) -> Ticket:
        # Register the new Ticket with the caller's Session.
        self._session.add(ticket)

        # Insert it and obtain its generated ID without committing.
        self._session.flush()

        # Return the same object, now containing its assigned ID.
        return ticket

    def update_details(self, ticket: Ticket, *, title: str, description: str, priority: TicketPriority) -> Ticket:
        # Change the existing object tracked by this Session.
        ticket.title = title
        ticket.description = description
        ticket.priority = priority

        # Send the changes to the database without committing.
        self._session.flush()

        return ticket

    def update_category(self, ticket: Ticket, *, category_id: int) -> Ticket:
        # Change the category of the ticket tracked by this Session.
        ticket.category_id = category_id

        # Persist the change without committing the caller's transaction.
        self._session.flush()

        return ticket

    def update_assignee(self, ticket: Ticket, *, assignee_id: UUID | None) -> Ticket:
        # A UUID assigns the ticket; None removes its assignment.
        ticket.assignee_id = assignee_id

        # Persist the change without committing.
        self._session.flush()

        return ticket

    def save(self, ticket: Ticket) -> Ticket:
        # The caller must supply an existing ticket tracked by this Session.
        # Flush its pending changes without committing the transaction.
        self._session.flush()

        return ticket

    def search(
        self,
        *,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category_id: int | None = None,
        assignee_id: UUID | None = None,
        requester_id: UUID | None = None,
        unassigned_only: bool = False,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        sort: str = "-created_at",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ticket], int]:
        # Reject invalid pagination before issuing queries.
        if page < 1:
            raise ValueError("page must be at least 1")

        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        # A specific assignee and an unassigned-only search conflict.
        if assignee_id is not None and unassigned_only:
            raise ValueError("assignee_id cannot be combined with unassigned_only")

        # Require aware timestamps and normalise them to UTC.
        if created_from is not None:
            if created_from.tzinfo is None or created_from.utcoffset() is None:
                raise ValueError("created_from must be timezone-aware")
            created_from = created_from.astimezone(UTC)

        if created_to is not None:
            if created_to.tzinfo is None or created_to.utcoffset() is None:
                raise ValueError("created_to must be timezone-aware")
            created_to = created_to.astimezone(UTC)

        if created_from is not None and created_to is not None and created_from >= created_to:
            raise ValueError("created_from must be earlier than created_to")

        # Rank enum values explicitly instead of sorting their text.
        priority_rank = case(
            (Ticket.priority == TicketPriority.LOW, 1),
            (Ticket.priority == TicketPriority.MEDIUM, 2),
            (Ticket.priority == TicketPriority.HIGH, 3),
            (Ticket.priority == TicketPriority.CRITICAL, 4),
            else_=0,
        )

        status_rank = case(
            (Ticket.status == TicketStatus.OPEN, 1),
            (Ticket.status == TicketStatus.IN_PROGRESS, 2),
            (Ticket.status == TicketStatus.RESOLVED, 3),
            (Ticket.status == TicketStatus.CLOSED, 4),
            else_=0,
        )

        # Only these six sort choices are accepted.
        sort_expressions = {
            "created_at": Ticket.created_at.asc(),
            "-created_at": Ticket.created_at.desc(),
            "priority": priority_rank.asc(),
            "-priority": priority_rank.desc(),
            "status": status_rank.asc(),
            "-status": status_rank.desc(),
        }

        if sort not in sort_expressions:
            raise ValueError("sort must be created_at, priority or status, optionally prefixed with '-'")

        # Reuse these exact conditions for both the count and page queries.
        conditions: list[ColumnElement[bool]] = [Ticket.deleted_at.is_(None)]

        if status is not None:
            conditions.append(Ticket.status == status)

        if priority is not None:
            conditions.append(Ticket.priority == priority)

        if category_id is not None:
            conditions.append(Ticket.category_id == category_id)

        if assignee_id is not None:
            conditions.append(Ticket.assignee_id == assignee_id)

        if requester_id is not None:
            conditions.append(Ticket.requester_id == requester_id)

        if unassigned_only:
            conditions.append(Ticket.assignee_id.is_(None))

        if created_from is not None:
            conditions.append(Ticket.created_at >= created_from)

        if created_to is not None:
            conditions.append(Ticket.created_at < created_to)

        # Count every match without applying pagination.
        count_statement = select(func.count()).select_from(Ticket).where(*conditions)
        total = self._session.execute(count_statement).scalar_one()

        # Skip the records belonging to earlier pages.
        offset = (page - 1) * page_size

        # ID provides a deterministic order when the primary values tie.
        statement = (
            select(Ticket)
            .where(*conditions)
            .order_by(sort_expressions[sort], Ticket.id.asc())
            .offset(offset)
            .limit(page_size)
        )

        tickets = list(self._session.scalars(statement).all())

        # The caller retains transaction and Session ownership.
        return tickets, total
