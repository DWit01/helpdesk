from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware timestamps as UTC and return aware UTC values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("Timezone-aware datetime required")

        value = value.astimezone(UTC)
        # SQLite stores UTC clock values without timezone information.
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            # Naive stored timestamps are interpreted as UTC.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
