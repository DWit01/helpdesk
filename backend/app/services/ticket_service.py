from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.exceptions import AccessDenied, Conflict, InvalidInput, NotFound
from app.models.category import Category
from app.models.comment import Comment
from app.models.ticket import Ticket, TicketPriority, TicketStatus
from app.models.user import UserRole
from app.repositories.category_repository import CategoryRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.pagination import Page, PageParams
from app.schemas.ticket import TicketAssign, TicketCreate, TicketRead, TicketStatusChange, TicketUpdate
from app.services.base import Service

ALLOWED_TRANSITIONS: set[tuple[TicketStatus, TicketStatus]] = {
    (TicketStatus.OPEN, TicketStatus.IN_PROGRESS),
    (TicketStatus.IN_PROGRESS, TicketStatus.OPEN),
    (TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED),
    (TicketStatus.RESOLVED, TicketStatus.CLOSED),
    (TicketStatus.RESOLVED, TicketStatus.IN_PROGRESS),
    (TicketStatus.OPEN, TicketStatus.CLOSED),
}


class TicketService(Service):
    def _active_category(self, category_id: int) -> Category:
        category = CategoryRepository(self.session).get_by_id(category_id)
        if category is None:
            raise NotFound("CATEGORY_NOT_FOUND", "Category was not found.")
        if not category.is_active:
            raise Conflict("CATEGORY_INACTIVE", "An active category is required.")
        return category

    def create(self, data: TicketCreate) -> TicketRead:
        with self._write():
            actor = self._actor()
            category = self._active_category(data.category_id)
            repository = TicketRepository(self.session)

            ticket = Ticket(
                # The non-null unique column needs a value for the first flush.
                ticket_number=f"pending-{uuid4().hex}",
                title=data.title,
                description=data.description,
                priority=data.priority,
                status=TicketStatus.OPEN,
                category=category,
                requester=actor,
                assignee=None,
            )
            repository.add(ticket)

            # The database supplies the ID; no max(id) + 1 race.
            ticket.ticket_number = f"HD-{ticket.id:06d}"
            repository.save(ticket)
            result = TicketRead.model_validate(ticket)

        return result

    def get(self, ticket_id: int) -> TicketRead:
        actor = self._actor()
        return TicketRead.model_validate(self._ticket(ticket_id, actor))

    def search(
        self,
        *,
        params: PageParams | None = None,
        statuses: list[TicketStatus] | None = None,
        priorities: list[TicketPriority] | None = None,
        category_id: int | None = None,
        assignee_id: UUID | None = None,
        assignee_me: bool = False,
        requester_id: UUID | None = None,
        unassigned_only: bool = False,
        q: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        sort: str = "-created_at",
    ) -> Page[TicketRead]:
        actor = self._actor()
        pagination = params if params is not None else PageParams()

        if actor.role == UserRole.REQUESTER:
            # Ignore requester-supplied ownership filters.
            requester_id = actor.id

        if assignee_me:
            self._require_staff(actor)
            if assignee_id is not None:
                raise InvalidInput("assignee_me cannot be combined with assignee_id")
            assignee_id = actor.id

        try:
            items, total = TicketRepository(self.session).search(
                statuses=statuses,
                priorities=priorities,
                category_id=category_id,
                assignee_id=assignee_id,
                requester_id=requester_id,
                unassigned_only=unassigned_only,
                q=q,
                created_from=created_from,
                created_to=created_to,
                sort=sort,
                page=pagination.page,
                page_size=pagination.page_size,
            )
        except ValueError as error:
            raise InvalidInput(str(error)) from error

        return Page[TicketRead](
            items=[TicketRead.model_validate(ticket) for ticket in items],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def stats(self) -> dict[str, dict[str, int]]:
        actor = self._actor()
        requester_id = actor.id if actor.role == UserRole.REQUESTER else None
        return TicketRepository(self.session).stats(requester_id=requester_id)

    def update(self, ticket_id: int, data: TicketUpdate) -> TicketRead:
        with self._write():
            actor = self._actor()
            ticket = self._ticket(ticket_id, actor)
            self._require_editable(ticket)

            if actor.role == UserRole.REQUESTER:
                if ticket.status != TicketStatus.OPEN or "priority" in data.model_fields_set:
                    raise AccessDenied()

            # Validate dependencies before changing the tracked ticket.
            category = self._active_category(data.category_id) if data.category_id is not None else None

            if data.title is not None:
                ticket.title = data.title
            if data.description is not None:
                ticket.description = data.description
            if data.priority is not None:
                ticket.priority = data.priority
            if category is not None:
                ticket.category = category

            TicketRepository(self.session).save(ticket)
            result = TicketRead.model_validate(ticket)

        return result

    def assign(self, ticket_id: int, data: TicketAssign) -> TicketRead:
        with self._write():
            actor = self._actor()
            ticket = self._ticket(ticket_id, actor)
            self._require_staff(actor)
            self._require_editable(ticket)

            if data.assignee_id is None:
                if ticket.status == TicketStatus.IN_PROGRESS:
                    raise Conflict("ASSIGNEE_REQUIRED", "An in-progress ticket must retain an assignee.")
                ticket.assignee = None
            else:
                ticket.assignee = self._active_assignee(data.assignee_id)

            TicketRepository(self.session).save(ticket)
            result = TicketRead.model_validate(ticket)

        return result

    def change_status(self, ticket_id: int, data: TicketStatusChange) -> TicketRead:
        with self._write():
            actor = self._actor()
            ticket = self._ticket(ticket_id, actor)
            transition = (ticket.status, data.status)

            if transition not in ALLOWED_TRANSITIONS:
                raise Conflict(
                    "INVALID_STATUS_TRANSITION",
                    "This status transition is not allowed.",
                )

            if actor.role == UserRole.REQUESTER:
                if ticket.status != TicketStatus.RESOLVED or data.status not in {
                    TicketStatus.CLOSED,
                    TicketStatus.IN_PROGRESS,
                }:
                    raise AccessDenied()

            if transition == (TicketStatus.OPEN, TicketStatus.CLOSED):
                self._require_admin(actor)

            if data.status == TicketStatus.IN_PROGRESS:
                if ticket.assignee_id is None:
                    raise Conflict(
                        "ASSIGNEE_REQUIRED",
                        "An assignee is required before work can start.",
                    )
                self._active_assignee(ticket.assignee_id)

            if data.status == TicketStatus.RESOLVED:
                if data.resolution_comment is None or not data.resolution_comment.strip():
                    raise Conflict(
                        "RESOLUTION_REQUIRED",
                        "A non-blank resolution comment is required.",
                    )
            elif data.resolution_comment is not None:
                raise InvalidInput("resolution_comment is only accepted when resolving a ticket.")

            now = datetime.now(UTC)
            ticket.status = data.status

            if data.status == TicketStatus.RESOLVED:
                ticket.resolved_at = now
            elif data.status != TicketStatus.CLOSED:
                ticket.resolved_at = None

            ticket.closed_at = now if data.status == TicketStatus.CLOSED else None

            # Only resolving creates a resolution comment.
            if data.status == TicketStatus.RESOLVED:
                assert data.resolution_comment is not None
                CommentRepository(self.session).add(
                    Comment(
                        ticket_id=ticket.id,
                        author_id=actor.id,
                        body=data.resolution_comment,
                        is_internal=False,
                    )
                )

            TicketRepository(self.session).save(ticket)
            result = TicketRead.model_validate(ticket)

        return result

    def delete(self, ticket_id: int) -> None:
        with self._write():
            actor = self._actor()
            ticket = self._ticket(ticket_id, actor)
            self._require_admin(actor)
            TicketRepository(self.session).soft_delete(ticket)
