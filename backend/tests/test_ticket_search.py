from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Category, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.repositories.ticket_repository import TicketRepository


def at(day: int) -> datetime:
    # Fixed timestamps make boundary and ordering assertions predictable.
    return datetime(2026, 10, day, 9, 0, tzinfo=UTC)


@pytest.fixture
def search_tickets(db_session: Session) -> list[Ticket]:
    alice = User(
        keycloak_sub="search-alice",
        username="search-alice",
        email="search-alice@example.com",
        role=UserRole.REQUESTER,
    )
    bob = User(
        keycloak_sub="search-bob",
        username="search-bob",
        email="search-bob@example.com",
        role=UserRole.REQUESTER,
    )
    agent = User(
        keycloak_sub="search-agent",
        username="search-agent",
        email="search-agent@example.com",
        role=UserRole.AGENT,
    )
    hardware = Category(name="Hardware")
    network = Category(name="Network")

    db_session.add_all([alice, bob, agent, hardware, network])
    db_session.flush()

    # Status, priority, category, requester, assignee and creation time.
    rows = [
        (TicketStatus.OPEN, TicketPriority.LOW, hardware.id, alice.id, None, at(1)),
        (TicketStatus.IN_PROGRESS, TicketPriority.HIGH, network.id, alice.id, agent.id, at(2)),
        (TicketStatus.RESOLVED, TicketPriority.MEDIUM, hardware.id, bob.id, agent.id, at(2)),
        (TicketStatus.CLOSED, TicketPriority.CRITICAL, network.id, bob.id, None, at(3)),
        (TicketStatus.OPEN, TicketPriority.HIGH, hardware.id, alice.id, agent.id, at(4)),
        (TicketStatus.OPEN, TicketPriority.MEDIUM, hardware.id, bob.id, None, at(4)),
        (TicketStatus.IN_PROGRESS, TicketPriority.HIGH, network.id, alice.id, agent.id, at(2)),
    ]

    tickets: list[Ticket] = []

    for number, (status, priority, category_id, requester_id, assignee_id, created_at) in enumerate(rows, start=1):
        ticket = Ticket(
            ticket_number=f"HD-{number:06d}",
            title=f"Search example ticket {number}",
            description="A sufficiently detailed description for ticket search tests.",
            status=status,
            priority=priority,
            category_id=category_id,
            requester_id=requester_id,
            assignee_id=assignee_id,
            created_at=created_at,
        )
        tickets.append(ticket)

    # Ticket 7 otherwise matches the same filters as ticket 2.
    tickets[6].deleted_at = at(5)

    db_session.add_all(tickets)
    db_session.flush()

    # Searches must retrieve database state rather than reuse these objects.
    db_session.expunge_all()

    return tickets


def test_search_empty_database(db_session: Session) -> None:
    tickets, total = TicketRepository(db_session).search()

    assert tickets == []
    assert total == 0


def test_search_defaults_exclude_deleted_and_return_newest_first(
    db_session: Session, search_tickets: list[Ticket]
) -> None:
    tickets, total = TicketRepository(db_session).search()

    expected = [5, 6, 4, 2, 3, 1]

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 6

    # Exclusion must not physically remove the deleted record.
    deleted_ticket = db_session.get(Ticket, search_tickets[6].id)

    assert deleted_ticket is not None
    assert deleted_ticket.deleted_at is not None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TicketStatus.OPEN, [5, 6, 1]),
        (TicketStatus.IN_PROGRESS, [2]),
        (TicketStatus.RESOLVED, [3]),
        (TicketStatus.CLOSED, [4]),
    ],
)
def test_search_filters_status(
    db_session: Session, search_tickets: list[Ticket], status: TicketStatus, expected: list[int]
) -> None:
    tickets, total = TicketRepository(db_session).search(status=status)

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == len(expected)


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        (TicketPriority.LOW, [1]),
        (TicketPriority.MEDIUM, [6, 3]),
        (TicketPriority.HIGH, [5, 2]),
        (TicketPriority.CRITICAL, [4]),
    ],
)
def test_search_filters_priority(
    db_session: Session, search_tickets: list[Ticket], priority: TicketPriority, expected: list[int]
) -> None:
    tickets, total = TicketRepository(db_session).search(priority=priority)

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == len(expected)


def test_search_filters_category(db_session: Session, search_tickets: list[Ticket]) -> None:
    tickets, total = TicketRepository(db_session).search(category_id=search_tickets[0].category_id)

    expected = [5, 6, 3, 1]

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 4


def test_search_filters_requester(db_session: Session, search_tickets: list[Ticket]) -> None:
    tickets, total = TicketRepository(db_session).search(requester_id=search_tickets[0].requester_id)

    expected = [5, 2, 1]

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 3


def test_search_filters_assignee(db_session: Session, search_tickets: list[Ticket]) -> None:
    tickets, total = TicketRepository(db_session).search(assignee_id=search_tickets[1].assignee_id)

    expected = [5, 2, 3]

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 3


def test_search_filters_unassigned_tickets(db_session: Session, search_tickets: list[Ticket]) -> None:
    tickets, total = TicketRepository(db_session).search(unassigned_only=True)

    expected = [6, 4, 1]

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 3


@pytest.mark.parametrize(
    ("created_from", "created_to", "expected"),
    [
        (at(2), None, [5, 6, 4, 2, 3]),
        (None, at(3), [2, 3, 1]),
        (at(2), at(4), [4, 2, 3]),
    ],
)
def test_search_filters_creation_dates(
    db_session: Session,
    search_tickets: list[Ticket],
    created_from: datetime | None,
    created_to: datetime | None,
    expected: list[int],
) -> None:
    tickets, total = TicketRepository(db_session).search(created_from=created_from, created_to=created_to)

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == len(expected)


def test_search_accepts_equivalent_non_utc_timestamp(db_session: Session, search_tickets: list[Ticket]) -> None:
    # 11:00 at UTC+02:00 is the same instant as 09:00 UTC.
    start = datetime(2026, 10, 2, 11, 0, tzinfo=timezone(timedelta(hours=2)))

    tickets, total = TicketRepository(db_session).search(created_from=start, created_to=at(3))

    assert [ticket.id for ticket in tickets] == [search_tickets[1].id, search_tickets[2].id]
    assert total == 2


def test_search_combines_all_filters_and_excludes_deleted_match(
    db_session: Session, search_tickets: list[Ticket]
) -> None:
    target = search_tickets[1]

    tickets, total = TicketRepository(db_session).search(
        status=TicketStatus.IN_PROGRESS,
        priority=TicketPriority.HIGH,
        category_id=target.category_id,
        assignee_id=target.assignee_id,
        requester_id=target.requester_id,
        created_from=at(2),
        created_to=at(3),
    )

    assert [ticket.id for ticket in tickets] == [target.id]
    assert total == 1


def test_search_returns_empty_when_filters_have_no_shared_match(
    db_session: Session, search_tickets: list[Ticket]
) -> None:
    tickets, total = TicketRepository(db_session).search(status=TicketStatus.CLOSED, priority=TicketPriority.LOW)

    assert tickets == []
    assert total == 0


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("created_at", [1, 2, 3, 4, 5, 6]),
        ("-created_at", [5, 6, 4, 2, 3, 1]),
        ("priority", [1, 3, 6, 2, 5, 4]),
        ("-priority", [4, 2, 5, 3, 6, 1]),
        ("status", [1, 5, 6, 2, 3, 4]),
        ("-status", [4, 3, 2, 1, 5, 6]),
    ],
)
def test_search_sorts_with_id_tie_breaker(
    db_session: Session, search_tickets: list[Ticket], sort: str, expected: list[int]
) -> None:
    tickets, total = TicketRepository(db_session).search(sort=sort)

    assert [ticket.id for ticket in tickets] == [search_tickets[number - 1].id for number in expected]
    assert total == 6


def test_search_pagination_preserves_ties_and_total(db_session: Session, search_tickets: list[Ticket]) -> None:
    repository = TicketRepository(db_session)

    first, first_total = repository.search(page=1, page_size=2)
    second, second_total = repository.search(page=2, page_size=2)
    third, third_total = repository.search(page=3, page_size=2)
    beyond, beyond_total = repository.search(page=4, page_size=2)
    repeated, repeated_total = repository.search(page=1, page_size=2)

    assert [ticket.id for ticket in first] == [search_tickets[4].id, search_tickets[5].id]
    assert [ticket.id for ticket in second] == [search_tickets[3].id, search_tickets[1].id]
    assert [ticket.id for ticket in third] == [search_tickets[2].id, search_tickets[0].id]
    assert beyond == []
    assert [ticket.id for ticket in repeated] == [ticket.id for ticket in first]
    assert first_total == second_total == third_total == beyond_total == repeated_total == 6


def test_search_counts_filtered_matches_before_pagination(db_session: Session, search_tickets: list[Ticket]) -> None:
    repository = TicketRepository(db_session)

    first, first_total = repository.search(priority=TicketPriority.HIGH, page=1, page_size=1)
    second, second_total = repository.search(priority=TicketPriority.HIGH, page=2, page_size=1)
    beyond, beyond_total = repository.search(priority=TicketPriority.HIGH, page=3, page_size=1)

    assert [ticket.id for ticket in first] == [search_tickets[4].id]
    assert [ticket.id for ticket in second] == [search_tickets[1].id]
    assert beyond == []
    assert first_total == second_total == beyond_total == 2


def test_search_default_page_size_is_twenty(db_session: Session) -> None:
    requester = User(
        keycloak_sub="page-size-requester",
        username="page-size-requester",
        email="page-size@example.com",
        role=UserRole.REQUESTER,
    )
    category = Category(name="Pagination")
    db_session.add_all([requester, category])
    db_session.flush()

    for number in range(1, 22):
        db_session.add(
            Ticket(
                ticket_number=f"PAGE-{number:06d}",
                title=f"Pagination test ticket {number}",
                description="A sufficiently detailed description for pagination tests.",
                category_id=category.id,
                requester_id=requester.id,
                created_at=at(1),
            )
        )

    db_session.flush()

    tickets, total = TicketRepository(db_session).search()

    assert len(tickets) == 20
    assert total == 21
    assert tickets[0].ticket_number == "PAGE-000001"
    assert tickets[-1].ticket_number == "PAGE-000020"


def test_search_accepts_maximum_page_size(db_session: Session, search_tickets: list[Ticket]) -> None:
    tickets, total = TicketRepository(db_session).search(page_size=100)

    assert len(tickets) == 6
    assert total == 6


def test_search_hides_newly_deleted_tracked_ticket(db_session: Session, search_tickets: list[Ticket]) -> None:
    ticket = db_session.get(Ticket, search_tickets[0].id)
    assert ticket is not None

    # Leave this change pending to exercise normal Session autoflush.
    ticket.deleted_at = at(5)

    tickets, total = TicketRepository(db_session).search()

    assert ticket.id not in [item.id for item in tickets]
    assert total == 5


def test_search_does_not_commit(db_session: Session, search_tickets: list[Ticket]) -> None:
    ticket_id = search_tickets[0].id

    TicketRepository(db_session).search()
    db_session.rollback()

    # Fixture inserts were uncommitted, so rollback must still undo them.
    assert db_session.get(Ticket, ticket_id) is None


@pytest.mark.parametrize(("page", "page_size"), [(0, 20), (-1, 20), (1, 0), (1, -1), (1, 101)])
def test_search_rejects_invalid_pagination(db_session: Session, page: int, page_size: int) -> None:
    with pytest.raises(ValueError, match="page"):
        TicketRepository(db_session).search(page=page, page_size=page_size)


@pytest.mark.parametrize("sort", ["title", "", "--created_at"])
def test_search_rejects_invalid_sort(db_session: Session, sort: str) -> None:
    with pytest.raises(ValueError, match="sort must"):
        TicketRepository(db_session).search(sort=sort)


@pytest.mark.parametrize(
    ("created_from", "created_to"),
    [
        (datetime(2026, 10, 2, 9, 0), None),
        (None, datetime(2026, 10, 3, 9, 0)),
    ],
)
def test_search_rejects_naive_dates(
    db_session: Session, created_from: datetime | None, created_to: datetime | None
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TicketRepository(db_session).search(created_from=created_from, created_to=created_to)


@pytest.mark.parametrize(("created_from", "created_to"), [(at(3), at(2)), (at(2), at(2))])
def test_search_rejects_empty_or_reversed_date_range(
    db_session: Session, created_from: datetime, created_to: datetime
) -> None:
    with pytest.raises(ValueError, match="earlier than"):
        TicketRepository(db_session).search(created_from=created_from, created_to=created_to)


def test_search_rejects_conflicting_assignee_filters(db_session: Session, search_tickets: list[Ticket]) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        TicketRepository(db_session).search(assignee_id=search_tickets[1].assignee_id, unassigned_only=True)
