from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Comment,
    Ticket,
    TicketPriority,
    TicketStatus,
    User,
    UserRole,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    # Create an isolated SQLite database for each test
    engine = create_engine("sqlite+pysqlite:///:memory:")

    # Enable SQLite foreign-key enforcement
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: Connection,
        connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        Base.metadata.create_all(engine)

        with Session(engine) as session:
            yield session

    finally:
        engine.dispose()


# Reusable test data helpers
def create_user(
    db_session: Session,
    keycloak_sub: str = "test-keycloak-id",
    role: UserRole = UserRole.REQUESTER,
) -> User:
    user = User(
        keycloak_sub=keycloak_sub,
        username=keycloak_sub,
        email=f"{keycloak_sub}@example.com",
        role=role,
    )

    db_session.add(user)
    db_session.flush()

    return user


def create_category(
    db_session: Session,
    name: str = "Hardware",
) -> Category:
    category = Category(
        name=name,
        description="Helpdesk support category",
    )

    db_session.add(category)
    db_session.flush()

    return category


def create_ticket(
    db_session: Session,
    requester: User,
    category: Category,
    ticket_number: str = "HD-000001",
) -> Ticket:
    ticket = Ticket(
        ticket_number=ticket_number,
        title="Laptop not working",
        description="The laptop does not start correctly.",
        requester_id=requester.id,
        category_id=category.id,
    )

    db_session.add(ticket)
    db_session.flush()

    return ticket


# --------------------------------------------------
# Table creation
# --------------------------------------------------


def test_four_tables_created(db_session: Session) -> None:
    inspector = inspect(db_session.connection())

    table_names = set(inspector.get_table_names())

    assert table_names == {
        "users",
        "categories",
        "tickets",
        "comments",
    }


# --------------------------------------------------
# UUID generation and defaults
# --------------------------------------------------


def test_user_uuid_and_defaults(db_session: Session) -> None:
    user = create_user(db_session)

    db_session.commit()
    db_session.refresh(user)

    assert isinstance(user.id, UUID)
    assert user.is_active is True

    assert isinstance(user.created_at, datetime)
    assert isinstance(user.updated_at, datetime)


def test_users_receive_different_uuids(db_session: Session) -> None:
    first_user = create_user(
        db_session,
        keycloak_sub="keycloak-user-1",
    )

    second_user = create_user(
        db_session,
        keycloak_sub="keycloak-user-2",
    )

    db_session.commit()

    assert isinstance(first_user.id, UUID)
    assert isinstance(second_user.id, UUID)
    assert first_user.id != second_user.id


# --------------------------------------------------
# Required fields and uniqueness
# --------------------------------------------------


def test_duplicate_keycloak_subject_rejected(
    db_session: Session,
) -> None:
    create_user(
        db_session,
        keycloak_sub="duplicate-keycloak-id",
    )

    duplicate_user = User(
        keycloak_sub="duplicate-keycloak-id",
        username="another-user",
        email="another@example.com",
        role=UserRole.AGENT,
    )

    db_session.add(duplicate_user)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_missing_user_role_rejected(db_session: Session) -> None:
    user = User(
        keycloak_sub="missing-role-id",
        username="user-without-role",
        email="missingrole@example.com",
    )

    db_session.add(user)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_duplicate_category_name_rejected(
    db_session: Session,
) -> None:
    create_category(db_session, name="Hardware")

    duplicate_category = Category(
        name="Hardware",
        description="Duplicate category",
    )

    db_session.add(duplicate_category)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


# --------------------------------------------------
# Foreign-key enforcement
# --------------------------------------------------


def test_invalid_category_foreign_key_rejected(
    db_session: Session,
) -> None:
    requester = create_user(db_session)

    ticket = Ticket(
        ticket_number="HD-000002",
        title="Invalid category",
        description="This ticket references a missing category.",
        requester_id=requester.id,
        category_id=99999,
    )

    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_invalid_requester_foreign_key_rejected(
    db_session: Session,
) -> None:
    category = create_category(db_session)

    ticket = Ticket(
        ticket_number="HD-000003",
        title="Invalid requester",
        description="This ticket references a missing requester.",
        requester_id=uuid4(),
        category_id=category.id,
    )

    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_invalid_comment_author_rejected(
    db_session: Session,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    comment = Comment(
        ticket_id=ticket.id,
        author_id=uuid4(),
        body="Comment with an invalid author.",
    )

    db_session.add(comment)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


# --------------------------------------------------
# ORM relationships
# --------------------------------------------------


def test_ticket_relationships(db_session: Session) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()

    assert ticket.requester.id == requester.id
    assert ticket.category.id == category.id
    assert ticket.assignee is None

    assert ticket in requester.requested_tickets
    assert ticket in category.tickets


def test_ticket_assignee_relationship(db_session: Session) -> None:
    requester = create_user(
        db_session,
        keycloak_sub="requester-1",
    )

    agent = create_user(
        db_session,
        keycloak_sub="agent-1",
        role=UserRole.AGENT,
    )

    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    ticket.assignee_id = agent.id

    db_session.commit()

    assert ticket.assignee is agent
    assert ticket in agent.assigned_tickets


def test_comment_relationships(db_session: Session) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    comment = Comment(
        ticket_id=ticket.id,
        author_id=requester.id,
        body="The laptop is still not working.",
    )

    db_session.add(comment)
    db_session.commit()

    assert comment.ticket is ticket
    assert comment.author is requester

    assert comment in ticket.comments
    assert comment in requester.comments


# --------------------------------------------------
# Enum constraints and defaults
# --------------------------------------------------


def test_ticket_status_and_priority_defaults(
    db_session: Session,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.status == TicketStatus.OPEN
    assert ticket.priority == TicketPriority.MEDIUM


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    [
        ("status", "INVALID_STATUS"),
        ("priority", "INVALID_PRIORITY"),
    ],
)
def test_invalid_ticket_enum_rejected(
    db_session: Session,
    column: str,
    invalid_value: str,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()

    # Execute invalid SQL to verify database CHECK constraints
    from sqlalchemy import text

    statement = text(f"UPDATE tickets SET {column} = :value WHERE id = :ticket_id")

    with pytest.raises(IntegrityError):
        db_session.execute(
            statement,
            {
                "value": invalid_value,
                "ticket_id": ticket.id,
            },
        )
        db_session.commit()

    db_session.rollback()


def test_invalid_user_role_rejected(db_session: Session) -> None:
    user = create_user(db_session)

    db_session.commit()

    from sqlalchemy import text

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE users SET role = :role WHERE id = :id"),
            {
                "role": "invalid_role",
                "id": user.id.hex,
            },
        )
        db_session.commit()

    db_session.rollback()


# --------------------------------------------------
# Length constraints
# --------------------------------------------------


@pytest.mark.parametrize(
    "invalid_name",
    ["A", "A" * 61],
)
def test_invalid_category_name_length_rejected(
    db_session: Session,
    invalid_name: str,
) -> None:
    category = Category(name=invalid_name)

    db_session.add(category)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "invalid_title",
    ["Test", "A" * 121],
)
def test_invalid_ticket_title_length_rejected(
    db_session: Session,
    invalid_title: str,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = Ticket(
        ticket_number="HD-000004",
        title=invalid_title,
        description="A valid ticket description.",
        requester_id=requester.id,
        category_id=category.id,
    )

    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "invalid_description",
    ["Too short", "A" * 5001],
)
def test_invalid_ticket_description_length_rejected(
    db_session: Session,
    invalid_description: str,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = Ticket(
        ticket_number="HD-000005",
        title="Description validation",
        description=invalid_description,
        requester_id=requester.id,
        category_id=category.id,
    )

    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "invalid_body",
    ["", "A" * 2001],
)
def test_invalid_comment_length_rejected(
    db_session: Session,
    invalid_body: str,
) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    comment = Comment(
        ticket_id=ticket.id,
        author_id=requester.id,
        body=invalid_body,
    )

    db_session.add(comment)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


# --------------------------------------------------
# Timestamps and soft deletion
# --------------------------------------------------


def test_ticket_timestamps_created(db_session: Session) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.created_at.tzinfo is not None
    assert ticket.created_at.utcoffset() == timedelta(0)

    assert ticket.updated_at.tzinfo is not None
    assert ticket.updated_at.utcoffset() == timedelta(0)

    assert isinstance(ticket.created_at, datetime)
    assert isinstance(ticket.updated_at, datetime)


def test_ticket_updated_at_changes(db_session: Session) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()

    # Supply a known earlier timestamp for deterministic testing
    earlier_time = datetime(2020, 1, 1, tzinfo=UTC)
    ticket.updated_at = earlier_time
    db_session.commit()

    ticket.title = "Laptop still not working"
    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.updated_at > earlier_time


def test_ticket_soft_delete_timestamp(db_session: Session) -> None:
    requester = create_user(db_session)
    category = create_category(db_session)

    ticket = create_ticket(
        db_session,
        requester=requester,
        category=category,
    )

    db_session.commit()

    assert ticket.deleted_at is None

    # Soft deletion records a timestamp without deleting the row
    ticket.deleted_at = datetime.now(UTC)

    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.deleted_at is not None

    # The ticket remains in the database
    assert db_session.get(Ticket, ticket.id) is not None
