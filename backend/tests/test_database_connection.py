import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import build_engine


def test_database_connection() -> None:
    # Create an isolated in-memory SQLite database Engine.
    test_engine = build_engine("sqlite:///:memory:")

    try:
        # Open a Connection and execute a simple SQL statement.
        with test_engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))

            # Verify that the database executed the statement.
            assert result.scalar_one() == 1

    finally:
        # Release the Engine's connection pool resources.
        test_engine.dispose()


def test_sqlite_foreign_keys_enabled() -> None:
    # Create an isolated SQLite Engine.
    test_engine = build_engine("sqlite:///:memory:")

    try:
        # Open a Connection to the SQLite database.
        with test_engine.connect() as connection:

            # Check whether SQLite foreign-key enforcement is enabled.
            result = connection.execute(text("PRAGMA foreign_keys"))

            # SQLite returns 1 when enforcement is enabled.
            assert result.scalar_one() == 1

    finally:
        # Always dispose of the test Engine.
        test_engine.dispose()


def test_sqlite_rejects_invalid_foreign_key() -> None:
    # Create an isolated in-memory SQLite database.
    test_engine = build_engine("sqlite:///:memory:")

    try:
        with test_engine.connect() as connection:

            # Create a parent table with a primary key.
            connection.execute(text("""
                    CREATE TABLE parent (
                        id INTEGER PRIMARY KEY
                    )
                    """))

            # Create a child table that references the parent table.
            connection.execute(text("""
                    CREATE TABLE child (
                        id INTEGER PRIMARY KEY,
                        parent_id INTEGER NOT NULL,
                        FOREIGN KEY (parent_id) REFERENCES parent(id)
                    )
                    """))

            # Attempt to reference a parent record that does not exist.
            # SQLite should reject this insertion.
            with pytest.raises(IntegrityError):
                connection.execute(text("""
                        INSERT INTO child (id, parent_id)
                        VALUES (1, 999)
                        """))

    finally:
        # Dispose of the Engine even if the test fails.
        test_engine.dispose()
