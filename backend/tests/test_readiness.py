import asyncio
from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app import main
from app.db.session import build_engine

PRIVATE_MARKER = "synthetic-private-database-detail"


@pytest.fixture
def readiness_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Engine, None, None]:
    # Replace the engine actually used by /ready with a disposable database.
    # No application tables, migrations or development data are needed.
    database_path = (tmp_path / "readiness.sqlite3").as_posix()
    test_engine = build_engine(f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setattr(main, "engine", test_engine)

    try:
        yield test_engine
    finally:
        test_engine.dispose()


@pytest.fixture
def readiness_client(
    readiness_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    # Public operations must work without temporary fake authentication.
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    monkeypatch.delenv("FAKE_CURRENT_USER_ID", raising=False)

    with TestClient(main.app) as client:
        yield client


def test_ready_is_public_and_returns_success(
    readiness_client: TestClient,
) -> None:
    response = readiness_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["Cache-Control"] == "no-store"

    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id


@pytest.mark.parametrize(
    "failure_event",
    ["do_connect", "before_cursor_execute"],
    ids=["connection-failure", "query-failure"],
)
def test_ready_returns_safe_503_and_recovers(
    readiness_client: TestClient,
    readiness_engine: Engine,
    caplog: pytest.LogCaptureFixture,
    failure_event: str,
) -> None:
    def fail_database_operation(
        *args: object,
        **kwargs: object,
    ) -> None:
        # Synthetic content tests sanitisation without using real secrets.
        raise OperationalError(
            f"SELECT '{PRIVATE_MARKER}'",
            {"private": PRIVATE_MARKER},
            RuntimeError(f"driver failure: {PRIVATE_MARKER}"),
        )

    event.listen(
        readiness_engine,
        failure_event,
        fail_database_operation,
    )

    try:
        response = readiness_client.get("/ready")
    finally:
        event.remove(
            readiness_engine,
            failure_event,
            fail_database_operation,
        )

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"

    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id

    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "Database is unavailable.",
            "details": [],
            "request_id": request_id,
        }
    }

    assert PRIVATE_MARKER not in response.text
    assert PRIVATE_MARKER not in caplog.text

    # An earlier failure must not permanently mark the application unready.
    recovered = readiness_client.get("/ready")

    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ready"}


def test_health_does_not_access_the_database(
    readiness_client: TestClient,
    readiness_engine: Engine,
) -> None:
    connection_attempts: list[bool] = []

    def reject_connection(
        *args: object,
        **kwargs: object,
    ) -> None:
        connection_attempts.append(True)
        raise OperationalError(
            "synthetic connection failure",
            {},
            RuntimeError(PRIVATE_MARKER),
        )

    event.listen(readiness_engine, "do_connect", reject_connection)

    try:
        health_response = readiness_client.get("/health")

        assert health_response.status_code == 200
        assert health_response.json() == {"status": "ok"}
        assert connection_attempts == []

        # Prove the failure hook is active for the readiness endpoint.
        ready_response = readiness_client.get("/ready")

        assert ready_response.status_code == 503
        assert connection_attempts == [True]
    finally:
        event.remove(readiness_engine, "do_connect", reject_connection)


def test_ready_executes_database_query_outside_the_event_loop(
    readiness_client: TestClient,
    readiness_engine: Engine,
) -> None:
    statements: list[str] = []

    def inspect_query(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        # A synchronous worker thread has no running asyncio event loop.
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()

        statements.append(statement)

    event.listen(readiness_engine, "before_cursor_execute", inspect_query)

    try:
        response = readiness_client.get("/ready")
    finally:
        event.remove(
            readiness_engine,
            "before_cursor_execute",
            inspect_query,
        )

    assert response.status_code == 200
    assert statements == ["SELECT 1"]
