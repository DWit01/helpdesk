# Helpdesk

A Python learning project implementing a helpdesk backend with FastAPI,
synchronous SQLAlchemy 2.x, Alembic and Keycloak authentication.

## Current implementation

- Users, categories, tickets and comments.
- Ticket ownership, assignment, lifecycle and soft deletion.
- Public comments and restricted internal notes.
- Keycloak access-token verification and local user provisioning.
- Requester, agent and admin permissions enforced by services.
- Configurable CORS with an empty origin allowlist by default.
- Shared error responses, JSON logging and request IDs.
- Automated tests, coverage reporting and pre-commit checks.

SQLite is used locally. PostgreSQL compatibility remains unverified.
The frontend and MCP server are future work.

## How authentication works in this project

1. A browser login uses Keycloak's Authorization Code flow with PKCE.
2. The user enters their password into Keycloak, never into Helpdesk.
3. The login client exchanges the authorisation code and PKCE verifier
   for tokens.
4. The client sends the access token in the Authorization: Bearer header.
5. Helpdesk verifies its RS256 signature using the configured JWKS endpoint.
6. Helpdesk checks expiry, issuer, audience and required identity claims.
7. The verified subject identifies the corresponding local user.
8. Services enforce role, ownership and ticket lifecycle rules.

An ID token is not the API credential. The API expects an access token
whose audience includes helpdesk-api.

Authentication establishes identity. Authorisation determines what that
identity may do. CORS controls browser access to responses; it does not
replace either check.

## Local users and roles

The Keycloak subject, sub, is the stable identity link. Email addresses
and usernames are not used to link different accounts.

On first authenticated access, Helpdesk creates a local user.
Subsequent authenticated requests synchronise the active user's profile
and highest recognised role:

admin > agent > requester

Unrelated role names do not grant application access. An identity with
no recognised application role is denied.

A valid token never reactivates an inactive local user. An administrator
must explicitly reactivate that account.

Roles come from the presented token's verified claims. An older unexpired
token can still contain earlier roles until it expires or is replaced.

## Configuration

The backend is a separate uv project in backend/.

Settings are read from environment variables. The example file does not
automatically configure a process, and real .env files must remain private.

| Variable | Purpose |
| --- | --- |
| DATABASE_URL | SQLAlchemy database URL |
| APP_ENV | local, development or production |
| LOG_LEVEL | Application logging level |
| KEYCLOAK_ISSUER | Exact trusted realm issuer |
| KEYCLOAK_JWKS_URL | Trusted public signing-key endpoint |
| KEYCLOAK_AUDIENCE | Expected access-token audience |
| CORS_ALLOWED_ORIGINS | JSON list of exact website origins |
| FAKE_AUTH_ENABLED | Explicit local learning override, false by default |
| FAKE_CURRENT_USER_ID | Existing local user for that override |

Fake identity is permitted only in local mode and only when the
Authorization header is absent. Invalid supplied credentials never
fall back to fake identity.

For local Keycloak:

- Issuer: http://127.0.0.1:8080/realms/helpdesk
- JWKS: http://127.0.0.1:8080/realms/helpdesk/protocol/openid-connect/certs
- Audience: helpdesk-api

CORS_ALLOWED_ORIGINS defaults to [].
Origins contain only protocol, hostname and optional port.
Different hostnames or ports are different origins.

Browser cookie credentials are disabled. The current API uses an explicit
Bearer header.

## Reproducing the local Keycloak configuration

Use an organisation-approved local Keycloak distribution.
The development server is for local learning, not production deployment.

1. Create the helpdesk realm.
2. Create realm roles requester, agent and admin.
3. Create an OpenID Connect client named helpdesk-api.
   Disable its login flows, client authentication and Authorization Services.
4. Create an OpenID Connect public client named helpdesk-ui.
   Enable Standard flow, require PKCE and select S256 where the method
   setting is available. Disable other grant flows.
5. For the local diagnostic helper, configure exactly this valid redirect:
   http://127.0.0.1:8765/callback
6. Leave website origins unconfigured until a website origin is chosen.
7. Add an Audience mapper to the UI client's dedicated scope:
   include helpdesk-api in access tokens, not ID tokens.
8. Disable Full scope allowed and explicitly include the three application
   realm roles in the UI client's role scope mappings.
9. Create synthetic users with email addresses and names:
   alice/requester, bob/agent and carol/admin.
10. Set passwords privately. Never put them in this repository.

helpdesk-ui is public because the local browser login helper does not
keep a client secret. PKCE binds the code exchange to the initiating
login attempt.

The callback above belongs to the diagnostic helper. It is not a future
website configuration.

## Export and restore

Local realm export, offline import and startup import have been
demonstrated using separate disposable Keycloak databases.

Stop Keycloak before offline export or import.
Select the intended database explicitly.
Startup import uses data/import and skips an existing realm; it is not
an update mechanism for an existing realm.

Raw exports can contain credential hashes and private key material.
Keep them outside this repository and do not treat them as shareable
configuration. The setup recipe above is the non-secret configuration
reference.

Docker Desktop is blocked by company controls. Docker-compose
automation remains deferred. Technical operation does not establish
organisation-policy approval.

## API behaviour

Business routes use the /api/v1 prefix and require authentication.

- 401: missing or invalid credentials; includes WWW-Authenticate: Bearer.
- 403: inactive user or insufficient application permissions.
- 404: absent resource or a ticket hidden from the requester.
- 409: business-rule conflict.
- 422: invalid request data.
- 503: authentication key service unavailable or database readiness failure.

Authentication-service and database-readiness failures have distinct
error codes.

The root greeting, health/readiness probes and API documentation are
public. /health checks the process; /ready checks database connectivity.

Unexpected errors return a safe envelope and request ID.
Raw tokens, passwords and private exception details must not be exposed.

## Architecture and verification

Routes call services. Services own business rules and transactions.
Repositories own queries. ORM models describe persistence.
Future MCP tools must reuse the same services and security rules.

Tests use isolated databases and generated RSA tokens with mocked
signing-key transport. Separate local checks have also used real Keycloak
tokens.

Latest verified automated checkpoint, 30 October 2026:

- 847 passed, 3 skipped.
- 98.83% application line coverage.
- Aggregate line-coverage gate: 70%.
- Pre-commit Ruff, Black and mypy checks passed.

Branch coverage, PostgreSQL compatibility and current live CI results
are not established by these local results.

Outstanding issues include the Starlette/httpx deprecation,
pytest-cache WinError 183 and previously observed Git directory-deletion
failures.

The live Week 4 demonstration passed on 30 October 2026 using real
Keycloak tokens for Alice, Bob and Carol against the existing disposable
Task 6 SQLite database. It verified representative requester, agent and
admin workflows, ownership restrictions and internal-note visibility.
The wider permission matrix is covered by parametrised automated tests.