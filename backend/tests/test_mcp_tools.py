from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AccessDenied, AuthenticationRequired
from app.mcp.tools import search_tickets
from app.models import Ticket, TicketPriority, TicketStatus, User
from app.services.ticket_service import TicketService
from tests import test_ticket_search as search_fixtures

search_rows = search_fixtures.search_tickets


def agent_service(db_session: Session) -> TicketService:
    """Build a service for the staff actor in the existing search fixture."""
    agent = db_session.scalar(select(User).where(User.keycloak_sub == "search-agent"))
    assert agent is not None

    return TicketService(db_session, agent.id)


def test_search_adapter_restricts_requester_to_own_tickets(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    service = TicketService(db_session, search_rows[0].requester_id)

    result = search_tickets(service)

    expected_ids = {
        search_rows[0].id,
        search_rows[1].id,
        search_rows[4].id,
    }
    assert {ticket.id for ticket in result.items} == expected_ids
    assert result.total == 3
    assert result.page == 1
    assert result.page_size == 20
    assert result.pages == 1
    assert all(ticket.requester.id == search_rows[0].requester_id for ticket in result.items)


def test_search_adapter_staff_can_see_other_requesters_without_deleted_tickets(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    result = search_tickets(agent_service(db_session))

    assert {ticket.id for ticket in result.items} == {ticket.id for ticket in search_rows[:6]}
    assert result.total == 6
    assert search_rows[6].id not in {ticket.id for ticket in result.items}


@pytest.mark.parametrize(
    ("status", "priority", "category_index", "query", "expected_numbers"),
    [
        (TicketStatus.OPEN, None, None, None, [1, 5, 6]),
        (None, TicketPriority.HIGH, None, None, [2, 5]),
        (None, None, 1, None, [2, 4]),
        (None, None, None, "ticket 2", [2]),
        (
            TicketStatus.OPEN,
            TicketPriority.HIGH,
            0,
            "ticket 5",
            [5],
        ),
    ],
)
def test_search_adapter_passes_filters_to_service(
    db_session: Session,
    search_rows: list[Ticket],
    status: TicketStatus | None,
    priority: TicketPriority | None,
    category_index: int | None,
    query: str | None,
    expected_numbers: list[int],
) -> None:
    category_id = search_rows[category_index].category_id if category_index is not None else None

    result = search_tickets(
        agent_service(db_session),
        status=status,
        priority=priority,
        category_id=category_id,
        query=query,
    )

    assert {ticket.id for ticket in result.items} == {search_rows[number - 1].id for number in expected_numbers}
    assert result.total == len(expected_numbers)


def test_search_adapter_preserves_pagination_and_total(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    service = agent_service(db_session)

    first_page = search_tickets(service, page=1, page_size=2)
    second_page = search_tickets(service, page=2, page_size=2)

    assert first_page.total == second_page.total == 6
    assert first_page.pages == second_page.pages == 3
    assert first_page.page == 1
    assert second_page.page == 2
    assert first_page.page_size == second_page.page_size == 2
    assert len(first_page.items) == len(second_page.items) == 2
    assert {ticket.id for ticket in first_page.items}.isdisjoint(ticket.id for ticket in second_page.items)


def test_search_adapter_returns_empty_page_when_nothing_matches(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    result = search_tickets(
        agent_service(db_session),
        query="no matching ticket exists",
    )

    assert result.items == []
    assert result.total == 0
    assert result.pages == 0


@pytest.mark.parametrize(
    ("page", "page_size"),
    [
        (0, 20),
        (-1, 20),
        (1, 0),
        (1, -1),
        (1, 101),
    ],
)
def test_search_adapter_rejects_invalid_pagination(
    db_session: Session,
    search_rows: list[Ticket],
    page: int,
    page_size: int,
) -> None:
    with pytest.raises(ValidationError):
        search_tickets(
            agent_service(db_session),
            page=page,
            page_size=page_size,
        )


@pytest.mark.parametrize("category_id", [0, -1])
def test_search_adapter_rejects_nonpositive_category_id(
    db_session: Session,
    search_rows: list[Ticket],
    category_id: int,
) -> None:
    with pytest.raises(ValidationError):
        search_tickets(
            agent_service(db_session),
            category_id=category_id,
        )


def test_search_adapter_preserves_inactive_user_denial(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    requester = db_session.get(User, search_rows[0].requester_id)
    assert requester is not None
    requester.is_active = False
    db_session.flush()

    service = TicketService(db_session, requester.id)

    with pytest.raises(AccessDenied):
        search_tickets(service)


def test_search_adapter_preserves_missing_user_denial(
    db_session: Session,
    search_rows: list[Ticket],
) -> None:
    service = TicketService(db_session, uuid4())

    with pytest.raises(AuthenticationRequired):
        search_tickets(service)
