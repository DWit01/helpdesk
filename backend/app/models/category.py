from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.ticket import Ticket


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(
        String(60),
        unique=True,
    )

    description: Mapped[str | None] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # One category can contain multiple tickets
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="category",
    )

    # Enforce the approved category name and description length
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 2 AND 60",
            name="ck_categories_name_length",
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 255",
            name="ck_categories_description_length",
        ),
    )
