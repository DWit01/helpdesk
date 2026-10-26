from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.ticket import Ticket
    from app.models.user import User


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Every comment belongs to a ticket
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id"),
    )

    # Every comment has an author
    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
    )

    body: Mapped[str] = mapped_column(Text)

    # Distinguishes internal notes from ordinary comments
    is_internal: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    ticket: Mapped["Ticket"] = relationship(
        "Ticket",
        back_populates="comments",
    )

    author: Mapped["User"] = relationship(
        "User",
        back_populates="comments",
    )

    __table_args__ = (
        # Comments must contain between 1 and 2000 characters
        CheckConstraint(
            "length(body) BETWEEN 1 AND 2000",
            name="ck_comments_body_length",
        ),
        Index("ix_comments_ticket_id", "ticket_id"),
    )
