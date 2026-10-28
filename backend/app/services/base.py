from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AccessDenied, AuthenticationRequired, Conflict, NotFound
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from app.repositories.ticket_repository import TicketRepository
from app.repositories.user_repository import UserRepository


class Service:
    def __init__(self, session: Session, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id

    def _actor(self) -> User:
        # Read permissions from the database, not a caller-supplied role.
        actor = UserRepository(self.session).get_by_id(self.actor_id)
        if actor is None:
            raise AuthenticationRequired()
        if not actor.is_active:
            raise AccessDenied()
        return actor

    @staticmethod
    def _require_staff(actor: User) -> None:
        if actor.role not in {UserRole.AGENT, UserRole.ADMIN}:
            raise AccessDenied()

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != UserRole.ADMIN:
            raise AccessDenied()

    def _ticket(self, ticket_id: int, actor: User) -> Ticket:
        ticket = TicketRepository(self.session).get_by_id(ticket_id)

        # Use the same response for absent, deleted and hidden tickets.
        if ticket is None or (actor.role == UserRole.REQUESTER and ticket.requester_id != actor.id):
            raise NotFound("TICKET_NOT_FOUND", "Ticket was not found.")

        return ticket

    @staticmethod
    def _require_editable(ticket: Ticket) -> None:
        if ticket.status == TicketStatus.CLOSED:
            raise Conflict("TICKET_CLOSED", "Closed tickets are read-only.")

    def _active_assignee(self, user_id: UUID) -> User:
        user = UserRepository(self.session).get_by_id(user_id)
        if user is None or not user.is_active or user.role not in {UserRole.AGENT, UserRole.ADMIN}:
            raise Conflict("INVALID_ASSIGNEE", "Assignee must be an active agent or admin.")
        return user

    @contextmanager
    def _write(self) -> Iterator[None]:
        """One public write operation owns one commit or rollback."""
        try:
            yield
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            # Never expose SQL, parameters or raw driver messages.
            raise Conflict("DATA_CONFLICT", "The operation conflicts with existing data.") from error
        except Exception:
            self.session.rollback()
            raise
