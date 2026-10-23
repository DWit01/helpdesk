from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.comment import Comment
    from app.models.user import User


# Define permitted ticket statuses
class TicketStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


# Define permitted ticket priorities
class TicketPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Unique, human-readable ticket identifier
    ticket_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
    )

    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)

    status: Mapped[TicketStatus] = mapped_column(
        SQLEnum(
            TicketStatus,
            create_constraint=True,
            name="ticket_status",
        ),
        default=TicketStatus.OPEN,
    )

    priority: Mapped[TicketPriority] = mapped_column(
        SQLEnum(
            TicketPriority,
            create_constraint=True,
            name="ticket_priority",
        ),
        default=TicketPriority.MEDIUM,
    )

    # Every ticket belongs to a category
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"),
    )

    # Every ticket has a requester
    requester_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
    )

    # An unassigned ticket has a NULL assignee
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # Supports soft deletion without immediately removing records
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="tickets",
    )

    requester: Mapped["User"] = relationship(
        "User",
        back_populates="requested_tickets",
        foreign_keys=[requester_id],
    )

    assignee: Mapped["User | None"] = relationship(
        "User",
        back_populates="assigned_tickets",
        foreign_keys=[assignee_id],
    )

    comments: Mapped[list["Comment"]] = relationship(
        "Comment",
        back_populates="ticket",
    )

    # Enforce field lengths and index commonly searched columns
    __table_args__ = (
        CheckConstraint(
            "length(title) BETWEEN 5 AND 120",
            name="ck_tickets_title_length",
        ),
        CheckConstraint(
            "length(description) BETWEEN 10 AND 5000",
            name="ck_tickets_description_length",
        ),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_assignee_id", "assignee_id"),
        Index("ix_tickets_requester_id", "requester_id"),
        Index("ix_tickets_created_at", "created_at"),
    )
