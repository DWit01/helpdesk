from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.exceptions import AccessDenied, ApplicationError, AuthenticationRequired, Conflict, NotFound
from app.mcp.tools import add_comment
from app.models import Comment, TicketStatus, UserRole
from app.schemas.comment import CommentRead
from app.services.comment_service import CommentService
from tests import test_services as service_fixtures

world = service_fixtures.world
World = service_fixtures.World


@pytest.mark.parametrize("role", list(UserRole))
def test_add_comment_persists_public_comment_with_trusted_author(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    author_id = world.users[role].id
    body = "The keyboard works after reconnecting its cable."

    result = add_comment(
        world.comments(role),
        ticket_id=ticket.id,
        body=body,
    )

    assert isinstance(result, CommentRead)
    assert result.ticket_id == ticket.id
    assert result.author_id == author_id
    assert result.body == body
    assert result.is_internal is False

    # Reload after rollback to prove the service committed the inserted row.
    world.db.rollback()
    world.db.expire_all()

    stored = world.db.get(Comment, result.id)

    assert stored is not None
    assert stored.ticket_id == ticket.id
    assert stored.author_id == author_id
    assert stored.body == body
    assert stored.is_internal is False
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 1


@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.ADMIN])
def test_add_comment_staff_can_persist_internal_note_hidden_from_requester(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    author_id = world.users[role].id
    body = "Private staff investigation of the keyboard controller."

    result = add_comment(
        world.comments(role),
        ticket_id=ticket.id,
        body=body,
        is_internal=True,
    )

    assert result.author_id == author_id
    assert result.is_internal is True

    world.db.rollback()
    world.db.expire_all()

    stored = world.db.get(Comment, result.id)

    assert stored is not None
    assert stored.ticket_id == ticket.id
    assert stored.author_id == author_id
    assert stored.body == body
    assert stored.is_internal is True

    # The ticket's requester cannot read the note created through this adapter.
    assert world.comments().list(ticket.id) == []


def test_add_comment_requester_cannot_create_internal_note(
    world: World,
) -> None:
    ticket = world.create()

    with pytest.raises(AccessDenied) as caught:
        add_comment(
            world.comments(),
            ticket_id=ticket.id,
            body="Attempted requester internal note.",
            is_internal=True,
        )

    assert caught.value.code == "ACCESS_DENIED"
    assert caught.value.status_code == 403
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


def test_add_comment_hides_foreign_ticket_like_missing_ticket(
    world: World,
) -> None:
    hidden_ticket = world.create(actor=world.other)
    missing_id = hidden_ticket.id + 100_000
    failures: list[tuple[str, str, int]] = []

    for ticket_id in (hidden_ticket.id, missing_id):
        with pytest.raises(NotFound) as caught:
            add_comment(
                world.comments(),
                ticket_id=ticket_id,
                body="This comment must not be inserted.",
            )

        error = caught.value
        failures.append((error.code, error.message, error.status_code))

    assert failures[0] == failures[1]
    assert failures[0][0] == "TICKET_NOT_FOUND"
    assert failures[0][2] == 404
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize("role", list(UserRole))
def test_add_comment_rejects_soft_deleted_ticket(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    world.tickets(UserRole.ADMIN).delete(ticket.id)

    with pytest.raises(NotFound) as caught:
        add_comment(
            world.comments(role),
            ticket_id=ticket.id,
            body="A deleted ticket must not accept this comment.",
        )

    assert caught.value.code == "TICKET_NOT_FOUND"
    assert caught.value.status_code == 404
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize("role", list(UserRole))
def test_add_comment_rejects_closed_ticket(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    service_fixtures.set_state(world, ticket.id, TicketStatus.CLOSED)

    with pytest.raises(Conflict) as caught:
        add_comment(
            world.comments(role),
            ticket_id=ticket.id,
            body="A closed ticket must not accept this comment.",
        )

    assert caught.value.code == "TICKET_CLOSED"
    assert caught.value.status_code == 409
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ticket_id", 0),
        ("ticket_id", -1),
        ("body", ""),
        ("body", "x" * 2001),
        ("is_internal", "not-a-boolean"),
    ],
)
def test_add_comment_validates_input_without_inserting(
    world: World,
    field: str,
    value: Any,
) -> None:
    ticket = world.create()
    payload: dict[str, Any] = {
        "ticket_id": ticket.id,
        "body": "A valid public update.",
    }
    payload[field] = value

    with pytest.raises(ValidationError) as caught:
        add_comment(
            world.comments(),
            **payload,
        )

    assert any(error["loc"] == (field,) for error in caught.value.errors())
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize("field", ["author_id", "role", "id"])
def test_add_comment_rejects_protected_arguments(
    world: World,
    field: str,
) -> None:
    ticket = world.create()
    protected_values: dict[str, Any] = {
        "author_id": str(world.other.id),
        "role": UserRole.ADMIN.value,
        "id": 999_999,
    }
    payload: dict[str, Any] = {
        "ticket_id": ticket.id,
        "body": "A valid public update.",
        field: protected_values[field],
    }

    with pytest.raises(TypeError, match=rf"unexpected keyword argument '{field}'"):
        add_comment(
            world.comments(),
            **payload,
        )

    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize(
    ("actor_state", "error_type", "expected_code", "expected_status"),
    [
        ("inactive", AccessDenied, "ACCESS_DENIED", 403),
        ("missing", AuthenticationRequired, "AUTH_TOKEN_INVALID", 401),
    ],
)
def test_add_comment_preserves_account_denial_without_inserting(
    world: World,
    actor_state: str,
    error_type: type[ApplicationError],
    expected_code: str,
    expected_status: int,
) -> None:
    ticket = world.create()
    actor_id = world.inactive.id if actor_state == "inactive" else uuid4()
    service = CommentService(world.db, actor_id)

    with pytest.raises(error_type) as caught:
        add_comment(
            service,
            ticket_id=ticket.id,
            body="This account must not create a comment.",
        )

    assert caught.value.code == expected_code
    assert caught.value.status_code == expected_status
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0
