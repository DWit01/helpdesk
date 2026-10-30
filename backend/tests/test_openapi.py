from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app import main
from app.db.session import engine
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.schemas.comment import CommentCreate, CommentUpdate
from app.schemas.ticket import (
    TicketAssign,
    TicketCreate,
    TicketStatusChange,
    TicketUpdate,
)
from app.schemas.user import UserUpdate
from tests import test_readiness as readiness_fixtures

# Register the existing isolated fixtures in this test module.
readiness_client = readiness_fixtures.readiness_client
readiness_engine = readiness_fixtures.readiness_engine


@pytest.fixture
def openapi_schema(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # Generate a fresh schema while letting pytest restore the previous cache.
    monkeypatch.setattr(main.app, "openapi_schema", None)
    return main.app.openapi()


def test_openapi_is_public_and_does_not_query_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_database_access(*args: object, **kwargs: object) -> None:
        pytest.fail("OpenAPI generation must not access the database")

    monkeypatch.setattr(main.app, "openapi_schema", None)
    monkeypatch.setattr(engine, "connect", reject_database_access)
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    monkeypatch.delenv("FAKE_CURRENT_USER_ID", raising=False)

    with TestClient(main.app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Helpdesk API"
    assert schema["info"]["version"] == "0.1.0"
    assert "Keycloak Bearer access token" in schema["info"]["description"]
    assert not schema.get("security")

    bearer = schema["components"]["securitySchemes"]["KeycloakBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    assert bearer["bearerFormat"] == "JWT"

    for path in ("/health", "/ready"):
        assert not schema["paths"][path]["get"].get("security")
    assert main.app.openapi() is main.app.openapi_schema


def test_operations_have_descriptions_and_request_ids(
    openapi_schema: dict[str, Any],
) -> None:
    operation_ids: list[str] = []

    for path_item in openapi_schema["paths"].values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            assert operation["summary"]
            assert operation["description"]
            assert operation["tags"]
            operation_ids.append(operation["operationId"])

            for response in operation["responses"].values():
                assert "X-Request-ID" in response["headers"]

    assert len(operation_ids) == len(set(operation_ids))


def test_business_errors_use_the_actual_envelope(
    openapi_schema: dict[str, Any],
) -> None:
    expected_ref = {"$ref": "#/components/schemas/ErrorEnvelope"}

    for path, path_item in openapi_schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue

        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            assert operation["security"] == [{"KeycloakBearer": []}]
            assert "WWW-Authenticate" in operation["responses"]["401"]["headers"]
            responses = operation["responses"]
            for status in ("401", "403", "422", "500", "503"):
                assert responses[status]["content"]["application/json"]["schema"] == expected_ref

            if method in {"post", "put", "patch", "delete"}:
                assert responses["409"]["content"]["application/json"]["schema"] == expected_ref

    error_body = openapi_schema["components"]["schemas"]["ErrorEnvelope"]["properties"]["error"]
    assert set(error_body["required"]) == {
        "code",
        "message",
        "details",
        "request_id",
    }


@pytest.mark.parametrize(
    ("path", "method", "model"),
    [
        ("/api/v1/tickets", "post", TicketCreate),
        ("/api/v1/tickets/{ticket_id}", "patch", TicketUpdate),
        ("/api/v1/tickets/{ticket_id}/assign", "post", TicketAssign),
        ("/api/v1/tickets/{ticket_id}/status", "post", TicketStatusChange),
        ("/api/v1/categories", "post", CategoryCreate),
        ("/api/v1/categories/{category_id}", "put", CategoryUpdate),
        ("/api/v1/tickets/{ticket_id}/comments", "post", CommentCreate),
        ("/api/v1/comments/{comment_id}", "put", CommentUpdate),
        ("/api/v1/users/{user_id}", "patch", UserUpdate),
    ],
)
def test_request_examples_satisfy_input_schemas(
    openapi_schema: dict[str, Any],
    path: str,
    method: str,
    model: type[BaseModel],
) -> None:
    operation = openapi_schema["paths"][path][method]
    example = operation["requestBody"]["content"]["application/json"]["example"]

    # Validate documentation examples against the real request models.
    model.model_validate(example)


def test_ticket_search_and_hidden_resource_contracts(
    openapi_schema: dict[str, Any],
) -> None:
    paths = openapi_schema["paths"]
    search = paths["/api/v1/tickets"]["get"]
    parameters = {item["name"]: item for item in search["parameters"]}

    assert "Repeat" in parameters["status"]["description"]
    assert "Repeat" in parameters["priority"]["description"]
    assert "Exclusive" in parameters["created_to"]["description"]
    assert "literal substring" in parameters["q"]["description"]
    assert parameters["page_size"]["schema"]["maximum"] == 100

    hidden_ticket = paths["/api/v1/tickets/{ticket_id}"]["get"]
    assert "hidden" in hidden_ticket["description"]
    assert "404" in hidden_ticket["responses"]

    # Preserve bodyless successful DELETE responses.
    for path_item in paths.values():
        if "delete" in path_item:
            assert "content" not in path_item["delete"]["responses"]["204"]


def test_ready_documentation_matches_success(
    openapi_schema: dict[str, Any],
    readiness_client: TestClient,
) -> None:
    response = readiness_client.get("/ready")
    documented = openapi_schema["paths"]["/ready"]["get"]["responses"]["200"]

    assert response.status_code == 200
    assert response.json() == documented["content"]["application/json"]["example"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "Cache-Control" in documented["headers"]
    assert "X-Request-ID" in documented["headers"]


def test_ready_documentation_matches_database_failure(
    openapi_schema: dict[str, Any],
    readiness_client: TestClient,
    readiness_engine: Engine,
) -> None:
    def fail_query(*args: object, **kwargs: object) -> None:
        raise OperationalError("synthetic readiness failure", {}, RuntimeError("synthetic"))

    event.listen(readiness_engine, "before_cursor_execute", fail_query)
    try:
        response = readiness_client.get("/ready")
    finally:
        event.remove(readiness_engine, "before_cursor_execute", fail_query)

    documented = openapi_schema["paths"]["/ready"]["get"]["responses"]["503"]
    expected = documented["content"]["application/json"]["example"]

    assert response.status_code == 503
    actual = response.json()["error"]
    assert actual["code"] == expected["error"]["code"]
    assert actual["message"] == expected["error"]["message"]
    assert actual["details"] == []
    assert actual["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "Cache-Control" in documented["headers"]
    assert "X-Request-ID" in documented["headers"]
