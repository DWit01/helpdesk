from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.ticket import Ticket


class CommentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, comment_id: int, *, include_internal: bool = False) -> Comment | None:
        # Join the parent ticket so deleted tickets cannot expose comments.
        statement = (
            select(Comment)
            .join(Ticket, Comment.ticket_id == Ticket.id)
            .where(Comment.id == comment_id, Ticket.deleted_at.is_(None))
        )
        if not include_internal:
            statement = statement.where(Comment.is_internal.is_(False))

        return self._session.scalars(statement).one_or_none()

    def list_for_ticket(self, ticket_id: int, *, include_internal: bool = False) -> list[Comment]:
        statement = (
            select(Comment)
            .join(Ticket, Comment.ticket_id == Ticket.id)
            .where(Comment.ticket_id == ticket_id, Ticket.deleted_at.is_(None))
        )
        if not include_internal:
            statement = statement.where(Comment.is_internal.is_(False))

        # The ID breaks ties when comments have the same creation time.
        statement = statement.order_by(Comment.created_at, Comment.id)
        return list(self._session.scalars(statement))

    def add(self, comment: Comment) -> Comment:
        self._session.add(comment)
        self._session.flush()
        return comment

    def update_body(self, comment: Comment, *, body: str) -> Comment:
        # The caller supplies a comment tracked by this Session.
        comment.body = body
        self._session.flush()
        return comment

    def delete(self, comment: Comment) -> None:
        # Comments have no soft-delete field; remove the supplied tracked row.
        # The service must check permissions and the parent ticket first.
        self._session.delete(comment)
        self._session.flush()
