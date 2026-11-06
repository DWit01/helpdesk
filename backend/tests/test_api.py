from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import build_engine, get_db
from app.main import app
from app.models import Category, Comment, Ticket, User, UserRole
from app.repositories.comment_repository import CommentRepository


@dataclass
class Api:
    client: TestClient
    sessions: sessionmaker[Session]
    users: dict[UserRole, UUID]
    category_id: int
    monkeypatch: pytest.MonkeyPatch

    def login(self, role: UserRole) -> None:
        """Select a stored test user through the real fake-auth dependency."""
        self.monkeypatch.setenv("FAKE_CURRENT_USER_ID", str(self.users[role]))

    def create_ticket(self, title: str = "Laptop will not start") -> int:
        response = self.client.post(
            "/api/v1/tickets",
            json={
                "title": title,
                "description": "The laptop does not respond to the power button.",
                "category_id": self.category_id,
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])


@pytest.fixture
def api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Api, None, None]:
    # A separate disposable database for each endpoint test.
    database_path = (tmp_path / "endpoint-tests.sqlite3").as_posix()
    engine = build_engine(f"sqlite+pysqlite:///{database_path}")
    sessions = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    with sessions() as db:
        users = {
            role: User(
                keycloak_sub=f"api-test-{role.value}",
                username=f"api-{role.value}",
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

    def override_db() -> Generator[Session, None, None]:
        # The real dependency graph shares this Session within each request.
        with sessions() as db:
            yield db

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")
    monkeypatch.setenv("FAKE_CURRENT_USER_ID", str(user_ids[UserRole.REQUESTER]))

    try:
        with TestClient(app) as client:
            yield Api(client, sessions, user_ids, category_id, monkeypatch)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def test_create_get_and_update_ticket(api: Api) -> None:
    ticket_id = api.create_ticket()
    response = api.client.get(f"/api/v1/tickets/{ticket_id}")

    assert response.status_code == 200
    ticket = response.json()
    assert ticket["ticket_number"] == f"HD-{ticket_id:06d}"
    assert ticket["status"] == "OPEN"
    assert ticket["requester"]["id"] == str(api.users[UserRole.REQUESTER])
    assert ticket["category"]["id"] == api.category_id
    assert ticket["assignee"] is None
    assert ticket["resolved_at"] is None
    assert ticket["closed_at"] is None
    assert "deleted_at" not in ticket
    assert "keycloak_sub" not in ticket["requester"]

    response = api.client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"title": "Updated laptop problem"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Updated laptop problem"

    with api.sessions() as db:
        stored = db.get(Ticket, ticket_id)
        assert stored is not None
        assert stored.title == "Updated laptop problem"


def test_requester_scope_counts_pagination_and_stats_route(api: Api) -> None:
    own_id = api.create_ticket()

    api.login(UserRole.ADMIN)
    api.create_ticket("Another person's laptop")
    api.login(UserRole.REQUESTER)

    response = api.client.get(
        "/api/v1/tickets",
        params={
            "page_size": 1,
            "requester_id": str(api.users[UserRole.ADMIN]),
        },
    )
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    assert page["page"] == 1
    assert page["page_size"] == 1
    assert page["pages"] == 1
    assert [item["id"] for item in page["items"]] == [own_id]

    response = api.client.get("/api/v1/tickets/stats")
    assert response.status_code == 200
    assert response.json()["by_status"]["OPEN"] == 1
    assert response.json()["by_status"]["CLOSED"] == 0

    api.login(UserRole.AGENT)
    response = api.client.get("/api/v1/tickets/stats")
    assert response.status_code == 200
    assert response.json()["by_status"]["OPEN"] == 2


def test_search_repeated_filters_sort_and_literal_text(api: Api) -> None:
    first_id = api.create_ticket("VPN failed at 100% load")
    second_id = api.create_ticket("VPN failed during normal load")
    api.create_ticket("Printer stopped responding")

    api.login(UserRole.AGENT)
    response = api.client.patch(
        f"/api/v1/tickets/{second_id}",
        json={"priority": "HIGH"},
    )
    assert response.status_code == 200

    response = api.client.get(
        "/api/v1/tickets",
        params=[
            ("status", "OPEN"),
            ("status", "RESOLVED"),
            ("priority", "MEDIUM"),
            ("priority", "HIGH"),
            ("q", "vpn"),
            ("sort", "-priority"),
        ],
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        second_id,
        first_id,
    ]

    response = api.client.get("/api/v1/tickets", params={"q": "%"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [first_id]


def test_assignment_filters_and_explicit_unassignment(api: Api) -> None:
    ticket_id = api.create_ticket()
    api.login(UserRole.AGENT)

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        json={"assignee_id": str(api.users[UserRole.AGENT])},
    )
    assert response.status_code == 200
    assert response.json()["assignee"]["id"] == str(api.users[UserRole.AGENT])

    for filters in [
        {"assignee": "me"},
        {"assignee_id": str(api.users[UserRole.AGENT])},
    ]:
        response = api.client.get("/api/v1/tickets", params=filters)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [ticket_id]

    response = api.client.get(
        "/api/v1/tickets",
        params={
            "assignee": "me",
            "assignee_id": str(api.users[UserRole.AGENT]),
        },
    )
    assert response.status_code == 422

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        json={"assignee_id": None},
    )
    assert response.status_code == 200
    assert response.json()["assignee"] is None

    response = api.client.get(
        "/api/v1/tickets",
        params={"unassigned_only": "true"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    api.login(UserRole.REQUESTER)
    assert (
        api.client.get(
            "/api/v1/tickets",
            params={"assignee": "me"},
        ).status_code
        == 403
    )


def test_date_and_timestamp_filters(api: Api) -> None:
    ticket_id = api.create_ticket()

    with api.sessions() as db:
        ticket = db.get(Ticket, ticket_id)
        assert ticket is not None
        ticket.created_at = datetime(2026, 10, 15, 12, tzinfo=UTC)
        db.commit()

    for boundaries in [
        {
            "created_from": "2026-10-15",
            "created_to": "2026-10-16",
        },
        {
            "created_from": "2026-10-15T13:00:00+01:00",
            "created_to": "2026-10-15T13:00:01+01:00",
        },
    ]:
        response = api.client.get("/api/v1/tickets", params=boundaries)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [ticket_id]

    response = api.client.get(
        "/api/v1/tickets",
        params={"created_to": "2026-10-15T12:00:00Z"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    "query",
    [
        {"page": "0"},
        {"page_size": "101"},
        {"status": "UNKNOWN"},
        {"priority": "UNKNOWN"},
        {"category_id": "0"},
        {"assignee_id": "invalid"},
        {"requester_id": "invalid"},
        {"assignee": "someone"},
        {"sort": "title"},
        {"created_from": "invalid"},
        {"created_from": "2026-10-15T12:30:00"},
        {"created_from": "2026-10-16", "created_to": "2026-10-15"},
        {"assignee_id": str(uuid4()), "unassigned_only": "true"},
    ],
)
def test_invalid_search_input_returns_422(
    api: Api,
    query: dict[str, str],
) -> None:
    response = api.client.get("/api/v1/tickets", params=query)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "Tiny"},
        {"requester_id": str(uuid4())},
        {"status": "CLOSED"},
        {"resolved_at": "2026-10-28T12:00:00Z"},
    ],
)
def test_invalid_or_server_owned_create_fields_are_rejected(
    api: Api,
    changes: dict[str, str],
) -> None:
    payload = {
        "title": "Laptop will not start",
        "description": "The laptop does not respond to its power button.",
        "category_id": api.category_id,
        **changes,
    }
    response = api.client.post("/api/v1/tickets", json=payload)
    assert response.status_code == 422

    response = api.client.get("/api/v1/tickets")
    assert response.json()["total"] == 0


@pytest.mark.parametrize("ticket_id", ["0", "-1", "invalid"])
def test_invalid_ticket_path_returns_422(api: Api, ticket_id: str) -> None:
    assert api.client.get(f"/api/v1/tickets/{ticket_id}").status_code == 422


def test_hidden_and_missing_tickets_have_same_response(api: Api) -> None:
    api.login(UserRole.ADMIN)
    hidden_id = api.create_ticket()
    api.login(UserRole.REQUESTER)

    hidden = api.client.get(f"/api/v1/tickets/{hidden_id}")
    absent = api.client.get("/api/v1/tickets/999999")

    assert hidden.status_code == absent.status_code == 404

    hidden_body = hidden.json()
    absent_body = absent.json()

    hidden_request_id = hidden_body["error"].pop("request_id")
    absent_request_id = absent_body["error"].pop("request_id")

    # Each request has its own valid ID, matching its response header.
    assert str(UUID(hidden_request_id)) == hidden_request_id
    assert str(UUID(absent_request_id)) == absent_request_id
    assert hidden_request_id == hidden.headers["X-Request-ID"]
    assert absent_request_id == absent.headers["X-Request-ID"]
    assert hidden_request_id != absent_request_id

    # Apart from request metadata, hidden and absent tickets look identical.
    assert hidden_body == absent_body
    assert hidden_body == {
        "error": {
            "code": "TICKET_NOT_FOUND",
            "message": "Ticket was not found.",
            "details": [],
        }
    }
    assert (
        api.client.get(
            f"/api/v1/tickets/{hidden_id}/comments",
        ).status_code
        == 404
    )
    assert (
        api.client.post(
            f"/api/v1/tickets/{hidden_id}/comments",
            json={"body": "Trying to reach another user's ticket"},
        ).status_code
        == 404
    )


def test_requester_cannot_change_priority_or_assign(api: Api) -> None:
    ticket_id = api.create_ticket()

    assert (
        api.client.patch(
            f"/api/v1/tickets/{ticket_id}",
            json={"priority": "HIGH"},
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            f"/api/v1/tickets/{ticket_id}/assign",
            json={"assignee_id": str(api.users[UserRole.AGENT])},
        ).status_code
        == 403
    )


def test_status_lifecycle_resolution_comment_and_closed_restriction(api: Api) -> None:
    ticket_id = api.create_ticket()
    api.login(UserRole.AGENT)

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "IN_PROGRESS"},
    )
    assert response.status_code == 409

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        json={"assignee_id": str(api.users[UserRole.AGENT])},
    )
    assert response.status_code == 200

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "IN_PROGRESS"},
    )
    assert response.status_code == 200

    response = api.client.get(f"/api/v1/tickets/{ticket_id}/comments")
    assert response.status_code == 200
    assert response.json() == []

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "RESOLVED"},
    )
    assert response.status_code == 409

    # A rejected resolution must leave the previous status intact.
    response = api.client.get(f"/api/v1/tickets/{ticket_id}")
    assert response.json()["status"] == "IN_PROGRESS"

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={
            "status": "RESOLVED",
            "resolution_comment": "Replaced the faulty power supply.",
        },
    )
    assert response.status_code == 200
    resolved_at = response.json()["resolved_at"]
    assert resolved_at is not None

    api.login(UserRole.REQUESTER)
    response = api.client.get(f"/api/v1/tickets/{ticket_id}/comments")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["body"] == "Replaced the faulty power supply."
    assert response.json()[0]["is_internal"] is False

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "CLOSED"},
    )
    assert response.status_code == 200
    assert response.json()["resolved_at"] == resolved_at
    assert response.json()["closed_at"] is not None

    assert (
        api.client.post(
            f"/api/v1/tickets/{ticket_id}/comments",
            json={"body": "An additional comment"},
        ).status_code
        == 409
    )

    with api.sessions() as db:
        ticket = db.get(Ticket, ticket_id)
        assert ticket is not None
        assert ticket.status.value == "CLOSED"


def test_comment_visibility_admin_edit_and_delete(api: Api) -> None:
    ticket_id = api.create_ticket()

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        json={"body": "A public requester comment"},
    )
    assert response.status_code == 201
    assert response.json()["author"]["username"] == f"api-{UserRole.REQUESTER.value}"
    public_id = int(response.json()["id"])

    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        json={"body": "A forbidden internal note", "is_internal": True},
    )
    assert response.status_code == 403

    api.login(UserRole.AGENT)
    response = api.client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        json={"body": "An internal diagnostic note", "is_internal": True},
    )
    assert response.status_code == 201
    internal_id = int(response.json()["id"])

    # Staff membership alone does not permit editing another author's comment.
    assert (
        api.client.put(
            f"/api/v1/comments/{public_id}",
            json={"body": "An unauthorised replacement"},
        ).status_code
        == 403
    )

    api.login(UserRole.REQUESTER)
    response = api.client.get(f"/api/v1/tickets/{ticket_id}/comments")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [public_id]
    assert response.json()[0]["author"]["username"] == f"api-{UserRole.REQUESTER.value}"
    assert set(response.json()[0]["author"]) == {"id", "username"}

    assert (
        api.client.put(
            f"/api/v1/comments/{internal_id}",
            json={"body": "Trying to edit a hidden note"},
        ).status_code
        == 404
    )

    api.login(UserRole.ADMIN)
    response = api.client.put(
        f"/api/v1/comments/{public_id}",
        json={"body": "Corrected by an administrator"},
    )
    assert response.status_code == 200
    assert response.json()["body"] == "Corrected by an administrator"
    assert response.json()["author_id"] == str(api.users[UserRole.REQUESTER])
    assert response.json()["author"]["username"] == f"api-{UserRole.REQUESTER.value}"

    response = api.client.delete(f"/api/v1/comments/{internal_id}")
    assert response.status_code == 204
    assert response.content == b""

    with api.sessions() as db:
        assert db.get(Comment, internal_id) is None


def test_soft_delete_persists_row_and_hides_ticket_comments(api: Api) -> None:
    ticket_id = api.create_ticket()
    assert (
        api.client.post(
            f"/api/v1/tickets/{ticket_id}/comments",
            json={"body": "Retain this historical comment"},
        ).status_code
        == 201
    )

    assert (
        api.client.delete(
            f"/api/v1/tickets/{ticket_id}",
        ).status_code
        == 403
    )

    api.login(UserRole.ADMIN)
    response = api.client.delete(f"/api/v1/tickets/{ticket_id}")
    assert response.status_code == 204
    assert response.content == b""

    assert api.client.get(f"/api/v1/tickets/{ticket_id}").status_code == 404
    assert (
        api.client.get(
            f"/api/v1/tickets/{ticket_id}/comments",
        ).status_code
        == 404
    )
    assert api.client.get("/api/v1/tickets").json()["total"] == 0
    assert api.client.get("/api/v1/tickets/stats").json()["by_status"]["OPEN"] == 0

    with api.sessions() as db:
        ticket = db.get(Ticket, ticket_id)
        assert ticket is not None
        assert ticket.deleted_at is not None


def test_category_crud_permissions_and_replacement(api: Api) -> None:
    assert (
        api.client.post(
            "/api/v1/categories",
            json={"name": "Access"},
        ).status_code
        == 403
    )

    api.login(UserRole.ADMIN)
    response = api.client.post(
        "/api/v1/categories",
        json={"name": "Access", "description": "Access support"},
    )
    assert response.status_code == 201
    category_id = int(response.json()["id"])

    assert (
        api.client.get(
            f"/api/v1/categories/{category_id}",
        ).status_code
        == 200
    )

    assert (
        api.client.post(
            "/api/v1/categories",
            json={"name": "Access"},
        ).status_code
        == 409
    )

    # PUT requires both name and is_active.
    assert (
        api.client.put(
            f"/api/v1/categories/{category_id}",
            json={"name": "Cloud Access"},
        ).status_code
        == 422
    )

    response = api.client.put(
        f"/api/v1/categories/{category_id}",
        json={"name": "Cloud Access", "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["description"] is None
    assert response.json()["is_active"] is False

    response = api.client.get("/api/v1/categories")
    assert category_id not in [item["id"] for item in response.json()]

    response = api.client.get(
        "/api/v1/categories",
        params={"include_inactive": "true"},
    )
    assert response.status_code == 200
    assert category_id in [item["id"] for item in response.json()]

    api.login(UserRole.REQUESTER)
    assert (
        api.client.get(
            "/api/v1/categories",
            params={"include_inactive": "true"},
        ).status_code
        == 403
    )

    api.login(UserRole.ADMIN)
    response = api.client.delete(f"/api/v1/categories/{category_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert (
        api.client.get(
            f"/api/v1/categories/{category_id}",
        ).status_code
        == 404
    )


def test_used_category_cannot_be_deleted(api: Api) -> None:
    api.create_ticket()
    api.login(UserRole.ADMIN)

    response = api.client.delete(f"/api/v1/categories/{api.category_id}")
    assert response.status_code == 409

    assert (
        api.client.get(
            f"/api/v1/categories/{api.category_id}",
        ).status_code
        == 200
    )


def test_me_user_listing_and_activation(api: Api) -> None:
    response = api.client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["id"] == str(api.users[UserRole.REQUESTER])
    assert "keycloak_sub" not in response.json()

    assert api.client.get("/api/v1/users").status_code == 403

    api.login(UserRole.AGENT)
    response = api.client.get("/api/v1/users", params={"role": "agent"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        str(api.users[UserRole.AGENT]),
    ]

    requester_id = api.users[UserRole.REQUESTER]
    assert (
        api.client.patch(
            f"/api/v1/users/{requester_id}",
            json={"is_active": False},
        ).status_code
        == 403
    )

    api.login(UserRole.ADMIN)
    response = api.client.patch(
        f"/api/v1/users/{requester_id}",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    response = api.client.get("/api/v1/users")
    assert str(requester_id) not in [item["id"] for item in response.json()]

    response = api.client.get(
        "/api/v1/users",
        params={"active_only": "false"},
    )
    assert str(requester_id) in [item["id"] for item in response.json()]

    assert (
        api.client.patch(
            f"/api/v1/users/{requester_id}",
            json={"role": "admin"},
        ).status_code
        == 422
    )

    api.login(UserRole.REQUESTER)
    assert api.client.get("/api/v1/me").status_code == 403


@pytest.mark.parametrize(
    "environment,enabled",
    [
        ("local", "false"),
        ("development", "true"),
        ("production", "true"),
    ],
)
def test_fake_auth_fails_closed_through_http(
    api: Api,
    environment: str,
    enabled: str,
) -> None:
    api.monkeypatch.setenv("APP_ENV", environment)
    api.monkeypatch.setenv("FAKE_AUTH_ENABLED", enabled)

    response = api.client.get("/api/v1/me")
    assert response.status_code == 401

    # Liveness remains public.
    response = api.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_headers_cannot_select_identity_or_role(api: Api) -> None:
    response = api.client.get(
        "/api/v1/me",
        headers={
            "X-User-ID": str(api.users[UserRole.ADMIN]),
            "X-Role": "admin",
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(api.users[UserRole.REQUESTER])
    assert response.json()["role"] == "requester"


def test_missing_category_uses_standard_error_envelope(api: Api) -> None:
    # Create a known absent ID without assuming the database's starting ID.
    absent_id = api.category_id + 1000

    response = api.client.get(
        f"/api/v1/categories/{absent_id}",
        headers={"X-Request-ID": "category-error-test"},
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "category-error-test"
    assert response.json() == {
        "error": {
            "code": "CATEGORY_NOT_FOUND",
            "message": "Category was not found.",
            "details": [],
            "request_id": "category-error-test",
        }
    }


def test_disabled_auth_uses_standard_error_envelope(api: Api) -> None:
    api.monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")

    response = api.client.get(
        "/api/v1/me",
        headers={"X-Request-ID": "auth-error-test"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "AUTH_TOKEN_INVALID",
            "message": "A valid current user is required.",
            "details": [],
            "request_id": "auth-error-test",
        }
    }


def test_api_query_validation_uses_standard_error_envelope(api: Api) -> None:
    response = api.client.get(
        "/api/v1/tickets",
        params={"page": 0},
        headers={"X-Request-ID": "validation-error-test"},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["request_id"] == "validation-error-test"
    assert any(item["loc"] == ["query", "page"] for item in error["details"])


def test_resolution_failure_after_comment_flush_is_safe_and_atomic(
    api: Api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ticket_id = api.create_ticket()
    api.login(UserRole.AGENT)

    assigned = api.client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        json={"assignee_id": str(api.users[UserRole.AGENT])},
    )
    assert assigned.status_code == 200

    started = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "IN_PROGRESS"},
    )
    assert started.status_code == 200

    private_marker = "synthetic-private-flush-detail"
    flushed_comment_ids: list[int] = []
    original_add = CommentRepository.add

    def add_then_fail(
        repository: CommentRepository,
        comment: Comment,
    ) -> Comment:
        # Perform the real INSERT and flush before simulating failure.
        saved = original_add(repository, comment)
        assert saved.id is not None
        flushed_comment_ids.append(saved.id)

        raise IntegrityError(
            f"synthetic statement: {private_marker}",
            {"private": private_marker},
            RuntimeError(private_marker),
        )

    payload = {
        "status": "RESOLVED",
        "resolution_comment": "Replaced the faulty power supply.",
    }
    request_id = "resolution-conflict-test"

    with api.monkeypatch.context() as patch:
        patch.setattr(CommentRepository, "add", add_then_fail)
        response = api.client.post(
            f"/api/v1/tickets/{ticket_id}/status",
            json=payload,
            headers={"X-Request-ID": request_id},
        )

    assert len(flushed_comment_ids) == 1
    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {
            "code": "DATA_CONFLICT",
            "message": "The operation conflicts with existing data.",
            "details": [],
            "request_id": request_id,
        }
    }
    assert private_marker not in response.text
    assert private_marker not in caplog.text

    # A fresh Session verifies what persisted after the failed request.
    with api.sessions() as db:
        stored = db.get(Ticket, ticket_id)
        assert stored is not None
        assert stored.status.value == "IN_PROGRESS"
        assert stored.resolved_at is None
        assert stored.closed_at is None
        assert db.scalar(select(func.count()).select_from(Comment).where(Comment.ticket_id == ticket_id)) == 0

    # Restoring persistence allows a later request to resolve normally.
    recovered = api.client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        json=payload,
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "RESOLVED"
    assert recovered.json()["resolved_at"] is not None

    api.login(UserRole.REQUESTER)
    comments = api.client.get(f"/api/v1/tickets/{ticket_id}/comments")

    assert comments.status_code == 200
    assert len(comments.json()) == 1
    assert comments.json()[0]["body"] == payload["resolution_comment"]
    assert comments.json()[0]["is_internal"] is False

    with api.sessions() as db:
        stored = db.get(Ticket, ticket_id)
        assert stored is not None
        assert stored.status.value == "RESOLVED"
        assert stored.resolved_at is not None
        assert db.scalar(select(func.count()).select_from(Comment).where(Comment.ticket_id == ticket_id)) == 1
