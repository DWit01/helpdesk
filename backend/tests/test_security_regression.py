import time
from typing import Any
from urllib.error import URLError

import jwt
import pytest
from httpx2 import Response
from sqlalchemy import select

from app.db.base import Base
from app.models import UserRole
from tests import test_authentication as auth_fixtures
from tests import test_security as security_fixtures

# Reuse existing isolated databases, RSA keys and mocked JWKS transport.
api = auth_fixtures.api
keys = auth_fixtures.keys
harness = auth_fixtures.harness
authenticated_api = auth_fixtures.authenticated_api

AuthenticatedApi = auth_fixtures.AuthenticatedApi

INVALID_CASES = [
    "expired",
    "wrong-issuer",
    "wrong-audience",
    "not-yet-valid",
    "future-issued-at",
    "missing-expiry",
    "wrong-signature",
    "unknown-key",
    "unsigned",
    "hmac",
    "malformed-roles",
    "untrusted-key-url",
]


def database_snapshot(
    authenticated_api: AuthenticatedApi,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Capture persisted values in all four application tables."""
    with authenticated_api.api.sessions() as db:
        return {
            table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)))
            for table in Base.metadata.sorted_tables
        }


def invalid_token(
    authenticated_api: AuthenticatedApi,
    case: str,
    subject: str,
) -> str:
    """Build genuinely signed test tokens, with one deliberate defect."""
    harness = authenticated_api.harness
    changes: dict[str, Any] = {
        "sub": subject,
        "preferred_username": "attempted-profile-change",
        "email": "attempted-change@example.test",
        "realm_access": {"roles": ["admin"]},
    }

    if case == "expired":
        changes["exp"] = int(time.time()) - 60
    elif case == "wrong-issuer":
        changes["iss"] = "https://untrusted.example.test/realms/helpdesk"
    elif case == "wrong-audience":
        changes["aud"] = "another-api"
    elif case == "not-yet-valid":
        changes["nbf"] = int(time.time()) + 3600
    elif case == "future-issued-at":
        changes["iat"] = int(time.time()) + 3600
    elif case == "missing-expiry":
        return harness.token(changes, remove="exp")
    elif case == "wrong-signature":
        return harness.token(changes, key=harness.keys.second)
    elif case == "unknown-key":
        return harness.token(changes, kid="unknown-key")
    elif case in {"unsigned", "hmac"}:
        claims = security_fixtures.default_claims()
        claims.update(changes)
        algorithm = "none" if case == "unsigned" else "HS256"
        signing_key = "" if case == "unsigned" else "synthetic-test-secret-with-at-least-thirty-two-bytes"
        return jwt.encode(
            claims,
            signing_key,
            algorithm=algorithm,
            headers={"kid": "first"},
        )
    elif case == "malformed-roles":
        changes["realm_access"] = {"roles": "admin"}
    elif case == "untrusted-key-url":
        return harness.token(
            changes,
            key=harness.keys.second,
            headers={"jku": "https://untrusted.example.test/keys"},
        )
    else:
        raise AssertionError(f"Unknown test case: {case}")

    return harness.token(changes)


def assert_safe_failure(
    response: Response,
    *,
    status: int,
    code: str,
    request_id: str,
) -> None:
    """Check the shared HTTP contract, including authentication challenges."""
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["details"] == []
    assert error["request_id"] == request_id
    assert response.headers["x-request-id"] == request_id

    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    else:
        assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("case", INVALID_CASES)
@pytest.mark.parametrize("existing_subject", [False, True])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_invalid_tokens_cannot_change_persisted_data(
    authenticated_api: AuthenticatedApi,
    caplog: pytest.LogCaptureFixture,
    case: str,
    existing_subject: bool,
    method: str,
) -> None:
    # Exercise both attempted first-login creation and existing-user sync.
    subject = "api-test-requester" if existing_subject else "rejected-new-subject"
    token = invalid_token(authenticated_api, case, subject)
    before = database_snapshot(authenticated_api)
    request_id = f"rejected-{case}-{method.lower()}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": request_id,
    }

    if method == "GET":
        response = authenticated_api.api.client.get(
            "/api/v1/me",
            headers=headers,
        )
    else:
        response = authenticated_api.api.client.post(
            "/api/v1/tickets",
            headers=headers,
            json={
                "title": "This ticket must never be created",
                "description": "An invalid token must prevent this write.",
                "category_id": authenticated_api.api.category_id,
            },
        )

    assert_safe_failure(
        response,
        status=401,
        code="AUTH_TOKEN_INVALID",
        request_id=request_id,
    )
    assert database_snapshot(authenticated_api) == before

    # Never echo credentials or attempted identity changes.
    assert token not in response.text
    assert token not in caplog.text
    assert "attempted-profile-change" not in response.text
    assert "attempted-change@example.test" not in response.text

    # Even a JWT carrying a key URL must use only configured JWKS.
    assert all(url == security_fixtures.JWKS_URL for url in authenticated_api.harness.opener.urls)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/me",
        "/api/v1/users",
        "/api/v1/categories",
        "/api/v1/tickets",
        "/api/v1/tickets/stats",
        "/api/v1/tickets/1/comments",
    ],
)
def test_missing_token_cannot_read_business_resources(
    authenticated_api: AuthenticatedApi,
    path: str,
) -> None:
    before = database_snapshot(authenticated_api)
    response = authenticated_api.api.client.get(
        path,
        headers={"X-Request-ID": "missing-token-regression"},
    )

    assert_safe_failure(
        response,
        status=401,
        code="AUTH_TOKEN_INVALID",
        request_id="missing-token-regression",
    )
    assert database_snapshot(authenticated_api) == before
    assert authenticated_api.harness.opener.urls == []


@pytest.mark.parametrize(
    "failure",
    ["network", "unusable-keys", "invalid-json"],
)
def test_key_service_failures_cannot_provision_or_use_fake_identity(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    # Supplied Bearer credentials must never fall back to local fake auth.
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")

    opener = authenticated_api.harness.opener
    private_detail = "private-key-service-detail"

    if failure == "network":
        opener.failure = URLError(private_detail)
    elif failure == "unusable-keys":
        opener.payload = {"keys": []}
    else:
        opener.raw_body = b"{invalid-json"

    token = authenticated_api.token(changes={"sub": "outage-new-subject"})
    before = database_snapshot(authenticated_api)
    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-ID": "key-service-regression",
        },
    )

    assert_safe_failure(
        response,
        status=503,
        code="AUTH_SERVICE_UNAVAILABLE",
        request_id="key-service-regression",
    )
    assert database_snapshot(authenticated_api) == before
    assert private_detail not in response.text
    assert token not in response.text
    assert token not in caplog.text


def test_http_key_cache_expires_safely_and_recovers(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Advance the cache clock without sleeping or changing JWT wall time.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    initial_token = authenticated_api.token(UserRole.REQUESTER)
    initial_headers = {"Authorization": f"Bearer {initial_token}"}
    first = authenticated_api.api.client.get(
        "/api/v1/me",
        headers=initial_headers,
    )
    assert first.status_code == 200
    local_id = first.json()["id"]

    opener = authenticated_api.harness.opener
    assert opener.urls == [security_fixtures.JWKS_URL]
    opener.failure = URLError("synthetic temporary outage")

    # Still-valid cached public keys can verify a token during an outage.
    cached = authenticated_api.api.client.get(
        "/api/v1/me",
        headers=initial_headers,
    )
    assert cached.status_code == 200
    assert cached.json()["id"] == local_id
    assert len(opener.urls) == 1

    clock[0] += 301
    changed_token = authenticated_api.token(changes={"preferred_username": "recovered-user"})
    changed_headers = {
        "Authorization": f"Bearer {changed_token}",
        "X-Request-ID": "cache-expiry-regression",
    }
    before = database_snapshot(authenticated_api)

    unavailable = authenticated_api.api.client.get(
        "/api/v1/me",
        headers=changed_headers,
    )
    assert_safe_failure(
        unavailable,
        status=503,
        code="AUTH_SERVICE_UNAVAILABLE",
        request_id="cache-expiry-regression",
    )
    assert database_snapshot(authenticated_api) == before

    # Once key retrieval recovers, normal verification and sync resume.
    opener.failure = None
    recovered = authenticated_api.api.client.get(
        "/api/v1/me",
        headers=changed_headers,
    )
    assert recovered.status_code == 200
    assert recovered.json()["id"] == local_id
    assert recovered.json()["username"] == "recovered-user"
    assert len(opener.urls) == 3
