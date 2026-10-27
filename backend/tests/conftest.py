import os
from collections.abc import Generator
from sqlite3 import Connection
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    # Create an isolated SQLite database for each test
    engine = create_engine("sqlite+pysqlite:///:memory:")

    # Enable SQLite foreign-key enforcement
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: Connection,
        connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        Base.metadata.create_all(engine)

        with Session(engine) as session:
            yield session

    finally:
        engine.dispose()


def pytest_sessionstart(session: pytest.Session) -> None:
    # Set safe configuration before pytest collects test modules.
    # Override any inherited development database URL.
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
