import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from uuid import UUID

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, ListToolsResult, TextContent
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import TokenVerifier
from app.db.base import Base
from app.db.session import build_engine
from app.mcp.config import MCPSettings
from app.mcp.server import create_mcp_app
from app.models import Category, Comment, Ticket, User, UserRole
from app.schemas.ticket import TicketAssign, TicketCreate, TicketRead
from app.services.ticket_service import TicketService
from tests import test_security as security_fixtures

keys = security_fixtures.keys
harness = security_fixtures.harness
RESOURCE = "http://127.0.0.1:8001/mcp"
TOOL_NAMES = {
    "search_tickets",
    "get_ticket",
    "create_ticket",
    "add_comment",
    "update_ticket_status",
    "list_categories",
}


@dataclass
class McpWorld:
    sessions: sessionmaker[Session]
    settings: MCPSettings
    harness: security_fixtures.Harness
    users: dict[UserRole, UUID]
    category_id: int

    def token(self, role: UserRole = UserRole.REQUESTER, **changes: Any) -> str:
        claims: dict[str, Any] = {
            "aud": RESOURCE,
            "sub": f"mcp-{role.value}",
            "preferred_username": f"mcp-{role.value}",
            "email": f"{role.value}@example.invalid",
            "realm_access": {"roles": [role.value]},
        }
        claims.update(changes)
        return self.harness.token(claims)

    def dispatch[T](self, token: str, operation: Callable[[Client], Awaitable[T]]) -> T:
        async def check() -> T:
            app = create_mcp_app(self.settings, self.sessions)
            async with asyncio.timeout(20):
                async with app.router.lifespan_context(app):
                    async with httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        headers={"Authorization": f"Bearer {token}"},
                        trust_env=False,
                    ) as http:
                        transport = streamable_http_client(RESOURCE, http_client=http)
                        async with Client(transport, mode="2026-07-28") as client:
                            return await operation(client)

        return asyncio.run(check())

    def call(self, name: str, arguments: dict[str, Any], token: str | None = None) -> CallToolResult:
        async def invoke(client: Client) -> CallToolResult:
            result = await client.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            return result

        return self.dispatch(token if token is not None else self.token(), invoke)

    def discover(self) -> ListToolsResult:
        async def invoke(client: Client) -> ListToolsResult:
            return await client.list_tools()

        return self.dispatch(self.token(), invoke)

    def rejected_request(self, headers: list[tuple[str, str]], url: str = RESOURCE) -> httpx2.Response:
        async def check() -> httpx2.Response:
            app = create_mcp_app(self.settings, self.sessions)
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    trust_env=False,
                ) as http:
                    return await http.post(
                        url,
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    )

        return asyncio.run(check())

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self.sessions() as db:
            return {
                table.name: [dict(row) for row in db.execute(select(table)).mappings()]
                for table in Base.metadata.sorted_tables
            }

    def counts(self) -> tuple[int, ...]:
        with self.sessions() as db:
            return tuple(
                int(db.scalar(select(func.count()).select_from(model)) or 0)
                for model in (User, Category, Ticket, Comment)
            )


@pytest.fixture
def mcp_world(
    tmp_path: Path,
    harness: security_fixtures.Harness,
) -> Generator[McpWorld, None, None]:
    url = f"sqlite+pysqlite:///{(tmp_path / 'mcp-tests.sqlite3').as_posix()}"
    engine = build_engine(url)
    sessions = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with sessions() as db:
        users = {
            role: User(
                keycloak_sub=f"mcp-{role.value}",
                username=f"mcp-{role.value}",
                email=f"{role.value}@example.invalid",
                role=role,
                is_active=True,
            )
            for role in UserRole
        }
        category = Category(name="Hardware", is_active=True)
        db.add_all([*users.values(), category])
        db.commit()
        user_ids = {role: user.id for role, user in users.items()}
        category_id = category.id
    settings = MCPSettings(
        database_url=url,
        keycloak_issuer=security_fixtures.ISSUER,
        keycloak_jwks_url=security_fixtures.JWKS_URL,
        mcp_resource_url=AnyHttpUrl(RESOURCE),
    )
    try:
        yield McpWorld(sessions, settings, harness, user_ids, category_id)
    finally:
        engine.dispose()


def data(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is not True, result
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def error(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is True
    first = result.content[0]
    assert isinstance(first, TextContent)
    decoded = json.loads(first.text)
    assert isinstance(decoded, dict)
    return decoded


def create(mcp_world: McpWorld, token: str | None = None) -> dict[str, Any]:
    return data(
        mcp_world.call(
            "create_ticket",
            {
                "title": "Broken keyboard",
                "description": "The keyboard does not respond to any key presses.",
                "category_id": mcp_world.category_id,
            },
            token,
        )
    )


def test_authenticated_http_discovery_contracts(mcp_world: McpWorld) -> None:
    tools = mcp_world.discover().tools
    assert {tool.name for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.description
        assert tool.input_schema["additionalProperties"] is False
        properties = tool.input_schema.get("properties", {})
        assert not {"ctx", "service", "actor_id", "requester_id", "role"} & properties.keys()
        assert all(value.get("description") for value in properties.values())
        assert tool.annotations is not None
    creation = next(tool for tool in tools if tool.name == "create_ticket")
    assert creation.input_schema["properties"]["title"]["maxLength"] == 120


def test_protected_metadata_is_public(mcp_world: McpWorld) -> None:
    async def check() -> None:
        app = create_mcp_app(mcp_world.settings, mcp_world.sessions)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), trust_env=False) as http:
                response = await http.get("http://127.0.0.1:8001/.well-known/oauth-protected-resource/mcp")
                assert response.status_code == 200
                assert response.json()["resource"] == RESOURCE
                assert response.json()["authorization_servers"] == [security_fixtures.ISSUER]

    asyncio.run(check())


@pytest.mark.parametrize("authorization", [None, "", "Basic credentials", "Bearer", "Bearer a b"])
def test_bad_headers_are_denied_without_database_writes(mcp_world: McpWorld, authorization: str | None) -> None:
    before = mcp_world.counts()
    headers = [] if authorization is None else [("Authorization", authorization)]
    response = mcp_world.rejected_request(headers)
    assert response.status_code == 401
    assert "Bearer" in response.headers["www-authenticate"]
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert response.headers["x-request-id"]
    assert mcp_world.counts() == before


@pytest.mark.parametrize("subject", ["mcp-requester", "never-provision-this-subject"])
@pytest.mark.parametrize("case", ["audience", "issuer", "expired", "signature", "malformed"])
def test_invalid_tokens_cannot_provision_or_write(mcp_world: McpWorld, case: str, subject: str) -> None:
    changes: dict[str, Any] = {
        "aud": RESOURCE,
        "sub": subject,
        "preferred_username": "attempted-change",
        "realm_access": {"roles": ["admin"]},
    }
    if case == "audience":
        changes["aud"] = "helpdesk-api"
    elif case == "issuer":
        changes["iss"] = "https://untrusted.example.test"
    elif case == "expired":
        changes["exp"] = int(time.time()) - 60
    token = mcp_world.harness.token(
        changes,
        key=mcp_world.harness.keys.second if case == "signature" else None,
    )
    if case == "malformed":
        token = "synthetic-malformed-token-not-a-jwt"
    before = mcp_world.snapshot()
    response = mcp_world.rejected_request([("Authorization", f"Bearer {token}")])
    assert response.status_code == 401
    assert token not in response.text
    assert mcp_world.snapshot() == before


def test_duplicate_headers_and_query_tokens_are_denied(mcp_world: McpWorld) -> None:
    token = mcp_world.token()
    before = mcp_world.counts()
    response = mcp_world.rejected_request(
        [
            ("Authorization", f"Bearer {token}"),
            ("Authorization", f"Bearer {token}"),
        ]
    )
    assert response.status_code == 401
    response = mcp_world.rejected_request([], f"{RESOURCE}?access_token=synthetic")
    assert response.status_code == 401
    assert mcp_world.counts() == before


def test_jwks_outage_is_safe_503_without_provisioning(mcp_world: McpWorld) -> None:
    mcp_world.harness.opener.failure = URLError("synthetic-private-outage-detail")
    before = mcp_world.counts()
    response = mcp_world.rejected_request([("Authorization", f"Bearer {mcp_world.token()}")])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_SERVICE_UNAVAILABLE"
    assert "synthetic-private-outage-detail" not in response.text
    assert mcp_world.counts() == before


def test_inactive_and_unrecognised_role_are_denied(mcp_world: McpWorld) -> None:
    with mcp_world.sessions() as db:
        user = db.get(User, mcp_world.users[UserRole.REQUESTER])
        assert user is not None
        user.is_active = False
        db.commit()
    response = mcp_world.rejected_request([("Authorization", f"Bearer {mcp_world.token()}")])
    assert response.status_code == 403
    token = mcp_world.token(UserRole.AGENT, realm_access={"roles": ["unrelated"]})
    response = mcp_world.rejected_request([("Authorization", f"Bearer {token}")])
    assert response.status_code == 403


def test_new_subject_is_provisioned_once_and_role_reduction_applies(mcp_world: McpWorld) -> None:
    token = mcp_world.token(sub="new-requester", preferred_username="new-requester", email="new@example.invalid")
    mcp_world.call("list_categories", {}, token)
    mcp_world.call("list_categories", {}, token)
    with mcp_world.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.keycloak_sub == "new-requester")) == 1
    admin_token = mcp_world.token(UserRole.ADMIN)
    mcp_world.call("list_categories", {}, admin_token)
    reduced = mcp_world.token(UserRole.ADMIN, realm_access={"roles": ["requester"]})
    mcp_world.call("list_categories", {}, reduced)
    with mcp_world.sessions() as db:
        user = db.get(User, mcp_world.users[UserRole.ADMIN])
        assert user is not None
        assert user.role == UserRole.REQUESTER


def test_real_persistence_for_create_comment_search_and_get(mcp_world: McpWorld) -> None:
    created = create(mcp_world)
    comment = data(mcp_world.call("add_comment", {"ticket_id": created["id"], "body": "Public keyboard update."}))
    assert comment["author_id"] == str(mcp_world.users[UserRole.REQUESTER])
    search = data(mcp_world.call("search_tickets", {"status": "OPEN"}))
    assert [item["id"] for item in search["items"]] == [created["id"]]
    detail = data(mcp_world.call("get_ticket", {"ticket_id": created["id"]}))
    assert [item["id"] for item in detail["comments"]] == [comment["id"]]
    with mcp_world.sessions() as db:
        assert db.get(Ticket, created["id"]) is not None
        assert db.get(Comment, comment["id"]) is not None


def test_foreign_ticket_is_hidden_and_staff_internal_note_stays_private(mcp_world: McpWorld) -> None:
    foreign = create(mcp_world, mcp_world.token(UserRole.ADMIN))
    first = error(mcp_world.call("get_ticket", {"ticket_id": foreign["id"]}))
    missing = error(mcp_world.call("get_ticket", {"ticket_id": foreign["id"] + 100000}))
    assert first["error"] == missing["error"]
    assert first["error"]["code"] == "TICKET_NOT_FOUND"
    own = create(mcp_world)
    search = data(mcp_world.call("search_tickets", {}))
    assert [item["id"] for item in search["items"]] == [own["id"]]
    internal = data(
        mcp_world.call(
            "add_comment",
            {
                "ticket_id": own["id"],
                "body": "synthetic-internal-note",
                "is_internal": True,
            },
            mcp_world.token(UserRole.AGENT),
        )
    )
    assert internal["is_internal"] is True
    detail = data(mcp_world.call("get_ticket", {"ticket_id": own["id"]}))
    assert detail["comments"] == []
    staff_detail = data(mcp_world.call("get_ticket", {"ticket_id": own["id"]}, mcp_world.token(UserRole.AGENT)))
    assert staff_detail["comments"] == []
    denied = error(
        mcp_world.call(
            "add_comment",
            {
                "ticket_id": own["id"],
                "body": "Forbidden internal note",
                "is_internal": True,
            },
        )
    )
    assert denied["error"]["code"] == "ACCESS_DENIED"


def test_status_resolution_and_category_listing_over_http(mcp_world: McpWorld) -> None:
    created = create(mcp_world)
    requester_denied = error(
        mcp_world.call("update_ticket_status", {"ticket_id": created["id"], "status": "IN_PROGRESS"})
    )
    assert requester_denied["error"]["code"] == "ACCESS_DENIED"
    with mcp_world.sessions() as db:
        TicketService(db, mcp_world.users[UserRole.ADMIN]).assign(
            created["id"],
            TicketAssign(assignee_id=mcp_world.users[UserRole.AGENT]),
        )
        db.add(Category(name="Retired", is_active=False))
        db.commit()
    agent = mcp_world.token(UserRole.AGENT)
    data(mcp_world.call("update_ticket_status", {"ticket_id": created["id"], "status": "IN_PROGRESS"}, agent))
    denied = error(mcp_world.call("update_ticket_status", {"ticket_id": created["id"], "status": "RESOLVED"}, agent))
    assert denied["error"]["code"] == "RESOLUTION_REQUIRED"
    resolved = data(
        mcp_world.call(
            "update_ticket_status",
            {
                "ticket_id": created["id"],
                "status": "RESOLVED",
                "resolution_comment": "Replaced keyboard.",
            },
            agent,
        )
    )
    assert resolved["status"] == "RESOLVED"
    categories = data(mcp_world.call("list_categories", {}))["categories"]
    assert [category["id"] for category in categories] == [mcp_world.category_id]


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": "synthetic-private-text", "description": "private", "category_id": 1},
        {
            "title": "synthetic-private-text",
            "description": "Long enough description.",
            "category_id": 1,
            "requester_id": "other",
        },
    ],
)
def test_bad_inputs_are_safe_and_do_not_insert_business_rows(mcp_world: McpWorld, arguments: dict[str, Any]) -> None:
    before = mcp_world.counts()
    result = error(mcp_world.call("create_ticket", arguments))
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "synthetic-private-text" not in json.dumps(result)
    assert mcp_world.counts() == before


def test_audit_has_identity_request_id_outcome_and_no_private_content(
    mcp_world: McpWorld,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = mcp_world.token()
    with caplog.at_level(logging.INFO):
        created = create(mcp_world, token)
        mcp_world.call("add_comment", {"ticket_id": created["id"], "body": "synthetic-private-comment"}, token)
        mcp_world.call("create_ticket", {"title": "synthetic-private-invalid"}, token)
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == "helpdesk.mcp.audit"]
    assert len(events) == 3
    assert {event["outcome"] for event in events} == {"success", "error"}
    assert all(event["request_id"] and event["user_id"] for event in events)
    assert all(event["user_id"] == str(mcp_world.users[UserRole.REQUESTER]) for event in events)
    assert token not in caplog.text
    assert "synthetic-private-comment" not in caplog.text
    assert "synthetic-private-invalid" not in caplog.text


def test_mcp_and_rest_audiences_are_separate(mcp_world: McpWorld) -> None:
    token = mcp_world.token()
    rest_verifier = TokenVerifier(
        issuer=security_fixtures.ISSUER,
        jwks_url=security_fixtures.JWKS_URL,
        audience="helpdesk-api",
    )
    from app.core.exceptions import AuthenticationRequired

    with pytest.raises(AuthenticationRequired):
        rest_verifier.verify(token)
    assert (
        mcp_world.rejected_request([("Authorization", f"Bearer {mcp_world.token(aud='helpdesk-api')}")]).status_code
        == 401
    )


def test_missing_mcp_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_RESOURCE_URL", raising=False)
    with pytest.raises(ValidationError):
        MCPSettings.model_validate(
            {
                "database_url": "sqlite:///:memory:",
                "keycloak_issuer": security_fixtures.ISSUER,
                "keycloak_jwks_url": security_fixtures.JWKS_URL,
            }
        )


def test_mcp_never_uses_rest_fake_identity(mcp_world: McpWorld) -> None:
    mcp_world.settings.fake_auth_enabled = True
    mcp_world.settings.fake_current_user_id = mcp_world.users[UserRole.ADMIN]
    assert mcp_world.rejected_request([]).status_code == 401


def test_unexpected_business_failure_has_safe_error_and_logs(
    mcp_world: McpWorld,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_creation(self: TicketService, payload: TicketCreate) -> TicketRead:
        raise RuntimeError("synthetic-private-driver-detail")

    monkeypatch.setattr(TicketService, "create", fail_creation)
    with caplog.at_level(logging.INFO):
        response = error(
            mcp_world.call(
                "create_ticket",
                {
                    "title": "Synthetic keyboard issue",
                    "description": "A sufficiently long synthetic description.",
                    "category_id": mcp_world.category_id,
                },
            )
        )
    assert response["error"]["code"] == "INTERNAL_ERROR"
    assert "synthetic-private-driver-detail" not in json.dumps(response)
    assert "synthetic-private-driver-detail" not in caplog.text
    with mcp_world.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
