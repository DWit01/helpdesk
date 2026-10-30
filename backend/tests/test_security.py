import base64
import json
import time
from dataclasses import FrozenInstanceError, dataclass
from io import BytesIO
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.api_jws import PyJWS

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationRequired,
    AuthenticationUnavailable,
)
from app.core.security import TokenIdentity, TokenVerifier

ISSUER = "https://identity.example.test/realms/helpdesk"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
AUDIENCE = "helpdesk-api"


def encode_integer(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": encode_integer(numbers.n),
        "e": encode_integer(numbers.e),
    }


def default_claims() -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice-subject",
        "preferred_username": "alice",
        "email": "alice@example.test",
        "name": "Alice Requester",
        "realm_access": {"roles": ["requester"]},
        "iat": now - 10,
        "nbf": now - 10,
        "exp": now + 600,
    }


@dataclass(frozen=True)
class Keys:
    first: rsa.RSAPrivateKey
    second: rsa.RSAPrivateKey


@pytest.fixture(scope="session")
def keys() -> Keys:
    return Keys(
        first=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        second=rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


class FakeOpener:
    """Replace HTTP only; PyJWT still parses and verifies real RSA tokens."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.raw_body: bytes | None = None
        self.failure: Exception | None = None
        self.urls: list[str] = []
        self.timeouts: list[float] = []

    def open(self, request: Request, timeout: float) -> BytesIO:
        self.urls.append(request.full_url)
        self.timeouts.append(timeout)

        if self.failure is not None:
            raise self.failure

        body = self.raw_body
        if body is None:
            body = json.dumps(self.payload).encode("utf-8")

        return BytesIO(body)


@dataclass
class Harness:
    verifier: TokenVerifier
    opener: FakeOpener
    keys: Keys

    def token(
        self,
        changes: dict[str, Any] | None = None,
        *,
        remove: str | None = None,
        kid: str = "first",
        key: rsa.RSAPrivateKey | None = None,
        headers: dict[str, Any] | None = None,
    ) -> str:
        claims = default_claims()
        if changes is not None:
            claims.update(changes)
        if remove is not None:
            claims.pop(remove, None)

        token_headers: dict[str, Any] = {"kid": kid}
        if headers is not None:
            token_headers.update(headers)

        # Sign raw JSON so deliberately malformed claims reach the verifier.
        # jwt.encode() rejects non-string issuers before a test can verify them.
        return PyJWS().encode(
            json.dumps(claims, separators=(",", ":")).encode("utf-8"),
            key if key is not None else self.keys.first,
            algorithm="RS256",
            headers=token_headers,
        )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, keys: Keys) -> Harness:
    opener = FakeOpener({"keys": [public_jwk(keys.first, "first")]})

    def build_fake_opener(*_handlers: object) -> FakeOpener:
        return opener

    monkeypatch.setattr("urllib.request.build_opener", build_fake_opener)

    verifier = TokenVerifier(
        issuer=ISSUER,
        jwks_url=JWKS_URL,
        audience=AUDIENCE,
    )
    return Harness(verifier, opener, keys)


def assert_invalid(verifier: TokenVerifier, token: str) -> None:
    with pytest.raises(AuthenticationRequired) as error:
        verifier.verify(token)

    assert error.value.status_code == 401
    assert error.value.code == "AUTH_TOKEN_INVALID"
    assert str(error.value) == "Invalid authentication token."


@pytest.mark.parametrize("audience", [AUDIENCE, [AUDIENCE, "another-api"]])
def test_valid_rsa_token_returns_identity(
    harness: Harness,
    audience: str | list[str],
) -> None:
    identity = harness.verifier.verify(harness.token({"aud": audience}))

    assert identity == TokenIdentity(
        subject="alice-subject",
        username="alice",
        email="alice@example.test",
        roles=frozenset({"requester"}),
        full_name="Alice Requester",
    )
    assert harness.opener.urls == [JWKS_URL]
    assert harness.opener.timeouts == [5]


def test_identity_is_immutable(harness: Harness) -> None:
    identity = harness.verifier.verify(harness.token())

    with pytest.raises(FrozenInstanceError):
        setattr(identity, "username", "mallory")

    assert isinstance(identity.roles, frozenset)


@pytest.mark.parametrize("token", ["", " ", "\t\n", "not-a-jwt"])
def test_invalid_input_does_not_fetch_keys(
    harness: Harness,
    token: str,
) -> None:
    assert_invalid(harness.verifier, token)
    assert harness.opener.urls == []


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
def test_disallowed_algorithm_does_not_fetch_keys(
    harness: Harness,
    algorithm: str,
) -> None:
    signing_secret = "synthetic-test-secret-with-at-least-thirty-two-bytes"
    token = jwt.encode(
        default_claims(),
        signing_secret if algorithm == "HS256" else "",
        algorithm=algorithm,
        headers={"kid": "first"},
    )

    assert_invalid(harness.verifier, token)
    assert harness.opener.urls == []


def test_missing_kid_does_not_fetch_keys(harness: Harness) -> None:
    token = jwt.encode(
        default_claims(),
        harness.keys.first,
        algorithm="RS256",
    )

    assert_invalid(harness.verifier, token)
    assert harness.opener.urls == []


def test_wrong_signature_is_rejected(harness: Harness) -> None:
    token = harness.token(key=harness.keys.second)
    assert_invalid(harness.verifier, token)


def test_token_supplied_key_url_is_ignored(harness: Harness) -> None:
    token = harness.token(headers={"jku": "https://attacker.example.test/keys"})
    harness.verifier.verify(token)

    assert harness.opener.urls == [JWKS_URL]


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": int(time.time()) - 60},
        {"iss": "https://attacker.example.test/realms/helpdesk"},
        {"iss": ISSUER + "/"},
        {"iss": 123},
        {"aud": "payroll-api"},
        {"aud": ["payroll-api"]},
        {"aud": []},
        {"aud": [AUDIENCE, 123]},
        {"iat": int(time.time()) + 3600},
        {"nbf": int(time.time()) + 3600},
    ],
)
def test_invalid_standard_claims_are_rejected(
    harness: Harness,
    changes: dict[str, Any],
) -> None:
    assert_invalid(harness.verifier, harness.token(changes))


@pytest.mark.parametrize(
    "claim",
    ["exp", "iss", "aud", "sub", "preferred_username", "email"],
)
def test_required_claims_cannot_be_omitted(
    harness: Harness,
    claim: str,
) -> None:
    assert_invalid(harness.verifier, harness.token(remove=claim))


@pytest.mark.parametrize("claim", ["sub", "preferred_username", "email"])
@pytest.mark.parametrize("value", [None, "", "   ", 123, [], {}])
def test_required_text_claims_are_validated(
    harness: Harness,
    claim: str,
    value: object,
) -> None:
    assert_invalid(harness.verifier, harness.token({claim: value}))


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", [None, True, [], {}, "123"])
def test_numeric_dates_reject_invalid_types(
    harness: Harness,
    claim: str,
    value: object,
) -> None:
    assert_invalid(harness.verifier, harness.token({claim: value}))


def test_numeric_string_expiry_is_rejected(harness: Harness) -> None:
    # It is numerically valid and in the future, but has the wrong JSON type.
    token = harness.token({"exp": str(int(time.time()) + 600)})
    assert_invalid(harness.verifier, token)


def test_fractional_numeric_date_is_allowed(harness: Harness) -> None:
    identity = harness.verifier.verify(harness.token({"exp": time.time() + 600.5}))
    assert identity.username == "alice"


@pytest.mark.parametrize(
    "realm_access",
    [
        None,
        [],
        "admin",
        {"roles": None},
        {"roles": "admin"},
        {"roles": [123]},
        {"roles": [""]},
        {"roles": ["   "]},
        {"roles": ["requester", False]},
    ],
)
def test_malformed_roles_are_rejected(
    harness: Harness,
    realm_access: object,
) -> None:
    assert_invalid(
        harness.verifier,
        harness.token({"realm_access": realm_access}),
    )


@pytest.mark.parametrize(
    "realm_access",
    [{}, {"roles": []}],
)
def test_empty_roles_do_not_gain_default_permissions(
    harness: Harness,
    realm_access: dict[str, Any],
) -> None:
    identity = harness.verifier.verify(harness.token({"realm_access": realm_access}))
    assert identity.roles == frozenset()


def test_absent_roles_do_not_gain_default_permissions(harness: Harness) -> None:
    identity = harness.verifier.verify(harness.token(remove="realm_access"))
    assert identity.roles == frozenset()


def test_roles_are_deduplicated_without_normalisation(harness: Harness) -> None:
    identity = harness.verifier.verify(harness.token({"realm_access": {"roles": ["requester", "requester", "ADMIN"]}}))
    assert identity.roles == frozenset({"requester", "ADMIN"})
    assert "admin" not in identity.roles


@pytest.mark.parametrize("name", [123, [], {}])
def test_optional_name_rejects_wrong_types(
    harness: Harness,
    name: object,
) -> None:
    assert_invalid(harness.verifier, harness.token({"name": name}))


def test_optional_name_can_be_absent(harness: Harness) -> None:
    identity = harness.verifier.verify(harness.token(remove="name"))
    assert identity.full_name is None


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"keys": []}, {"keys": "invalid"}, {"keys": [{}]}],
)
def test_unusable_jwks_returns_service_unavailable(
    harness: Harness,
    payload: object,
) -> None:
    harness.opener.payload = payload

    with pytest.raises(AuthenticationUnavailable) as error:
        harness.verifier.verify(harness.token())

    assert error.value.status_code == 503
    assert error.value.code == "AUTH_SERVICE_UNAVAILABLE"


@pytest.mark.parametrize("body", [b"{", b"\xff"])
def test_invalid_jwks_encoding_returns_service_unavailable(
    harness: Harness,
    body: bytes,
) -> None:
    harness.opener.raw_body = body

    with pytest.raises(AuthenticationUnavailable):
        harness.verifier.verify(harness.token())


@pytest.mark.parametrize(
    "failure",
    [URLError("synthetic connection failure"), TimeoutError("synthetic timeout")],
)
def test_jwks_outage_returns_safe_service_error(
    harness: Harness,
    failure: Exception,
) -> None:
    harness.opener.failure = failure

    with pytest.raises(AuthenticationUnavailable) as error:
        harness.verifier.verify(harness.token())

    assert error.value.status_code == 503
    assert str(error.value) == "Authentication is temporarily unavailable."


def test_unknown_key_id_is_invalid_token(harness: Harness) -> None:
    assert_invalid(harness.verifier, harness.token(kid="unknown"))


def test_cached_keys_are_reused_during_short_outage(harness: Harness) -> None:
    token = harness.token()
    harness.verifier.verify(token)
    harness.opener.failure = URLError("synthetic outage")

    identity = harness.verifier.verify(token)

    assert identity.username == "alice"
    assert harness.opener.urls == [JWKS_URL]


def test_expired_cache_fails_closed_then_recovers(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    token = harness.token()
    harness.verifier.verify(token)

    clock[0] += 301
    harness.opener.failure = URLError("synthetic outage")

    with pytest.raises(AuthenticationUnavailable):
        harness.verifier.verify(token)

    harness.opener.failure = None
    assert harness.verifier.verify(token).username == "alice"
    assert len(harness.opener.urls) == 3


def test_rotated_key_is_refreshed_after_cooldown(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    harness.verifier.verify(harness.token())
    harness.opener.payload = {
        "keys": [
            public_jwk(harness.keys.first, "first"),
            public_jwk(harness.keys.second, "second"),
        ]
    }
    rotated_token = harness.token(kid="second", key=harness.keys.second)

    # PyJWT limits forced refreshes after an unknown key ID.
    assert_invalid(harness.verifier, rotated_token)
    assert len(harness.opener.urls) == 1

    clock[0] += 31

    identity = harness.verifier.verify(rotated_token)
    assert identity.username == "alice"
    assert len(harness.opener.urls) == 2


def test_unrelated_programming_errors_are_not_hidden(harness: Harness) -> None:
    harness.opener.failure = RuntimeError("synthetic programming error")

    with pytest.raises(RuntimeError, match="synthetic programming error"):
        harness.verifier.verify(harness.token())


@pytest.mark.parametrize(
    ("setting_name", "value"),
    [
        ("issuer", ""),
        ("issuer", "   "),
        ("issuer", "not-a-url"),
        ("issuer", ISSUER + "?unexpected=1"),
        ("jwks_url", ""),
        ("jwks_url", "file:///private/keys"),
        ("jwks_url", "https://user:password@example.test/keys"),
        ("jwks_url", "http://remote.example.test/keys"),
        ("audience", ""),
        ("audience", "   "),
        ("audience", " helpdesk-api "),
    ],
)
def test_invalid_configuration_is_rejected(
    setting_name: str,
    value: str,
) -> None:
    arguments = {
        "issuer": ISSUER,
        "jwks_url": JWKS_URL,
        "audience": AUDIENCE,
    }
    arguments[setting_name] = value

    with pytest.raises(ValueError):
        TokenVerifier(**arguments)


@pytest.mark.parametrize("missing", ["keycloak_issuer", "keycloak_jwks_url"])
def test_settings_factory_rejects_missing_trust_configuration(
    missing: str,
) -> None:
    values: dict[str, Any] = {
        "database_url": "sqlite:///:memory:",
        "keycloak_issuer": ISSUER,
        "keycloak_jwks_url": JWKS_URL,
        "keycloak_audience": AUDIENCE,
    }
    values[missing] = None
    settings = Settings(**values)

    with pytest.raises(ValueError):
        TokenVerifier.from_settings(settings)


def test_settings_factory_builds_working_verifier(harness: Harness) -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        keycloak_issuer=ISSUER,
        keycloak_jwks_url=JWKS_URL,
        keycloak_audience=AUDIENCE,
    )

    verifier = TokenVerifier.from_settings(settings)

    assert verifier.verify(harness.token()).username == "alice"
