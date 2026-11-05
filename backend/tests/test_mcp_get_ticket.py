from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import AccessDenied, AuthenticationRequired, NotFound
from app.mcp.tools import get_ticket
from app.models import UserRole
from app.schemas.comment import CommentCreate
from app.services.ticket_service import TicketService
from tests import test_services as service_fixtures

world = service_fixtures.world
World = service_fixtures.World


@pytest.mark.parametrize("role", list(UserRole))
def test_get_ticket_returns_public_comments_without_internal_notes(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    public_comment = world.comments().create(
        ticket.id,
        CommentCreate(body="Public update for the requester."),
    )
    internal_comment = world.comments(UserRole.AGENT).create(
        ticket.id,
        CommentCreate(
            body="Private staff-only investigation.",
            is_internal=True,
        ),
    )

    result = get_ticket(
        world.tickets(role),
        ticket_id=ticket.id,
    )

    assert result.ticket == ticket
    assert [comment.id for comment in result.comments] == [public_comment.id]
    assert all(not comment.is_internal for comment in result.comments)

    payload = result.model_dump(mode="json")

    assert payload["ticket"]["id"] == ticket.id
    assert [comment["body"] for comment in payload["comments"]] == [public_comment.body]
    assert payload["comments"][0]["author"]["username"] == public_comment.author.username
    assert internal_comment.body not in result.model_dump_json()


def test_get_ticket_returns_empty_comments_for_ticket_without_comments(
    world: World,
) -> None:
    ticket = world.create()

    result = get_ticket(
        world.tickets(),
        ticket_id=ticket.id,
    )

    assert result.ticket.id == ticket.id
    assert result.comments == []


def test_get_ticket_hides_another_requesters_ticket(
    world: World,
) -> None:
    hidden_ticket = world.create(actor=world.other)
    world.comments(UserRole.AGENT).create(
        hidden_ticket.id,
        CommentCreate(body="A public comment on a hidden ticket."),
    )

    with pytest.raises(NotFound) as caught:
        get_ticket(
            world.tickets(),
            ticket_id=hidden_ticket.id,
        )

    assert caught.value.code == "TICKET_NOT_FOUND"
    assert caught.value.status_code == 404


def test_get_ticket_hidden_and_missing_have_equivalent_errors(
    world: World,
) -> None:
    hidden_ticket = world.create(actor=world.other)
    missing_id = hidden_ticket.id + 100_000
    failures: list[tuple[str, str, int]] = []

    for ticket_id in (hidden_ticket.id, missing_id):
        with pytest.raises(NotFound) as caught:
            get_ticket(
                world.tickets(),
                ticket_id=ticket_id,
            )

        error = caught.value
        failures.append((error.code, error.message, error.status_code))

    assert failures[0] == failures[1]


@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.ADMIN])
def test_get_ticket_staff_can_read_another_requesters_ticket(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create(actor=world.other)

    result = get_ticket(
        world.tickets(role),
        ticket_id=ticket.id,
    )

    assert result.ticket.id == ticket.id
    assert result.ticket.requester.id == world.other.id


@pytest.mark.parametrize("role", list(UserRole))
def test_get_ticket_excludes_soft_deleted_tickets(
    world: World,
    role: UserRole,
) -> None:
    ticket = world.create()
    world.tickets(UserRole.ADMIN).delete(ticket.id)

    with pytest.raises(NotFound) as caught:
        get_ticket(
            world.tickets(role),
            ticket_id=ticket.id,
        )

    assert caught.value.code == "TICKET_NOT_FOUND"
    assert caught.value.status_code == 404


@pytest.mark.parametrize("ticket_id", [0, -1])
def test_get_ticket_rejects_nonpositive_id(
    world: World,
    ticket_id: int,
) -> None:
    with pytest.raises(ValidationError):
        get_ticket(
            world.tickets(),
            ticket_id=ticket_id,
        )


def test_get_ticket_preserves_inactive_user_denial(
    world: World,
) -> None:
    ticket = world.create()
    service = TicketService(world.db, world.inactive.id)

    with pytest.raises(AccessDenied):
        get_ticket(
            service,
            ticket_id=ticket.id,
        )


def test_get_ticket_preserves_missing_user_denial(
    world: World,
) -> None:
    ticket = world.create()
    service = TicketService(world.db, uuid4())

    with pytest.raises(AuthenticationRequired):
        get_ticket(
            service,
            ticket_id=ticket.id,
        )
