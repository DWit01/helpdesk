from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_handlers import register_exception_handlers
from app.db.session import get_db
from app.dependencies import _cached_token_verifier, require_roles
from app.models import User, UserRole
from tests import test_api as api_fixtures
from tests import test_security as security_fixtures

# Register existing decorated fixtures without redefining imported names.
api = api_fixtures.api
keys = security_fixtures.keys
harness = security_fixtures.harness


@dataclass
class AuthenticatedApi:
    api: api_fixtures.Api
    harness: security_fixtures.Harness

    def token(
        self,
        role: UserRole = UserRole.REQUESTER,
        changes: dict[str, Any] | None = None,
    ) -> str:
        claims: dict[str, Any] = {
            "sub": f"api-test-{role.value}",
            "realm_access": {"roles": [role.value]},
        }

        if changes is not None:
            claims.update(changes)

        return self.harness.token(claims)


@pytest.fixture
def authenticated_api(
    api: api_fixtures.Api,
    harness: security_fixtures.Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[AuthenticatedApi, None, None]:
    _cached_token_verifier.cache_clear()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    monkeypatch.setenv("KEYCLOAK_ISSUER", security_fixtures.ISSUER)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", security_fixtures.JWKS_URL)
    monkeypatch.setenv("KEYCLOAK_AUDIENCE", security_fixtures.AUDIENCE)

    try:
        yield AuthenticatedApi(api, harness)
    finally:
        _cached_token_verifier.cache_clear()


@pytest.mark.parametrize("role", list(UserRole))
def test_bearer_token_resolves_existing_local_user(
    authenticated_api: AuthenticatedApi,
    role: UserRole,
) -> None:
    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authenticated_api.token(role)}"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(authenticated_api.api.users[role])
    assert response.json()["role"] == role.value
    assert "keycloak_sub" not in response.json()


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic credentials", "Bearer", "Bearer invalid", "Bearer a b"],
)
def test_missing_or_invalid_credentials_are_rejected(
    authenticated_api: AuthenticatedApi,
    authorization: str | None,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    response = authenticated_api.api.client.get("/api/v1/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_duplicate_authorization_headers_are_rejected(
    authenticated_api: AuthenticatedApi,
) -> None:
    token = authenticated_api.token()

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers=[
            ("Authorization", f"Bearer {token}"),
            ("Authorization", "Bearer invalid"),
        ],
    )

    assert response.status_code == 401


def test_unknown_subject_is_provisioned_once(
    authenticated_api: AuthenticatedApi,
) -> None:
    with authenticated_api.api.sessions() as db:
        before = db.scalar(select(func.count()).select_from(User))

    token = authenticated_api.token(changes={"sub": "new-subject"})
    headers = {"Authorization": f"Bearer {token}"}

    first = authenticated_api.api.client.get("/api/v1/me", headers=headers)
    second = authenticated_api.api.client.get("/api/v1/me", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["role"] == "requester"
    assert "keycloak_sub" not in first.json()

    with authenticated_api.api.sessions() as db:
        after = db.scalar(select(func.count()).select_from(User))
        user = db.scalar(select(User).where(User.keycloak_sub == "new-subject"))

        assert user is not None
        assert str(user.id) == first.json()["id"]
        assert user.username == "alice"

    assert before is not None
    assert after == before + 1


def test_inactive_user_is_not_reactivated(
    authenticated_api: AuthenticatedApi,
) -> None:
    user_id = authenticated_api.api.users[UserRole.REQUESTER]

    with authenticated_api.api.sessions() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.is_active = False
        db.commit()

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authenticated_api.token()}"},
    )

    assert response.status_code == 403

    with authenticated_api.api.sessions() as db:
        user = db.get(User, user_id)
        assert user is not None
        assert user.is_active is False


@pytest.mark.parametrize("roles", [[], ["unrelated-role"]])
def test_missing_application_role_is_denied_without_updating_user(
    authenticated_api: AuthenticatedApi,
    roles: list[str],
) -> None:
    token = authenticated_api.token(changes={"realm_access": {"roles": roles}})

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403

    with authenticated_api.api.sessions() as db:
        user = db.get(
            User,
            authenticated_api.api.users[UserRole.REQUESTER],
        )
        assert user is not None
        assert user.role == UserRole.REQUESTER


def test_invalid_bearer_never_falls_back_to_fake_identity(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")

    # No Authorization header: explicitly enabled local fake mode works.
    local_response = authenticated_api.api.client.get("/api/v1/me")
    assert local_response.status_code == 200

    # Supplied invalid credentials must not become that fake user.
    invalid_response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer invalid"},
    )
    assert invalid_response.status_code == 401

    basic_response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": "Basic credentials"},
    )
    assert basic_response.status_code == 401


def test_valid_bearer_takes_precedence_over_configured_fake_user(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": (f"Bearer {authenticated_api.token(UserRole.AGENT)}")},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(authenticated_api.api.users[UserRole.AGENT])


@pytest.mark.parametrize("environment", ["development", "production"])
def test_fake_identity_remains_local_only(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")

    response = authenticated_api.api.client.get("/api/v1/me")
    assert response.status_code == 401


def test_verifier_and_key_cache_are_reused(
    authenticated_api: AuthenticatedApi,
) -> None:
    headers = {"Authorization": f"Bearer {authenticated_api.token()}"}

    for _ in range(2):
        response = authenticated_api.api.client.get(
            "/api/v1/me",
            headers=headers,
        )
        assert response.status_code == 200

    assert authenticated_api.harness.opener.urls == [security_fixtures.JWKS_URL]
    assert _cached_token_verifier.cache_info().hits >= 1


def test_missing_trust_configuration_returns_safe_503(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KEYCLOAK_ISSUER")

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authenticated_api.token()}"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_SERVICE_UNAVAILABLE"
    assert "WWW-Authenticate" not in response.headers


def test_signing_key_outage_returns_safe_503(
    authenticated_api: AuthenticatedApi,
) -> None:
    authenticated_api.harness.opener.failure = URLError("synthetic private connection detail")

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authenticated_api.token()}"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_SERVICE_UNAVAILABLE"
    assert "synthetic private connection detail" not in response.text


def test_authentication_errors_preserve_request_id(
    authenticated_api: AuthenticatedApi,
) -> None:
    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={
            "Authorization": "Bearer invalid",
            "X-Request-ID": "authentication-test",
        },
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "authentication-test"
    assert response.json()["error"]["request_id"] == "authentication-test"


def test_public_probes_do_not_verify_bearer_tokens(
    authenticated_api: AuthenticatedApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_verifier(*args: object, **kwargs: object) -> None:
        raise AssertionError("Public probes must not construct a verifier.")

    monkeypatch.setattr(
        "app.dependencies._cached_token_verifier",
        unexpected_verifier,
    )

    for path in ("/health", "/ready"):
        response = authenticated_api.api.client.get(
            path,
            headers={"Authorization": "Bearer invalid"},
        )
        assert response.status_code == 200


@pytest.fixture
def role_client(
    authenticated_api: AuthenticatedApi,
) -> Generator[TestClient, None, None]:
    test_app = FastAPI()
    register_exception_handlers(test_app)

    def override_db() -> Generator[Session, None, None]:
        with authenticated_api.api.sessions() as db:
            yield db

    test_app.dependency_overrides[get_db] = override_db

    @test_app.get(
        "/staff",
        dependencies=[Depends(require_roles(UserRole.AGENT, UserRole.ADMIN))],
    )
    def staff_endpoint() -> dict[str, str]:
        return {"status": "allowed"}

    with TestClient(test_app) as client:
        yield client


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        (UserRole.REQUESTER, 403),
        (UserRole.AGENT, 200),
        (UserRole.ADMIN, 200),
    ],
)
def test_role_dependency_enforces_allowed_roles_through_http(
    authenticated_api: AuthenticatedApi,
    role_client: TestClient,
    role: UserRole,
    expected_status: int,
) -> None:
    response = role_client.get(
        "/staff",
        headers={"Authorization": f"Bearer {authenticated_api.token(role)}"},
    )

    assert response.status_code == expected_status


def test_role_dependency_requires_an_explicit_policy() -> None:
    with pytest.raises(ValueError):
        require_roles()


def test_verified_role_and_profile_changes_are_synchronised(
    authenticated_api: AuthenticatedApi,
) -> None:
    local_id = authenticated_api.api.users[UserRole.REQUESTER]

    promoted_token = authenticated_api.token(
        changes={
            "preferred_username": "alice-updated",
            "email": "updated@example.test",
            "name": "Alice Updated",
            "realm_access": {"roles": ["requester", "agent", "admin"]},
        }
    )
    promoted = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {promoted_token}"},
    )

    assert promoted.status_code == 200
    assert promoted.json()["id"] == str(local_id)
    assert promoted.json()["role"] == "admin"
    assert promoted.json()["username"] == "alice-updated"

    # A later token containing only requester also removes stored admin access.
    demoted = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {authenticated_api.token()}"},
    )

    assert demoted.status_code == 200
    assert demoted.json()["id"] == str(local_id)
    assert demoted.json()["role"] == "requester"


def test_invalid_token_cannot_provision_a_user(
    authenticated_api: AuthenticatedApi,
) -> None:
    with authenticated_api.api.sessions() as db:
        before = db.scalar(select(func.count()).select_from(User))

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer invalid"},
    )

    assert response.status_code == 401

    with authenticated_api.api.sessions() as db:
        after = db.scalar(select(func.count()).select_from(User))

    assert after == before


def test_overlong_verified_profile_does_not_provision(
    authenticated_api: AuthenticatedApi,
) -> None:
    token = authenticated_api.token(
        changes={
            "sub": "invalid-profile-subject",
            "preferred_username": "x" * 51,
        }
    )

    response = authenticated_api.api.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401

    with authenticated_api.api.sessions() as db:
        user = db.scalar(select(User).where(User.keycloak_sub == "invalid-profile-subject"))

    assert user is None
