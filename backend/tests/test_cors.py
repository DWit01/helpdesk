import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main as main_module
from app.core.config import Settings
from app.main import app
from tests import test_api as api_fixtures

# Reuse the existing fixture with its isolated test database.
api = api_fixtures.api

# Synthetic website address used only by tests, not a deployment decision.
ALLOWED_ORIGIN = "https://helpdesk.example.test"


@pytest.fixture
def allowed_api(api: api_fixtures.Api, monkeypatch: pytest.MonkeyPatch) -> api_fixtures.Api:
    """Change only the test app's origin list, restoring its middleware afterwards."""
    monkeypatch.setattr(app, "cors_allowed_origins", (ALLOWED_ORIGIN,))
    # Starlette constructs the stack lazily; rebuild with this test's configuration.
    monkeypatch.setattr(app, "middleware_stack", None)
    return api


def test_unconfigured_origin_is_rejected(api: api_fixtures.Api) -> None:
    response = api.client.options(
        "/api/v1/me",
        headers={
            "Origin": "https://unapproved.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_allowed_preflight_does_not_require_authentication(
    allowed_api: api_fixtures.Api,
) -> None:
    allowed_api.monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    response = allowed_api.client.options(
        "/api/v1/me",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, Content-Type, X-Request-ID",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    ("method", "headers"),
    [("TRACE", "Authorization"), ("GET", "X-Unapproved-Header")],
)
def test_disallowed_method_or_header_fails_preflight(allowed_api: api_fixtures.Api, method: str, headers: str) -> None:
    response = allowed_api.client.options(
        "/api/v1/me",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )

    assert response.status_code == 400


def test_allowed_origin_receives_response_and_request_id(
    allowed_api: api_fixtures.Api,
) -> None:
    response = allowed_api.client.get("/health", headers={"Origin": ALLOWED_ORIGIN, "X-Request-ID": "cors-test"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["x-request-id"] == "cors-test"
    exposed = {header.strip().lower() for header in response.headers["access-control-expose-headers"].split(",")}
    assert {"x-request-id", "www-authenticate"} <= exposed
    assert "origin" in response.headers["vary"].lower()
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", ["https://unapproved.example", ALLOWED_ORIGIN + ":8443"])
def test_unapproved_actual_request_has_no_origin_permission(allowed_api: api_fixtures.Api, origin: str) -> None:
    response = allowed_api.client.get("/health", headers={"Origin": origin})

    # CORS restricts browser access to the response, not server-side execution.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_request_without_origin_still_works(allowed_api: api_fixtures.Api) -> None:
    response = allowed_api.client.get("/health")

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_still_requires_authentication(allowed_api: api_fixtures.Api) -> None:
    allowed_api.monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    response = allowed_api.client.get("/api/v1/me", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_allowed_origin_does_not_grant_admin_permissions(allowed_api: api_fixtures.Api) -> None:
    response = allowed_api.client.post(
        "/api/v1/categories",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"name": "Forbidden category"},
    )

    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_cors_settings_default_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    settings = Settings(database_url="sqlite:///:memory:")

    assert settings.cors_allowed_origins == []


def test_cors_settings_read_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["https://helpdesk.example.test", "http://127.0.0.1:3000"]')
    settings = Settings(database_url="sqlite:///:memory:")

    assert settings.cors_allowed_origins == [ALLOWED_ORIGIN, "http://127.0.0.1:3000"]


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "*",
        "null",
        "https://*.example.test",
        " https://helpdesk.example.test",
        "https://helpdesk.example.test/",
        "https://helpdesk.example.test/path",
        "https://helpdesk.example.test?query=value",
        "https://helpdesk.example.test#fragment",
        "https://user:password@helpdesk.example.test",
        "https://helpdesk.example.test:70000",
        "https://helpdesk.example.test:0",
        "https://helpdesk.example.test:",
        "https://helpdesk.example.test:not-a-port",
        "https://[invalid",
        "file://helpdesk.example.test",
        "https://",
        "https://helpdesk.example.test\\path",
    ],
)
def test_invalid_cors_origin_configuration_is_rejected(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///:memory:", cors_allowed_origins=[origin])


@pytest.mark.parametrize(
    "origin",
    [ALLOWED_ORIGIN, "http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"],
)
def test_valid_cors_origin_configuration_is_preserved(origin: str) -> None:
    settings = Settings(database_url="sqlite:///:memory:", cors_allowed_origins=[origin])

    assert settings.cors_allowed_origins == [origin]


def test_allowed_origin_receives_not_found_response(allowed_api: api_fixtures.Api) -> None:
    response = allowed_api.client.get("/missing-cors-test-route", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_allowed_origin_receives_validation_response(allowed_api: api_fixtures.Api) -> None:
    response = allowed_api.client.get("/api/v1/tickets/not-an-integer", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 422
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_allowed_origin_receives_readiness_failure(allowed_api: api_fixtures.Api) -> None:
    allowed_api.monkeypatch.setattr(main_module, "database_is_ready", lambda engine: False)
    response = allowed_api.client.get("/ready", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("origin", [ALLOWED_ORIGIN, "https://unapproved.example"])
def test_unexpected_server_error_keeps_safe_envelope_and_cors_policy(
    allowed_api: api_fixtures.Api, origin: str
) -> None:
    def unexpected_failure(engine: object) -> bool:
        raise RuntimeError("private-test-error-must-not-leak")

    allowed_api.monkeypatch.setattr(main_module, "database_is_ready", unexpected_failure)
    # Inspect the real HTTP 500 response rather than re-raising the test exception.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/ready", headers={"Origin": origin, "X-Request-ID": "cors-error-test"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == "cors-error-test"
    assert response.headers["x-request-id"] == "cors-error-test"
    assert "private-test-error-must-not-leak" not in response.text
    if origin == ALLOWED_ORIGIN:
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    else:
        assert "access-control-allow-origin" not in response.headers
