from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.exceptions import AccessDenied, AuthenticationRequired, Conflict, NotFound
from app.mcp.tools import list_categories, update_ticket_status
from app.models import Category, Comment, Ticket, TicketStatus, UserRole
from app.schemas.ticket import TicketAssign
from app.services.category_service import CategoryService
from app.services.ticket_service import TicketService
from tests import test_services as service_fixtures

world = service_fixtures.world
World = service_fixtures.World


@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.ADMIN])
def test_status_adapter_starts_assigned_ticket_and_commits(world: World, role: UserRole) -> None:
    ticket = world.create()
    world.tickets(role).assign(ticket.id, TicketAssign(assignee_id=world.users[role].id))
    result = update_ticket_status(world.tickets(role), ticket_id=ticket.id, status=TicketStatus.IN_PROGRESS)
    world.db.rollback()
    world.db.expire_all()
    stored = world.db.get(Ticket, ticket.id)
    assert stored is not None
    assert result.status == stored.status == TicketStatus.IN_PROGRESS


def test_status_adapter_resolves_with_public_resolution_comment(world: World) -> None:
    ticket = world.create()
    service_fixtures.set_state(world, ticket.id, TicketStatus.IN_PROGRESS)
    result = update_ticket_status(
        world.tickets(UserRole.AGENT),
        ticket_id=ticket.id,
        status=TicketStatus.RESOLVED,
        resolution_comment="Replaced the keyboard.",
    )
    world.db.rollback()
    world.db.expire_all()
    stored = world.db.get(Ticket, ticket.id)
    assert stored is not None
    assert result.status == stored.status == TicketStatus.RESOLVED
    assert stored.resolved_at is not None
    comments = world.comments().list(ticket.id)
    assert len(comments) == 1
    assert comments[0].body == "Replaced the keyboard."
    assert comments[0].is_internal is False


@pytest.mark.parametrize("comment", [None, "   "])
def test_status_adapter_preserves_resolution_requirement(world: World, comment: str | None) -> None:
    ticket = world.create()
    service_fixtures.set_state(world, ticket.id, TicketStatus.IN_PROGRESS)
    with pytest.raises(Conflict) as caught:
        update_ticket_status(
            world.tickets(UserRole.AGENT),
            ticket_id=ticket.id,
            status=TicketStatus.RESOLVED,
            resolution_comment=comment,
        )
    assert caught.value.code == "RESOLUTION_REQUIRED"
    assert world.tickets().get(ticket.id).status == TicketStatus.IN_PROGRESS
    assert world.db.scalar(select(func.count()).select_from(Comment)) == 0


@pytest.mark.parametrize("target", [TicketStatus.CLOSED, TicketStatus.IN_PROGRESS])
def test_status_adapter_requester_can_close_or_reopen_resolved_ticket(world: World, target: TicketStatus) -> None:
    ticket = world.create()
    service_fixtures.set_state(world, ticket.id, TicketStatus.RESOLVED)
    result = update_ticket_status(world.tickets(), ticket_id=ticket.id, status=target)
    assert result.status == target


def test_status_adapter_denies_requester_start(world: World) -> None:
    ticket = world.create()
    with pytest.raises(AccessDenied):
        update_ticket_status(world.tickets(), ticket_id=ticket.id, status=TicketStatus.IN_PROGRESS)
    assert world.tickets().get(ticket.id).status == TicketStatus.OPEN


def test_status_adapter_preserves_hidden_ticket_error(world: World) -> None:
    ticket = world.create(actor=world.other)
    with pytest.raises(NotFound) as caught:
        update_ticket_status(world.tickets(), ticket_id=ticket.id, status=TicketStatus.CLOSED)
    assert caught.value.code == "TICKET_NOT_FOUND"


@pytest.mark.parametrize(("field", "value"), [("ticket_id", 0), ("status", "UNKNOWN")])
def test_status_adapter_validates_input(world: World, field: str, value: Any) -> None:
    ticket = world.create()
    payload: dict[str, Any] = {"ticket_id": ticket.id, "status": TicketStatus.CLOSED, field: value}
    with pytest.raises(ValidationError):
        update_ticket_status(world.tickets(UserRole.ADMIN), **payload)
    assert world.tickets().get(ticket.id).status == TicketStatus.OPEN


@pytest.mark.parametrize("role", list(UserRole))
def test_categories_adapter_excludes_inactive_for_every_role(world: World, role: UserRole) -> None:
    world.db.add(Category(name="Retired", is_active=False))
    world.db.commit()
    result = list_categories(CategoryService(world.db, world.users[role].id))
    assert [category.id for category in result] == [world.category.id]
    assert all(category.is_active for category in result)


@pytest.mark.parametrize("operation", ["status", "categories"])
@pytest.mark.parametrize("actor_state", ["missing", "inactive"])
def test_remaining_adapters_preserve_actor_denial(world: World, operation: str, actor_state: str) -> None:
    ticket = world.create()
    actor_id = uuid4() if actor_state == "missing" else world.inactive.id
    error_type = AuthenticationRequired if actor_state == "missing" else AccessDenied
    with pytest.raises(error_type):
        if operation == "status":
            update_ticket_status(TicketService(world.db, actor_id), ticket_id=ticket.id, status=TicketStatus.CLOSED)
        else:
            list_categories(CategoryService(world.db, actor_id))
