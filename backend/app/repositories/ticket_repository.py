from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ticket import Ticket, TicketPriority


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
