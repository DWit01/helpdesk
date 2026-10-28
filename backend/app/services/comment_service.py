from app.core.exceptions import AccessDenied, NotFound
from app.models.comment import Comment
from app.models.user import User, UserRole
from app.repositories.comment_repository import CommentRepository
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.services.base import Service


class CommentService(Service):
    def _comment(self, comment_id: int, actor: User) -> Comment:
        comment = CommentRepository(self.session).get_by_id(
            comment_id,
            include_internal=actor.role != UserRole.REQUESTER,
        )
        if comment is None:
            raise NotFound("COMMENT_NOT_FOUND", "Comment was not found.")

        # A comment ID must not bypass parent-ticket ownership.
        self._ticket(comment.ticket_id, actor)
        return comment

    def list(self, ticket_id: int) -> list[CommentRead]:
        actor = self._actor()
        self._ticket(ticket_id, actor)
        comments = CommentRepository(self.session).list_for_ticket(
            ticket_id,
            include_internal=actor.role != UserRole.REQUESTER,
        )
        return [CommentRead.model_validate(comment) for comment in comments]

    def create(self, ticket_id: int, data: CommentCreate) -> CommentRead:
        with self._write():
            actor = self._actor()
            ticket = self._ticket(ticket_id, actor)
            self._require_editable(ticket)

            if data.is_internal and actor.role == UserRole.REQUESTER:
                raise AccessDenied()

            comment = CommentRepository(self.session).add(
                Comment(
                    ticket_id=ticket.id,
                    author_id=actor.id,
                    body=data.body,
                    is_internal=data.is_internal,
                )
            )
            result = CommentRead.model_validate(comment)

        return result

    def update(self, comment_id: int, data: CommentUpdate) -> CommentRead:
        with self._write():
            actor = self._actor()
            comment = self._comment(comment_id, actor)
            ticket = self._ticket(comment.ticket_id, actor)
            self._require_editable(ticket)

            if comment.author_id != actor.id and actor.role != UserRole.ADMIN:
                raise AccessDenied()

            CommentRepository(self.session).update_body(comment, body=data.body)
            result = CommentRead.model_validate(comment)

        return result

    def delete(self, comment_id: int) -> None:
        with self._write():
            actor = self._actor()
            comment = self._comment(comment_id, actor)
            ticket = self._ticket(comment.ticket_id, actor)
            self._require_editable(ticket)

            if comment.author_id != actor.id and actor.role != UserRole.ADMIN:
                raise AccessDenied()

            CommentRepository(self.session).delete(comment)
