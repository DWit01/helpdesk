from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    AccessDenied,
    AuthenticationRequired,
    AuthenticationUnavailable,
)
from app.core.security import TokenVerifier
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.services.identity_service import IdentityService

bearer_scheme = HTTPBearer(
    scheme_name="KeycloakBearer",
    bearerFormat="JWT",
    description="A Keycloak access token intended for helpdesk-api.",
    auto_error=False,
)

DbSession = Annotated[Session, Depends(get_db)]
AuthorizationHeaders = Annotated[
    list[str] | None,
    Header(alias="Authorization", include_in_schema=False),
]
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


@lru_cache(maxsize=16)
def _cached_token_verifier(
    issuer: str | None,
    jwks_url: str | None,
    audience: str,
) -> TokenVerifier:
    """Reuse a verifier and its key cache for each configuration."""
    if issuer is None or jwks_url is None:
        raise ValueError("Keycloak trust configuration is missing.")

    return TokenVerifier(
        issuer=issuer,
        jwks_url=jwks_url,
        audience=audience,
    )


def _local_fake_user(db: Session, settings: Settings) -> User:
    """Allow explicitly enabled fake identity only in local mode."""
    if settings.app_env != "local" or not settings.fake_auth_enabled:
        raise AuthenticationRequired()

    if settings.fake_current_user_id is None:
        raise AuthenticationRequired()

    user = UserRepository(db).get_by_id(settings.fake_current_user_id)

    if user is None:
        raise AuthenticationRequired()

    if not user.is_active:
        raise AccessDenied()

    return user


def get_current_user(
    db: DbSession,
    authorization: AuthorizationHeaders = None,
    credentials: BearerCredentials = None,
) -> User:
    """Resolve verified credentials to an active provisioned local user."""
    settings = Settings()

    # Only absence of the header can enter local fake mode.
    # Malformed or invalid supplied credentials must never fall back.
    if authorization is None:
        return _local_fake_user(db, settings)

    if len(authorization) != 1 or credentials is None:
        raise AuthenticationRequired()

    token = credentials.credentials

    if credentials.scheme.lower() != "bearer" or not token or any(character.isspace() for character in token):
        raise AuthenticationRequired()

    try:
        verifier = _cached_token_verifier(
            settings.keycloak_issuer,
            settings.keycloak_jwks_url,
            settings.keycloak_audience,
        )
    except ValueError:
        # Bad server configuration must not be reported as a bad user token.
        raise AuthenticationUnavailable() from None

    identity = verifier.verify(token)
    return IdentityService(db).resolve(identity)


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    """Build a dependency allowing only explicitly listed stored roles."""
    allowed_roles = frozenset(roles)

    if not allowed_roles:
        raise ValueError("At least one permitted role is required.")

    def check_roles(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if user.role not in allowed_roles:
            raise AccessDenied()

        return user

    return check_roles
