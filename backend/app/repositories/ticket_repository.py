from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement

from app.models.ticket import Ticket, TicketPriority, TicketStatus


class TicketRepository:
    def __init__(self, session: Session) -> None:
        # Retain the Session supplied by the caller.
        self._session = session

    def get_by_id(self, ticket_id: int) -> Ticket | None:
        statement = (
            select(Ticket)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.requester),
                joinedload(Ticket.assignee),
            )
            .where(Ticket.id == ticket_id, Ticket.deleted_at.is_(None))
        )
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

    def soft_delete(self, ticket: Ticket) -> Ticket:
        """Mark a tracked ticket deleted while retaining its row and comments."""
        # As with other writes, the caller supplies a ticket tracked by this Session.
        # Services must check permissions and lifecycle rules before calling this method.
        if ticket.deleted_at is None:
            ticket.deleted_at = datetime.now(UTC)

        # Preserve the first deletion timestamp on repeat calls. The caller owns commit/rollback.
        self._session.flush()
        return ticket

    def search(
        self,
        *,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        statuses: list[TicketStatus] | None = None,
        priorities: list[TicketPriority] | None = None,
        category_id: int | None = None,
        assignee_id: UUID | None = None,
        requester_id: UUID | None = None,
        unassigned_only: bool = False,
        q: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        sort: str = "-created_at",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ticket], int]:
        if page < 1:
            raise ValueError("page must be at least 1")

        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        if assignee_id is not None and unassigned_only:
            raise ValueError("assignee_id cannot be combined with unassigned_only")

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

        conditions: list[ColumnElement[bool]] = [Ticket.deleted_at.is_(None)]

        # Preserve the original single-value repository interface.
        if status is not None:
            conditions.append(Ticket.status == status)
        if priority is not None:
            conditions.append(Ticket.priority == priority)

        # New multi-value filters support the planned repeated query parameters.
        if statuses is not None:
            conditions.append(Ticket.status.in_(statuses))
        if priorities is not None:
            conditions.append(Ticket.priority.in_(priorities))

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

        if q is not None and q.strip():
            # Treat %, _ and the escape character as literal search text.
            text = q.strip()
            conditions.append(
                or_(
                    Ticket.title.icontains(text, autoescape=True),
                    Ticket.description.icontains(text, autoescape=True),
                )
            )

        count_statement = select(func.count()).select_from(Ticket).where(*conditions)
        total = self._session.execute(count_statement).scalar_one()

        statement = (
            select(Ticket)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.requester),
                joinedload(Ticket.assignee),
            )
            .where(*conditions)
            .order_by(sort_expressions[sort], Ticket.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        return list(self._session.scalars(statement)), total

    def stats(self, *, requester_id: UUID | None = None) -> dict[str, dict[str, int]]:
        conditions: list[ColumnElement[bool]] = [Ticket.deleted_at.is_(None)]
        if requester_id is not None:
            conditions.append(Ticket.requester_id == requester_id)

        by_status = {status.value: 0 for status in TicketStatus}
        by_priority = {priority.value: 0 for priority in TicketPriority}

        status_rows = self._session.execute(
            select(Ticket.status, func.count()).where(*conditions).group_by(Ticket.status)
        )
        for status, count in status_rows:
            by_status[status.value] = count

        priority_rows = self._session.execute(
            select(Ticket.priority, func.count()).where(*conditions).group_by(Ticket.priority)
        )
        for priority, count in priority_rows:
            by_priority[priority.value] = count

        return {"by_status": by_status, "by_priority": by_priority}
