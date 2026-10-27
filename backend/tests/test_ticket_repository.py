from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Category, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.repositories.ticket_repository import TicketRepository


def create_ticket(db_session: Session, ticket_number: str = "HD-000001", *, persist: bool = True) -> Ticket:
    # Insert the records required by the ticket's foreign keys.
    requester = User(
        keycloak_sub=f"subject-{ticket_number}",
        username=f"user-{ticket_number}",
        email=f"{ticket_number.lower()}@example.com",
        role=UserRole.REQUESTER,
    )
    category = Category(
        name=f"Category {ticket_number}",
        description="Category used by repository tests.",
    )

    db_session.add_all([requester, category])
    db_session.flush()

    # Construct the Ticket using the assigned foreign-key values.
    ticket = Ticket(
        ticket_number=ticket_number,
        title="Laptop does not start",
        description="The laptop does not respond when the power button is pressed.",
        requester_id=requester.id,
        category_id=category.id,
    )

    # Existing lookup tests still receive an inserted ticket by default.
    if persist:
        db_session.add(ticket)
        db_session.flush()

    return ticket


def create_agent(db_session: Session) -> User:
    agent = User(
        keycloak_sub="test-agent-subject",
        username="test-agent",
        email="agent@example.com",
        role=UserRole.AGENT,
        is_active=True,
    )

    db_session.add(agent)
    db_session.flush()

    return agent


def test_get_by_id_returns_matching_ticket(db_session: Session) -> None:
    create_ticket(db_session, ticket_number="HD-000001")
    target = create_ticket(db_session, ticket_number="HD-000002")
    target_id = target.id

    # Detach objects so the lookup retrieves database-backed state.
    db_session.expunge_all()

    repository = TicketRepository(db_session)
    result = repository.get_by_id(target_id)

    assert result is not None
    assert result.id == target_id
    assert result.ticket_number == "HD-000002"


def test_get_by_id_returns_none_for_missing_ticket(db_session: Session) -> None:
    existing = create_ticket(db_session)
    missing_id = existing.id + 1
    db_session.expunge_all()

    repository = TicketRepository(db_session)
    result = repository.get_by_id(missing_id)

    assert result is None


def test_get_by_id_returns_none_for_soft_deleted_ticket(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket.deleted_at = datetime.now(UTC)
    db_session.flush()
    ticket_id = ticket.id
    db_session.expunge_all()

    repository = TicketRepository(db_session)
    result = repository.get_by_id(ticket_id)

    assert result is None

    # The repository hides the ticket, but the database row still exists.
    stored_ticket = db_session.get(Ticket, ticket_id)

    assert stored_ticket is not None
    assert stored_ticket.deleted_at is not None


def test_get_by_id_returns_closed_ticket_that_is_not_deleted(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket.status = TicketStatus.CLOSED
    ticket.closed_at = datetime.now(UTC)
    db_session.flush()
    ticket_id = ticket.id
    db_session.expunge_all()

    repository = TicketRepository(db_session)
    result = repository.get_by_id(ticket_id)

    assert result is not None
    assert result.id == ticket_id
    assert result.status == TicketStatus.CLOSED
    assert result.deleted_at is None


def test_get_by_id_hides_ticket_already_tracked_by_session(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket.deleted_at = datetime.now(UTC)
    db_session.flush()

    # Keep the deleted object tracked to exercise this Session state too.
    repository = TicketRepository(db_session)
    result = repository.get_by_id(ticket.id)

    assert result is None


def test_add_assigns_id_and_inserts_ticket(db_session: Session) -> None:
    ticket = create_ticket(db_session, persist=False)
    repository = TicketRepository(db_session)

    result = repository.add(ticket)

    # The repository returns the original object with a generated ID.
    assert result is ticket
    assert isinstance(result.id, int)

    ticket_id = result.id
    db_session.expunge_all()

    # Retrieve independently to verify that an actual row was inserted.
    saved_ticket = db_session.get(Ticket, ticket_id)

    assert saved_ticket is not None
    assert saved_ticket.ticket_number == "HD-000001"
    assert saved_ticket.title == "Laptop does not start"


def test_add_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session, persist=False)
    repository = TicketRepository(db_session)

    repository.add(ticket)
    ticket_id = ticket.id

    # Undo the transaction after the repository has flushed its insert.
    db_session.rollback()

    assert db_session.get(Ticket, ticket_id) is None


def test_update_details_persists_changes(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket_id = ticket.id
    original_number = ticket.ticket_number
    repository = TicketRepository(db_session)

    result = repository.update_details(
        ticket,
        title="VPN connection fails",
        description="The VPN connection fails whenever the employee attempts to sign in.",
        priority=TicketPriority.HIGH,
    )

    assert result is ticket

    # Detach objects, then retrieve the stored state independently.
    db_session.expunge_all()
    saved_ticket = db_session.get(Ticket, ticket_id)

    assert saved_ticket is not None
    assert saved_ticket.title == "VPN connection fails"
    assert saved_ticket.description == "The VPN connection fails whenever the employee attempts to sign in."
    assert saved_ticket.priority == TicketPriority.HIGH

    # Editing details must preserve the ticket's identity.
    assert saved_ticket.id == ticket_id
    assert saved_ticket.ticket_number == original_number


def test_update_details_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    original_title = ticket.title
    original_description = ticket.description
    original_priority = ticket.priority
    repository = TicketRepository(db_session)

    # Preserve the starting ticket so rollback only undoes the update.
    db_session.commit()

    repository.update_details(
        ticket,
        title="VPN connection fails",
        description="The VPN connection fails whenever the employee attempts to sign in.",
        priority=TicketPriority.HIGH,
    )

    db_session.rollback()
    db_session.refresh(ticket)

    assert ticket.title == original_title
    assert ticket.description == original_description
    assert ticket.priority == original_priority


def test_update_category_persists_new_category(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket_id = ticket.id
    original_category_id = ticket.category_id

    new_category = Category(name="Network")
    db_session.add(new_category)
    db_session.flush()
    new_category_id = new_category.id

    repository = TicketRepository(db_session)
    result = repository.update_category(ticket, category_id=new_category_id)

    assert result is ticket

    db_session.expunge_all()
    saved_ticket = db_session.get(Ticket, ticket_id)

    assert saved_ticket is not None
    assert saved_ticket.category_id == new_category_id
    assert saved_ticket.category.name == "Network"
    assert db_session.get(Category, original_category_id) is not None


def test_update_category_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    original_category_id = ticket.category_id

    new_category = Category(name="Network")
    db_session.add(new_category)
    db_session.flush()
    new_category_id = new_category.id

    # Commit the starting state so rollback only undoes the category change.
    db_session.commit()

    repository = TicketRepository(db_session)
    repository.update_category(ticket, category_id=new_category_id)

    db_session.rollback()
    db_session.refresh(ticket)

    assert ticket.category_id == original_category_id
    assert db_session.get(Category, new_category_id) is not None


def test_update_assignee_assigns_existing_agent(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    agent = create_agent(db_session)
    ticket_id = ticket.id
    agent_id = agent.id
    repository = TicketRepository(db_session)

    result = repository.update_assignee(ticket, assignee_id=agent_id)

    assert result is ticket

    # Reload independently to verify the stored foreign key and relationship.
    db_session.expunge_all()
    saved_ticket = db_session.get(Ticket, ticket_id)

    assert saved_ticket is not None
    assert saved_ticket.assignee_id == agent_id
    assert saved_ticket.assignee is not None
    assert saved_ticket.assignee.username == "test-agent"
    assert saved_ticket.status == TicketStatus.OPEN


def test_update_assignee_can_unassign_ticket(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    agent = create_agent(db_session)

    # Establish an assigned ticket before exercising unassignment.
    ticket.assignee_id = agent.id
    db_session.flush()
    ticket_id = ticket.id

    repository = TicketRepository(db_session)
    result = repository.update_assignee(ticket, assignee_id=None)

    assert result is ticket

    db_session.expunge_all()
    saved_ticket = db_session.get(Ticket, ticket_id)

    assert saved_ticket is not None
    assert saved_ticket.assignee_id is None
    assert saved_ticket.assignee is None


def test_update_assignee_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    agent = create_agent(db_session)
    agent_id = agent.id

    # Preserve the original unassigned ticket and agent before the update.
    db_session.commit()

    repository = TicketRepository(db_session)
    repository.update_assignee(ticket, assignee_id=agent_id)

    db_session.rollback()
    db_session.refresh(ticket)

    assert ticket.assignee_id is None
    assert db_session.get(User, agent_id) is not None


def test_save_persists_resolved_status_and_timestamp(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    resolved_at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    repository = TicketRepository(db_session)

    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_at = resolved_at

    result = repository.save(ticket)

    assert result is ticket

    # Reload stored values to verify that save flushed the changes.
    db_session.refresh(ticket)

    assert ticket.status == TicketStatus.RESOLVED
    assert ticket.resolved_at == resolved_at
    assert ticket.closed_at is None


def test_save_persists_closed_status_and_timestamps(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    resolved_at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    closed_at = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    repository = TicketRepository(db_session)

    ticket.status = TicketStatus.CLOSED
    ticket.resolved_at = resolved_at
    ticket.closed_at = closed_at

    repository.save(ticket)
    db_session.refresh(ticket)

    assert ticket.status == TicketStatus.CLOSED
    assert ticket.resolved_at == resolved_at
    assert ticket.closed_at == closed_at


def test_save_can_clear_lifecycle_timestamps(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    ticket.status = TicketStatus.CLOSED
    ticket.resolved_at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    ticket.closed_at = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    db_session.flush()
    repository = TicketRepository(db_session)

    ticket.status = TicketStatus.OPEN
    ticket.resolved_at = None
    ticket.closed_at = None

    repository.save(ticket)
    db_session.refresh(ticket)

    assert ticket.status == TicketStatus.OPEN
    assert ticket.resolved_at is None
    assert ticket.closed_at is None


def test_save_does_not_commit_lifecycle_changes(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    repository = TicketRepository(db_session)

    # Preserve the original OPEN ticket before changing its lifecycle fields.
    db_session.commit()

    ticket.status = TicketStatus.CLOSED
    ticket.resolved_at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    ticket.closed_at = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)

    repository.save(ticket)

    db_session.rollback()
    db_session.refresh(ticket)

    assert ticket.status == TicketStatus.OPEN
    assert ticket.resolved_at is None
    assert ticket.closed_at is None
