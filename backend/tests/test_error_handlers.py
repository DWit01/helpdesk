import json
import logging
from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from app.api.error_handlers import register_exception_handlers
from app.core.exceptions import (
    AccessDenied,
    ApplicationError,
    AuthenticationRequired,
    Conflict,
    InvalidInput,
    NotFound,
)
from app.core.logging import JsonFormatter
from app.middleware.request_id import (
    request_id_context,
    request_id_middleware,
)

PRIVATE_VALUE = "synthetic-private-value"


class ValidationBody(BaseModel):
    count: int
    label: str

    @field_validator("label")
    @classmethod
    def reject_private_label(cls, value: str) -> str:
        if value == PRIVATE_VALUE:
            # Deliberately includes input to test that the API removes it.
            raise ValueError(f"Rejected value: {value}")
        return value


class CountResponse(BaseModel):
    count: int


@pytest.fixture
def error_client() -> Generator[TestClient, None, None]:
    # A fresh application per test. No database or production routes.
    app = FastAPI(debug=False)
    register_exception_handlers(app)
    app.middleware("http")(request_id_middleware)

    errors: dict[int, ApplicationError] = {
        401: AuthenticationRequired(),
        403: AccessDenied(),
        404: NotFound("TICKET_NOT_FOUND", "Ticket was not found."),
        409: Conflict(
            "INVALID_STATUS_TRANSITION",
            "This status change is not permitted.",
        ),
        422: InvalidInput("A valid date range is required."),
    }

    @app.get("/domain/{status_code}")
    def domain_failure(status_code: int) -> None:
        raise errors[status_code]

    def reject_identity() -> None:
        raise AuthenticationRequired()

    @app.get("/dependency", dependencies=[Depends(reject_identity)])
    def dependency_failure() -> dict[str, str]:
        return {"status": "unreachable"}

    @app.get("/query")
    def query(count: int) -> dict[str, int]:
        return {"count": count}

    @app.post("/body")
    def body(data: ValidationBody) -> dict[str, int]:
        return {"count": data.count}

    @app.get("/http/{status_code}")
    def http_failure(status_code: int) -> None:
        raise HTTPException(
            status_code=status_code,
            detail={"private": PRIVATE_VALUE},
            headers={"Retry-After": "5"},
        )

    @app.get("/unexpected")
    def unexpected_failure() -> None:
        raise RuntimeError(PRIVATE_VALUE)

    @app.get("/bad-response", response_model=CountResponse)
    def bad_response() -> dict[str, str]:
        return {"count": PRIVATE_VALUE}

    # Unexpected exceptions are deliberately re-raised by Starlette after
    # sending the 500 response. Disable client re-raising to inspect it.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(
    ("status_code", "code", "message"),
    [
        (401, "AUTH_TOKEN_INVALID", "A valid current user is required."),
        (403, "ACCESS_DENIED", "This action is not permitted."),
        (404, "TICKET_NOT_FOUND", "Ticket was not found."),
        (
            409,
            "INVALID_STATUS_TRANSITION",
            "This status change is not permitted.",
        ),
        (422, "VALIDATION_ERROR", "A valid date range is required."),
    ],
)
def test_domain_errors_preserve_contract(
    error_client: TestClient,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = error_client.get(
        f"/domain/{status_code}",
        headers={"X-Request-ID": "domain-test"},
    )

    assert response.status_code == status_code
    assert response.headers["X-Request-ID"] == "domain-test"
    assert response.json() == {
        "error": {
            "code": code,
            "message": message,
            "details": [],
            "request_id": "domain-test",
        }
    }


def test_dependency_error_uses_global_handler(error_client: TestClient) -> None:
    response = error_client.get("/dependency")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_validation_omits_input_context_and_custom_messages(
    error_client: TestClient,
) -> None:
    response = error_client.post(
        "/body",
        json={"count": PRIVATE_VALUE, "label": PRIVATE_VALUE},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "Request validation failed."
    assert len(error["details"]) == 2

    locations = {tuple(item["loc"]) for item in error["details"]}
    assert locations == {("body", "count"), ("body", "label")}

    for item in error["details"]:
        assert set(item) == {"loc", "type", "message"}

    assert PRIVATE_VALUE not in response.text
    assert "Rejected value" not in response.text


def test_query_validation_uses_standard_shape(error_client: TestClient) -> None:
    response = error_client.get("/query", params={"count": PRIVATE_VALUE})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["query", "count"]
    assert PRIVATE_VALUE not in response.text


def test_invalid_json_uses_standard_shape(error_client: TestClient) -> None:
    response = error_client.post(
        "/body",
        content='{"count":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "AUTH_TOKEN_INVALID"),
        (429, "RATE_LIMITED"),
        (500, "INTERNAL_ERROR"),
    ],
)
def test_http_errors_preserve_status_and_headers_without_raw_detail(
    error_client: TestClient,
    status_code: int,
    code: str,
) -> None:
    response = error_client.get(f"/http/{status_code}")

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["details"] == []
    assert response.headers["Retry-After"] == "5"
    assert PRIVATE_VALUE not in response.text


def test_unknown_route_uses_standard_shape(error_client: TestClient) -> None:
    response = error_client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_wrong_method_preserves_allow_header(error_client: TestClient) -> None:
    response = error_client.post("/query")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["Allow"]


@pytest.mark.parametrize("path", ["/query?count=1", "/domain/404", "/unexpected"])
def test_generated_request_id_is_a_uuid_and_matches_header(
    error_client: TestClient,
    path: str,
) -> None:
    response = error_client.get(path)

    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id

    if response.status_code >= 400:
        assert response.json()["error"]["request_id"] == request_id


@pytest.mark.parametrize("path", ["/unexpected", "/bad-response"])
def test_unexpected_errors_are_generic_and_have_request_ids(
    error_client: TestClient,
    path: str,
) -> None:
    response = error_client.get(
        path,
        headers={"X-Request-ID": "unexpected-test"},
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "unexpected-test"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "details": [],
            "request_id": "unexpected-test",
        }
    }
    assert PRIVATE_VALUE not in response.text


def test_unexpected_error_logs_safe_stack_and_matching_request_id(
    error_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger_name = "app.api.error_handlers"
    logger = logging.getLogger(logger_name)
    formatted_logs: list[str] = []

    class CaptureJson(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            formatted_logs.append(JsonFormatter().format(record))

    handler = CaptureJson()
    logger.addHandler(handler)

    try:
        with caplog.at_level(logging.ERROR, logger=logger_name):
            response = error_client.get(
                "/unexpected",
                headers={"X-Request-ID": "logged-test"},
            )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 500
    assert len(formatted_logs) == 1

    entry = json.loads(formatted_logs[0])
    assert entry["message"] == "unexpected_error"
    assert entry["request_id"] == "logged-test"
    assert entry["exception_type"] == "RuntimeError"
    assert entry["stack_trace"]
    assert any(frame["function"] == "unexpected_failure" for frame in entry["stack_trace"])
    assert PRIVATE_VALUE not in formatted_logs[0]


def test_handled_domain_error_does_not_log_an_unexpected_failure(
    error_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.api.error_handlers"):
        response = error_client.get("/domain/409")

    assert response.status_code == 409
    assert not [record for record in caplog.records if record.name == "app.api.error_handlers"]


def test_request_id_context_is_reset_after_failure(
    error_client: TestClient,
) -> None:
    previous_id = request_id_context.get()

    error_client.get(
        "/unexpected",
        headers={"X-Request-ID": "failed-request"},
    )

    assert request_id_context.get() == previous_id

    response = error_client.get("/query", params={"count": 1})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "failed-request"
