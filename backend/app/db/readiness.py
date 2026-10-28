from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


def database_is_ready(database_engine: Engine) -> bool:
    """Check reachability without reading or changing application data."""
    try:
        # Return the connection to the pool even when execution fails.
        with database_engine.connect() as connection:
            return bool(connection.execute(text("SELECT 1")).scalar_one() == 1)
    except SQLAlchemyError:
        # Database errors can contain SQL, credentials or driver details.
        # Expected readiness failures expose only a boolean result.
        return False
