from collections.abc import Generator
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.config import Settings


def enable_sqlite_foreign_keys(
    dbapi_connection: SQLiteConnection,
    connection_record: ConnectionPoolEntry,
) -> None:
    # Remember the connection's original autocommit setting.
    original_autocommit = dbapi_connection.autocommit

    # Temporarily enable autocommit so SQLite accepts the PRAGMA.
    dbapi_connection.autocommit = True

    try:
        # Create a cursor to execute the SQLite command.
        cursor = dbapi_connection.cursor()

        try:
            # Enable foreign-key enforcement for this connection.
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            # Always close the cursor.
            cursor.close()

    finally:
        # Restore the connection's original autocommit setting.
        dbapi_connection.autocommit = original_autocommit


def build_engine(database_url: str) -> Engine:
    # Create an Engine using the supplied database URL.
    new_engine = create_engine(database_url)

    # Apply SQLite-specific configuration only to SQLite Engines.
    if new_engine.dialect.name == "sqlite":
        # Register the function to run whenever a new DBAPI
        # connection is established.
        event.listen(
            new_engine,
            "connect",
            enable_sqlite_foreign_keys,
        )

    return new_engine


# Load application configuration from environment variables.
# DATABASE_URL remains required.
settings = Settings()

# Create the application Engine.
engine = build_engine(settings.database_url)

# Create a factory for database Sessions.
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Generator[Session, None, None]:
    # Create a new Session when the dependency is executed.
    db = SessionLocal()

    try:
        # Provide the Session to the caller.
        yield db
    finally:
        # Always close the Session when the dependency finishes.
        db.close()
