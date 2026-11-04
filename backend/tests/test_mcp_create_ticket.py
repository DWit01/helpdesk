from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AccessDenied, ApplicationError, AuthenticationRequired, Conflict, NotFound
from app.mcp.tools import create_ticket
from app.models import Ticket, TicketPriority, TicketStatus, UserRole
from app.schemas.ticket import TicketRead
from app.services.ticket_service import TicketService
from tests import test_services as service_fixtures

world = service_fixtures.world
World = service_fixtures.World


def ticket_input(world: World) -> dict[str, Any]:
    """Build boundary inputs that tests can also make deliberately invalid."""
    return {
        "title": "Keyboard does not respond",
        "description": "The keyboard stops responding when connected to the laptop.",
        "category_id": world.category.id,
    }


@pytest.mark.parametrize("role", list(UserRole))
def test_create_ticket_persists_fields_and_trusted_caller(
    world: World,
    role: UserRole,
) -> None:
    actor_id = world.users[role].id
    category_id = world.category.id

    result = create_ticket(
        world.tickets(role),
        title="Keyboard does not respond",
        description="The keyboard stops responding when connected to the laptop.",
        category_id=category_id,
        priority=TicketPriority.HIGH,
    )

    assert isinstance(result, TicketRead)
    assert result.title == "Keyboard does not respond"
    assert result.description == "The keyboard stops responding when connected to the laptop."
    assert result.category.id == category_id
    assert result.requester.id == actor_id
    assert result.priority == TicketPriority.HIGH

    # A missing service commit would lose the inserted row here.
    world.db.rollback()
    world.db.expire_all()

    stored = world.db.get(Ticket, result.id)

    assert stored is not None
    assert stored.title == result.title
    assert stored.description == result.description
    assert stored.category_id == category_id
    assert stored.requester_id == actor_id
    assert stored.priority == TicketPriority.HIGH
    assert stored.ticket_number == result.ticket_number
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 1


def test_create_ticket_uses_default_priority_and_initial_state(
    world: World,
) -> None:
    result = create_ticket(
        world.tickets(),
        **ticket_input(world),
    )

    assert result.priority == TicketPriority.MEDIUM
    assert result.status == TicketStatus.OPEN
    assert result.assignee is None
    assert result.resolved_at is None
    assert result.closed_at is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "tiny"),
        ("description", "too short"),
        ("category_id", 0),
        ("priority", "URGENT"),
    ],
)
def test_create_ticket_reuses_schema_validation_without_inserting(
    world: World,
    field: str,
    value: Any,
) -> None:
    payload = ticket_input(world)
    payload[field] = value

    with pytest.raises(ValidationError) as caught:
        create_ticket(
            world.tickets(),
            **payload,
        )

    assert any(error["loc"] == (field,) for error in caught.value.errors())
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize(
    "field",
    [
        "requester_id",
        "role",
        "status",
        "assignee_id",
        "ticket_number",
    ],
)
def test_create_ticket_rejects_protected_arguments(
    world: World,
    field: str,
) -> None:
    protected_values = {
        "requester_id": str(world.other.id),
        "role": UserRole.ADMIN.value,
        "status": TicketStatus.CLOSED.value,
        "assignee_id": str(world.users[UserRole.AGENT].id),
        "ticket_number": "HD-999999",
    }
    payload = ticket_input(world)
    payload[field] = protected_values[field]

    # Ordinary internal functions reject unknown keywords before their body runs.
    with pytest.raises(TypeError, match=rf"unexpected keyword argument '{field}'"):
        create_ticket(
            world.tickets(),
            **payload,
        )

    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize(
    ("category_state", "error_type", "expected_code", "expected_status"),
    [
        ("missing", NotFound, "CATEGORY_NOT_FOUND", 404),
        ("inactive", Conflict, "CATEGORY_INACTIVE", 409),
    ],
)
def test_create_ticket_preserves_category_failures_without_inserting(
    world: World,
    category_state: str,
    error_type: type[ApplicationError],
    expected_code: str,
    expected_status: int,
) -> None:
    payload = ticket_input(world)

    if category_state == "inactive":
        world.category.is_active = False
        world.db.commit()
    else:
        payload["category_id"] = world.category.id + 100_000

    with pytest.raises(error_type) as caught:
        create_ticket(
            world.tickets(),
            **payload,
        )

    assert caught.value.code == expected_code
    assert caught.value.status_code == expected_status
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize(
    ("actor_state", "error_type", "expected_code", "expected_status"),
    [
        ("inactive", AccessDenied, "ACCESS_DENIED", 403),
        ("missing", AuthenticationRequired, "AUTH_TOKEN_INVALID", 401),
    ],
)
def test_create_ticket_preserves_account_denial_without_inserting(
    world: World,
    actor_state: str,
    error_type: type[ApplicationError],
    expected_code: str,
    expected_status: int,
) -> None:
    actor_id = world.inactive.id if actor_state == "inactive" else uuid4()
    service = TicketService(world.db, actor_id)

    with pytest.raises(error_type) as caught:
        create_ticket(
            service,
            **ticket_input(world),
        )

    assert caught.value.code == expected_code
    assert caught.value.status_code == expected_status
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 0


def test_create_ticket_commit_failure_rolls_back_and_session_recovers(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = world.tickets()
    payload = ticket_input(world)
    private_marker = "synthetic-private-create-detail"
    commit_attempts: list[bool] = []

    def fail_commit() -> None:
        # The real service/repository inserted a row before this simulated failure.
        assert world.db.scalar(select(func.count()).select_from(Ticket)) == 1
        commit_attempts.append(True)

        raise IntegrityError(
            "synthetic create-ticket commit failure",
            {"private": private_marker},
            RuntimeError(private_marker),
        )

    # Restore commit before checking recovery through the same service/Session.
    with monkeypatch.context() as patch:
        patch.setattr(world.db, "commit", fail_commit)

        with pytest.raises(Conflict) as caught:
            create_ticket(
                service,
                **payload,
            )

    assert commit_attempts == [True]
    assert caught.value.code == "DATA_CONFLICT"
    assert caught.value.status_code == 409
    assert str(caught.value) == "The operation conflicts with existing data."
    assert private_marker not in str(caught.value)
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 0

    recovered = create_ticket(
        service,
        **payload,
    )

    world.db.rollback()
    world.db.expire_all()

    stored = world.db.get(Ticket, recovered.id)

    assert stored is not None
    assert stored.title == payload["title"]
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 1
