from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import func, select

from app.models import Category, Comment, Ticket, UserRole
from tests import test_authentication as auth_fixtures

# Reuse isolated databases and real RSA signing fixtures.
api = auth_fixtures.api
keys = auth_fixtures.keys
harness = auth_fixtures.harness
authenticated_api = auth_fixtures.authenticated_api

PRINCIPALS = {
    "alice": ("api-test-requester", UserRole.REQUESTER),
    "alex": ("permission-second-requester", UserRole.REQUESTER),
    "bob": ("api-test-agent", UserRole.AGENT),
    "carol": ("api-test-admin", UserRole.ADMIN),
}

TICKET_ACTIONS = {
    "edit": {"alice": 200, "alex": 404, "bob": 200, "carol": 200},
    "priority": {"alice": 403, "alex": 404, "bob": 200, "carol": 200},
    "assign": {"alice": 403, "alex": 404, "bob": 200, "carol": 200},
    "start": {"alice": 403, "alex": 404, "bob": 200, "carol": 200},
    "close": {"alice": 200, "alex": 404, "bob": 200, "carol": 200},
    "reopen": {"alice": 200, "alex": 404, "bob": 200, "carol": 200},
    "cancel": {"alice": 403, "alex": 404, "bob": 403, "carol": 200},
    "delete": {"alice": 403, "alex": 404, "bob": 403, "carol": 204},
}


@dataclass
class PermissionApi:
    base: auth_fixtures.AuthenticatedApi
    users: dict[str, UUID] = field(default_factory=dict)

    def headers(
        self,
        person: str,
        *,
        roles: list[str] | None = None,
    ) -> dict[str, str]:
        subject, role = PRINCIPALS[person]
        token = self.base.harness.token(
            {
                "sub": subject,
                "preferred_username": person,
                "email": f"{person}@example.test",
                "name": person.title(),
                "realm_access": {"roles": roles if roles is not None else [role.value]},
            }
        )
        return {"Authorization": f"Bearer {token}"}

    def call(
        self,
        person: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        roles: list[str] | None = None,
    ) -> Response:
        response = self.base.api.client.request(
            method,
            path,
            headers=self.headers(person, roles=roles),
            json=body,
            params=params,
        )
        # Narrow the test client's Any result and verify the runtime contract.
        assert isinstance(response, Response)
        return response

    def create_ticket(self, person: str = "alice") -> int:
        response = self.call(
            person,
            "POST",
            "/api/v1/tickets",
            body={
                "title": f"{person.title()} permission ticket",
                "description": "A ticket created for authenticated permission tests.",
                "category_id": self.base.api.category_id,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["requester"]["id"] == str(self.users[person])
        return int(response.json()["id"])

    def create_category(self, name: str = "Permission category") -> int:
        response = self.call(
            "carol",
            "POST",
            "/api/v1/categories",
            body={"name": name},
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def create_comment(
        self,
        person: str,
        ticket_id: int,
        *,
        internal: bool = False,
    ) -> int:
        response = self.call(
            person,
            "POST",
            f"/api/v1/tickets/{ticket_id}/comments",
            body={
                "body": "Private staff note" if internal else "Public ticket reply",
                "is_internal": internal,
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def resolved_ticket(self) -> int:
        ticket_id = self.create_ticket()

        assigned = self.call(
            "carol",
            "POST",
            f"/api/v1/tickets/{ticket_id}/assign",
            body={"assignee_id": str(self.users["bob"])},
        )
        assert assigned.status_code == 200, assigned.text

        started = self.call(
            "bob",
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            body={"status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text

        resolved = self.call(
            "bob",
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            body={
                "status": "RESOLVED",
                "resolution_comment": "The reported problem has been fixed.",
            },
        )
        assert resolved.status_code == 200, resolved.text
        return ticket_id

    def ticket_state(self, ticket_id: int) -> dict[str, object]:
        with self.base.api.sessions() as db:
            ticket = db.get(Ticket, ticket_id)
            assert ticket is not None

            return {
                "title": ticket.title,
                "description": ticket.description,
                "category_id": ticket.category_id,
                "priority": ticket.priority.value,
                "status": ticket.status.value,
                "assignee_id": ticket.assignee_id,
                "resolved_at": ticket.resolved_at,
                "closed_at": ticket.closed_at,
                "deleted_at": ticket.deleted_at,
            }


@pytest.fixture
def policy(
    authenticated_api: auth_fixtures.AuthenticatedApi,
) -> PermissionApi:
    result = PermissionApi(authenticated_api)

    # Provision each identity through the actual authenticated HTTP path.
    for person in PRINCIPALS:
        response = result.call(person, "GET", "/api/v1/me")
        assert response.status_code == 200, response.text
        assert response.json()["username"] == person
        result.users[person] = UUID(response.json()["id"])

    assert len(set(result.users.values())) == 4
    return result


@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_each_role_can_create_a_ticket_owned_by_itself(
    policy: PermissionApi,
    person: str,
) -> None:
    policy.create_ticket(person)


@pytest.mark.parametrize(
    ("person", "expected_total"),
    [("alice", 1), ("alex", 1), ("bob", 2), ("carol", 2)],
)
def test_lists_pagination_and_stats_respect_visibility(
    policy: PermissionApi,
    person: str,
    expected_total: int,
) -> None:
    alice_ticket = policy.create_ticket("alice")
    alex_ticket = policy.create_ticket("alex")

    response = policy.call(
        person,
        "GET",
        "/api/v1/tickets",
        params={"page_size": 1},
    )
    assert response.status_code == 200
    assert response.json()["total"] == expected_total
    assert len(response.json()["items"]) == 1

    if person in {"alice", "alex"}:
        expected_id = alice_ticket if person == "alice" else alex_ticket
        assert response.json()["items"][0]["id"] == expected_id

    stats = policy.call(person, "GET", "/api/v1/tickets/stats")
    assert stats.status_code == 200
    assert stats.json()["by_status"]["OPEN"] == expected_total
    assert sum(stats.json()["by_priority"].values()) == expected_total


def test_requester_cannot_override_ownership_filter(
    policy: PermissionApi,
) -> None:
    own_ticket = policy.create_ticket("alice")
    policy.create_ticket("alex")

    response = policy.call(
        "alice",
        "GET",
        "/api/v1/tickets",
        params={"requester_id": str(policy.users["alex"])},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [own_ticket]


def test_hidden_deleted_and_absent_tickets_have_equivalent_errors(
    policy: PermissionApi,
) -> None:
    visible = policy.create_ticket("alice")
    hidden = policy.create_ticket("alex")
    deleted = policy.create_ticket("alice")

    deletion = policy.call("carol", "DELETE", f"/api/v1/tickets/{deleted}")
    assert deletion.status_code == 204

    missing = max(visible, hidden, deleted) + 1000
    errors: list[dict[str, Any]] = []

    for ticket_id in (hidden, deleted, missing):
        response = policy.call("alice", "GET", f"/api/v1/tickets/{ticket_id}")
        assert response.status_code == 404

        error = response.json()["error"]
        assert error["details"] == []
        errors.append(
            {
                "code": error["code"],
                "message": error["message"],
                "details": error["details"],
            }
        )

    assert errors[0] == errors[1] == errors[2]

    listing = policy.call("alice", "GET", "/api/v1/tickets")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert [item["id"] for item in listing.json()["items"]] == [visible]

    stats = policy.call("alice", "GET", "/api/v1/tickets/stats")
    assert stats.json()["by_status"]["OPEN"] == 1

    with policy.base.api.sessions() as db:
        retained = db.get(Ticket, deleted)
        assert retained is not None
        assert retained.deleted_at is not None


@pytest.mark.parametrize(
    ("action", "person", "expected"),
    [(action, person, status) for action, outcomes in TICKET_ACTIONS.items() for person, status in outcomes.items()],
)
def test_ticket_action_permission_matrix(
    policy: PermissionApi,
    action: str,
    person: str,
    expected: int,
) -> None:
    ticket_id = policy.resolved_ticket() if action in {"close", "reopen"} else policy.create_ticket()
    category_id = policy.base.api.category_id

    if action == "edit":
        category_id = policy.create_category("Alternate category")

    if action == "start":
        assigned = policy.call(
            "carol",
            "POST",
            f"/api/v1/tickets/{ticket_id}/assign",
            body={"assignee_id": str(policy.users["bob"])},
        )
        assert assigned.status_code == 200

    before = policy.ticket_state(ticket_id)

    actions: dict[str, tuple[str, str, dict[str, Any] | None]] = {
        "edit": (
            "PATCH",
            f"/api/v1/tickets/{ticket_id}",
            {
                "title": "Edited permission ticket",
                "description": "Updated details for permission verification.",
                "category_id": category_id,
            },
        ),
        "priority": (
            "PATCH",
            f"/api/v1/tickets/{ticket_id}",
            {"priority": "HIGH"},
        ),
        "assign": (
            "POST",
            f"/api/v1/tickets/{ticket_id}/assign",
            {"assignee_id": str(policy.users["bob"])},
        ),
        "start": (
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            {"status": "IN_PROGRESS"},
        ),
        "close": (
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            {"status": "CLOSED"},
        ),
        "reopen": (
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            {"status": "IN_PROGRESS"},
        ),
        "cancel": (
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            {"status": "CLOSED"},
        ),
        "delete": (
            "DELETE",
            f"/api/v1/tickets/{ticket_id}",
            None,
        ),
    }

    method, path, body = actions[action]
    response = policy.call(person, method, path, body=body)
    assert response.status_code == expected, response.text

    after = policy.ticket_state(ticket_id)

    if expected >= 400:
        # Check persisted business data, not only the response status.
        assert after == before
    elif action == "edit":
        assert after["title"] == "Edited permission ticket"
        assert after["category_id"] == category_id
    elif action == "priority":
        assert after["priority"] == "HIGH"
    elif action == "assign":
        assert after["assignee_id"] == policy.users["bob"]
    elif action in {"start", "reopen"}:
        assert after["status"] == "IN_PROGRESS"
        if action == "reopen":
            assert after["resolved_at"] is None
            assert after["closed_at"] is None
    elif action in {"close", "cancel"}:
        assert after["status"] == "CLOSED"
        assert after["closed_at"] is not None
    else:
        assert after["deleted_at"] is not None


@pytest.mark.parametrize("person", ["alice", "bob", "carol"])
@pytest.mark.parametrize("action", ["edit", "assign", "comment"])
def test_closed_ticket_changes_do_not_persist(
    policy: PermissionApi,
    person: str,
    action: str,
) -> None:
    ticket_id = policy.resolved_ticket()
    closed = policy.call(
        "alice",
        "POST",
        f"/api/v1/tickets/{ticket_id}/status",
        body={"status": "CLOSED"},
    )
    assert closed.status_code == 200
    before = policy.ticket_state(ticket_id)

    if action == "edit":
        response = policy.call(
            person,
            "PATCH",
            f"/api/v1/tickets/{ticket_id}",
            body={"title": "Forbidden closed-ticket edit"},
        )
    elif action == "assign":
        response = policy.call(
            person,
            "POST",
            f"/api/v1/tickets/{ticket_id}/assign",
            body={"assignee_id": str(policy.users["bob"])},
        )
    else:
        response = policy.call(
            person,
            "POST",
            f"/api/v1/tickets/{ticket_id}/comments",
            body={"body": "Forbidden closed-ticket comment"},
        )

    # Requesters cannot assign at all; staff reach the closed-ticket check.
    expected = 403 if person == "alice" and action == "assign" else 409
    assert response.status_code == expected
    assert policy.ticket_state(ticket_id) == before

    with policy.base.api.sessions() as db:
        forbidden = db.scalar(
            select(Comment).where(
                Comment.ticket_id == ticket_id,
                Comment.body == "Forbidden closed-ticket comment",
            )
        )
    assert forbidden is None


def test_authenticated_staff_still_obey_workflow_requirements(
    policy: PermissionApi,
) -> None:
    ticket_id = policy.create_ticket()

    no_assignee = policy.call(
        "bob",
        "POST",
        f"/api/v1/tickets/{ticket_id}/status",
        body={"status": "IN_PROGRESS"},
    )
    assert no_assignee.status_code == 409
    assert policy.ticket_state(ticket_id)["status"] == "OPEN"

    assigned = policy.call(
        "carol",
        "POST",
        f"/api/v1/tickets/{ticket_id}/assign",
        body={"assignee_id": str(policy.users["bob"])},
    )
    assert assigned.status_code == 200

    started = policy.call(
        "bob",
        "POST",
        f"/api/v1/tickets/{ticket_id}/status",
        body={"status": "IN_PROGRESS"},
    )
    assert started.status_code == 200

    no_resolution = policy.call(
        "bob",
        "POST",
        f"/api/v1/tickets/{ticket_id}/status",
        body={"status": "RESOLVED"},
    )
    assert no_resolution.status_code == 409
    assert policy.ticket_state(ticket_id)["status"] == "IN_PROGRESS"


@pytest.mark.parametrize(
    ("person", "internal", "expected"),
    [
        ("alice", False, 201),
        ("alex", False, 404),
        ("bob", False, 201),
        ("carol", False, 201),
        ("alice", True, 403),
        ("alex", True, 404),
        ("bob", True, 201),
        ("carol", True, 201),
    ],
)
def test_comment_creation_permissions(
    policy: PermissionApi,
    person: str,
    internal: bool,
    expected: int,
) -> None:
    ticket_id = policy.create_ticket()
    response = policy.call(
        person,
        "POST",
        f"/api/v1/tickets/{ticket_id}/comments",
        body={"body": "Permission matrix comment", "is_internal": internal},
    )

    assert response.status_code == expected

    with policy.base.api.sessions() as db:
        count = db.scalar(select(func.count()).select_from(Comment).where(Comment.ticket_id == ticket_id))

    assert count == (1 if expected == 201 else 0)


@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_internal_comment_visibility(
    policy: PermissionApi,
    person: str,
) -> None:
    ticket_id = policy.create_ticket()
    public_id = policy.create_comment("alice", ticket_id)
    internal_id = policy.create_comment("bob", ticket_id, internal=True)

    response = policy.call(person, "GET", f"/api/v1/tickets/{ticket_id}/comments")

    if person == "alex":
        assert response.status_code == 404
        assert "Private staff note" not in response.text
        return

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}

    if person == "alice":
        assert ids == {public_id}
        assert "Private staff note" not in response.text
    else:
        assert ids == {public_id, internal_id}


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_requester_cannot_modify_an_internal_comment_by_id(
    policy: PermissionApi,
    method: str,
) -> None:
    ticket_id = policy.create_ticket()
    comment_id = policy.create_comment("bob", ticket_id, internal=True)

    response = policy.call(
        "alice",
        method,
        f"/api/v1/comments/{comment_id}",
        body={"body": "Attempted internal-note edit"} if method == "PUT" else None,
    )

    assert response.status_code == 404
    assert "Private staff note" not in response.text

    with policy.base.api.sessions() as db:
        comment = db.get(Comment, comment_id)
        assert comment is not None
        assert comment.body == "Private staff note"
        assert comment.is_internal is True


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_public_comment_author_and_admin_permissions(
    policy: PermissionApi,
    method: str,
    person: str,
) -> None:
    ticket_id = policy.create_ticket()
    comment_id = policy.create_comment("alice", ticket_id)

    response = policy.call(
        person,
        method,
        f"/api/v1/comments/{comment_id}",
        body={"body": "Updated public reply"} if method == "PUT" else None,
    )

    allowed = person in {"alice", "carol"}
    expected = (200 if method == "PUT" else 204) if allowed else (404 if person == "alex" else 403)
    assert response.status_code == expected

    with policy.base.api.sessions() as db:
        comment = db.get(Comment, comment_id)

        if allowed and method == "DELETE":
            assert comment is None
        else:
            assert comment is not None
            assert comment.body == ("Updated public reply" if allowed else "Public ticket reply")


@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_category_reads_are_available_to_all_application_roles(
    policy: PermissionApi,
    person: str,
) -> None:
    response = policy.call(person, "GET", "/api/v1/categories")
    assert response.status_code == 200
    assert any(item["id"] == policy.base.api.category_id for item in response.json())


@pytest.mark.parametrize("person", list(PRINCIPALS))
@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_category_maintenance_is_admin_only(
    policy: PermissionApi,
    person: str,
    action: str,
) -> None:
    category_id = policy.create_category() if action != "create" else policy.base.api.category_id

    if action == "create":
        response = policy.call(
            person,
            "POST",
            "/api/v1/categories",
            body={"name": "New policy category"},
        )
    elif action == "update":
        response = policy.call(
            person,
            "PUT",
            f"/api/v1/categories/{category_id}",
            body={"name": "Updated policy category", "is_active": False},
        )
    else:
        response = policy.call(person, "DELETE", f"/api/v1/categories/{category_id}")

    expected = {"create": 201, "update": 200, "delete": 204}[action] if person == "carol" else 403
    assert response.status_code == expected

    with policy.base.api.sessions() as db:
        if action == "create":
            created = db.scalar(select(Category).where(Category.name == "New policy category"))
            assert (created is not None) is (person == "carol")
        else:
            category = db.get(Category, category_id)

            if action == "delete" and person == "carol":
                assert category is None
            else:
                assert category is not None
                assert category.is_active is (person != "carol")
                assert category.name == ("Updated policy category" if person == "carol" else "Permission category")


@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_user_listing_is_staff_only(
    policy: PermissionApi,
    person: str,
) -> None:
    response = policy.call(
        person,
        "GET",
        "/api/v1/users",
        params={"role": "agent"},
    )

    expected = 200 if person in {"bob", "carol"} else 403
    assert response.status_code == expected

    if expected == 200:
        assert any(item["id"] == str(policy.users["bob"]) for item in response.json())


@pytest.mark.parametrize("person", list(PRINCIPALS))
def test_user_activation_changes_are_admin_only(
    policy: PermissionApi,
    person: str,
) -> None:
    response = policy.call(
        person,
        "PATCH",
        f"/api/v1/users/{policy.users['alex']}",
        body={"is_active": False},
    )

    assert response.status_code == (200 if person == "carol" else 403)

    current = policy.call("carol", "GET", "/api/v1/users", params={"active_only": False})
    assert current.status_code == 200
    alex = next(item for item in current.json() if item["id"] == str(policy.users["alex"]))
    assert alex["is_active"] is (person != "carol")


def test_deactivation_blocks_existing_token_until_admin_reactivation(
    policy: PermissionApi,
) -> None:
    ticket_id = policy.create_ticket()

    disabled = policy.call(
        "carol",
        "PATCH",
        f"/api/v1/users/{policy.users['alice']}",
        body={"is_active": False},
    )
    assert disabled.status_code == 200

    denied = policy.call("alice", "GET", f"/api/v1/tickets/{ticket_id}")
    assert denied.status_code == 403

    enabled = policy.call(
        "carol",
        "PATCH",
        f"/api/v1/users/{policy.users['alice']}",
        body={"is_active": True},
    )
    assert enabled.status_code == 200

    allowed = policy.call("alice", "GET", f"/api/v1/tickets/{ticket_id}")
    assert allowed.status_code == 200


def test_lower_privilege_token_removes_stored_admin_access(
    policy: PermissionApi,
) -> None:
    promoted = policy.call(
        "alice",
        "POST",
        "/api/v1/categories",
        body={"name": "Promotion policy category"},
        roles=["requester", "admin"],
    )
    assert promoted.status_code == 201

    demoted = policy.call(
        "alice",
        "POST",
        "/api/v1/categories",
        body={"name": "Demotion policy category"},
        roles=["requester"],
    )
    assert demoted.status_code == 403

    current = policy.call("alice", "GET", "/api/v1/me")
    assert current.status_code == 200
    assert current.json()["id"] == str(policy.users["alice"])
    assert current.json()["role"] == "requester"


def test_plain_headers_cannot_change_authenticated_identity_or_role(
    policy: PermissionApi,
) -> None:
    headers = {
        **policy.headers("alice"),
        "X-User-ID": str(policy.users["carol"]),
        "X-Role": "admin",
    }

    response = policy.base.api.client.post(
        "/api/v1/categories",
        headers=headers,
        json={"name": "Spoofed administrator category"},
    )

    assert response.status_code == 403
