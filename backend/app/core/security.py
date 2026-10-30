import math
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK, PyJWKClient

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationRequired,
    AuthenticationUnavailable,
)


@dataclass(frozen=True)
class TokenIdentity:
    """Validated identity data, without database or permission decisions."""

    subject: str
    username: str
    email: str
    roles: frozenset[str]
    full_name: str | None = None


class _SigningKeyClient(PyJWKClient):
    """Separate signing-key service failures from unknown token key IDs."""

    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]:
        try:
            return super().get_signing_keys(refresh=refresh)
        except jwt.PyJWTError, ValueError:
            # Includes network failures, malformed JSON and unusable key sets.
            raise AuthenticationUnavailable() from None


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        jwks_url: str,
        audience: str,
    ) -> None:
        self._validate_url(issuer)
        self._validate_url(jwks_url)

        if not audience.strip() or audience != audience.strip():
            raise ValueError("The authentication audience must not be blank or padded.")

        self._issuer = issuer
        self._audience = audience

        # Reuse this verifier instance to retain the key-set cache.
        # Per-key caching stays disabled, avoiding an indefinite key cache.
        self._jwks_client = _SigningKeyClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        issuer = settings.keycloak_issuer
        jwks_url = settings.keycloak_jwks_url

        if issuer is None or jwks_url is None:
            raise ValueError("Keycloak issuer and JWKS URL must be configured.")

        return cls(
            issuer=issuer,
            jwks_url=jwks_url,
            audience=settings.keycloak_audience,
        )

    @staticmethod
    def _validate_url(value: str) -> None:
        """Permit HTTPS, plus HTTP for local loopback development."""
        if not value.strip() or value != value.strip():
            raise ValueError("Authentication URLs must not be blank or padded.")

        try:
            parsed = urlsplit(value)
            # Accessing port also validates its syntax.
            _ = parsed.port

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError

            if parsed.scheme == "http" and parsed.hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ValueError
        except ValueError:
            raise ValueError(
                "Authentication URLs require HTTPS or local loopback HTTP, "
                "without credentials, query parameters or fragments."
            ) from None

    def verify(self, token: str) -> TokenIdentity:
        """Return identity data only after verification and claim validation."""
        if not token.strip():
            raise AuthenticationRequired("Invalid authentication token.")

        claims = self._decode_token(token)
        self._validate_numeric_dates(claims)

        subject = self._required_text(claims, "sub")
        username = self._required_text(claims, "preferred_username")
        email = self._required_text(claims, "email")
        roles = self._validated_roles(claims)

        full_name = claims.get("name")
        if full_name is not None and not isinstance(full_name, str):
            raise AuthenticationRequired("Invalid authentication token.")

        return TokenIdentity(
            subject=subject,
            username=username,
            email=email,
            roles=roles,
            full_name=full_name,
        )

    def _decode_token(self, token: str) -> dict[str, Any]:
        try:
            # Inspect untrusted headers only to reject unsupported input.
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")

            if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid.strip():
                raise AuthenticationRequired("Invalid authentication token.")

            # Always use the configured endpoint, never a token-supplied URL.
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            return jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": [
                        "exp",
                        "iss",
                        "aud",
                        "sub",
                        "preferred_username",
                        "email",
                    ],
                },
            )
        except jwt.InvalidKeyError:
            raise AuthenticationUnavailable() from None
        except jwt.InvalidTokenError, jwt.PyJWKClientError:
            raise AuthenticationRequired("Invalid authentication token.") from None

    @staticmethod
    def _validate_numeric_dates(claims: dict[str, Any]) -> None:
        """Reject coerced strings, booleans and non-finite NumericDate values."""
        for name in ("exp", "iat", "nbf"):
            if name not in claims:
                continue

            value = claims[name]

            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AuthenticationRequired("Invalid authentication token.")

            if isinstance(value, float) and not math.isfinite(value):
                raise AuthenticationRequired("Invalid authentication token.")

    @staticmethod
    def _required_text(claims: dict[str, Any], name: str) -> str:
        value = claims.get(name)

        if not isinstance(value, str) or not value.strip():
            raise AuthenticationRequired("Invalid authentication token.")

        return value

    @staticmethod
    def _validated_roles(claims: dict[str, Any]) -> frozenset[str]:
        realm_access = claims.get("realm_access", {})

        if not isinstance(realm_access, dict):
            raise AuthenticationRequired("Invalid authentication token.")

        roles = realm_access.get("roles", [])

        if not isinstance(roles, list):
            raise AuthenticationRequired("Invalid authentication token.")

        validated_roles: set[str] = set()

        for role in roles:
            if not isinstance(role, str) or not role.strip():
                raise AuthenticationRequired("Invalid authentication token.")

            validated_roles.add(role)

        return frozenset(validated_roles)
