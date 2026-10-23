from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.db.session import get_db


def test_get_db_closes_session() -> None:
    # Use a mock Session to check cleanup without database operations.
    mock_session = Mock(spec=Session)

    # Replace the factory where get_db() looks it up.
    with patch("app.db.session.SessionLocal", return_value=mock_session):
        # Calling get_db() creates a generator; its body has not run yet.
        dependency = get_db()

        try:
            # Advance to yield and verify the caller receives our Session.
            assert next(dependency) is mock_session

            # The Session must remain available while the caller uses it.
            mock_session.close.assert_not_called()
        finally:
            # Explicitly close the generator, triggering get_db()'s finally.
            dependency.close()

        # Verify that explicit generator cleanup closed the Session once.
        mock_session.close.assert_called_once_with()


def test_get_db_closes_session_on_completion() -> None:
    mock_session = Mock(spec=Session)

    with patch("app.db.session.SessionLocal", return_value=mock_session):
        dependency = get_db()

        try:
            assert next(dependency) is mock_session

            # Resume after yield. Normal completion raises StopIteration.
            with pytest.raises(StopIteration):
                next(dependency)

            # Check cleanup BEFORE the test's safety-net close().
            mock_session.close.assert_called_once_with()
        finally:
            # Ensure cleanup even if an assertion fails.
            dependency.close()


def test_get_db_closes_session_on_exception() -> None:
    mock_session = Mock(spec=Session)

    with patch("app.db.session.SessionLocal", return_value=mock_session):
        dependency = get_db()

        try:
            assert next(dependency) is mock_session

            # Inject an error where the generator is paused at yield.
            # Verify that get_db() does not swallow that error.
            with pytest.raises(RuntimeError, match="simulated failure"):
                dependency.throw(RuntimeError("simulated failure"))

            # Verify the error path closed the Session before our safety net.
            mock_session.close.assert_called_once_with()
        finally:
            dependency.close()
