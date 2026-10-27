import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app import seed
from app.models import Category, Comment, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.repositories.comment_repository import CommentRepository
from app.repositories.ticket_repository import TicketRepository


def row_counts(session: Session) -> tuple[int, int, int, int]:
    # Count stored rows, including deleted tickets, to catch accidental duplicates.
    return (
        session.scalar(select(func.count()).select_from(Category)) or 0,
        session.scalar(select(func.count()).select_from(User)) or 0,
        session.scalar(select(func.count()).select_from(Ticket)) or 0,
        session.scalar(select(func.count()).select_from(Comment)) or 0,
    )


def test_seed_creates_related_synthetic_demo_data(db_session: Session) -> None:
    result = seed.seed_demo_data(db_session)
    db_session.expunge_all()

    assert result == seed.SeedResult(3, 2, 3, 2)
    assert row_counts(db_session) == (3, 2, 3, 2)
    categories = list(db_session.scalars(select(Category).order_by(Category.name)))
    assert [category.name for category in categories] == ["Hardware", "Network", "Software"]
    users = list(db_session.scalars(select(User)))
    assert {user.role for user in users} == {UserRole.REQUESTER, UserRole.AGENT}
    assert all(user.email.endswith("@example.invalid") for user in users)
    tickets = list(db_session.scalars(select(Ticket).order_by(Ticket.ticket_number)))
    assert [ticket.status for ticket in tickets] == [
        TicketStatus.OPEN,
        TicketStatus.IN_PROGRESS,
        TicketStatus.RESOLVED,
    ]
    assert [ticket.priority for ticket in tickets] == [TicketPriority.MEDIUM, TicketPriority.HIGH, TicketPriority.LOW]
    assert [ticket.category.name for ticket in tickets] == ["Hardware", "Network", "Software"]
    assert all(ticket.requester.keycloak_sub == seed.DEMO_REQUESTER_SUB for ticket in tickets)
    assert tickets[0].assignee is None
    for ticket in tickets[1:]:
        assert ticket.assignee is not None
        assert ticket.assignee.keycloak_sub == seed.DEMO_AGENT_SUB
    assert tickets[0].resolved_at is None
    assert tickets[1].resolved_at is None
    assert tickets[2].resolved_at is not None
    assert tickets[2].resolved_at.utcoffset() is not None
    assert all(ticket.deleted_at is None and ticket.closed_at is None for ticket in tickets)
    comments = CommentRepository(db_session)
    assert len(comments.list_for_ticket(tickets[2].id)) == 1
    assert len(comments.list_for_ticket(tickets[2].id, include_internal=True)) == 2
    assert all(comment.author.keycloak_sub == seed.DEMO_AGENT_SUB for comment in tickets[2].comments)


def test_seed_rerun_after_commit_preserves_ids_and_adds_no_duplicates(db_session: Session) -> None:
    seed.seed_demo_data(db_session)
    ticket_ids = list(db_session.scalars(select(Ticket.id).order_by(Ticket.id)))
    user_ids = set(db_session.scalars(select(User.id)))
    db_session.commit()
    db_session.expunge_all()

    result = seed.seed_demo_data(db_session)

    assert result == seed.SeedResult()
    assert row_counts(db_session) == (3, 2, 3, 2)
    assert list(db_session.scalars(select(Ticket.id).order_by(Ticket.id))) == ticket_ids
    assert set(db_session.scalars(select(User.id))) == user_ids


def test_seed_preserves_edits_inactive_records_and_deleted_ticket(db_session: Session) -> None:
    seed.seed_demo_data(db_session)
    ticket = db_session.scalars(select(Ticket).where(Ticket.ticket_number == "DEMO-000003")).one()
    ticket.title = "Manually edited demo ticket"
    ticket.deleted_at = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    ticket.category.is_active = False
    ticket.requester.is_active = False
    ticket.requester.username = "edited-requester"
    comment = ticket.comments[0]
    comment.body = "Manually edited comment"
    ticket_id, comment_id = ticket.id, comment.id
    db_session.commit()
    db_session.expunge_all()

    assert seed.seed_demo_data(db_session) == seed.SeedResult()
    db_session.expunge_all()

    stored = db_session.get(Ticket, ticket_id)
    assert stored is not None
    assert stored.title == "Manually edited demo ticket"
    assert stored.deleted_at == datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert stored.category.is_active is False
    assert stored.requester.is_active is False
    assert stored.requester.username == "edited-requester"
    retained_comment = db_session.get(Comment, comment_id)
    assert retained_comment is not None
    assert retained_comment.body == "Manually edited comment"
    assert TicketRepository(db_session).get_by_id(ticket_id) is None
    assert CommentRepository(db_session).list_for_ticket(ticket_id, include_internal=True) == []
    assert row_counts(db_session) == (3, 2, 3, 2)


def test_seed_reuses_category_without_overwriting_it_or_unrelated_data(db_session: Session) -> None:
    hardware = Category(name="Hardware", description="Existing local description")
    unrelated = Category(name="Other support", description="Keep this category")
    db_session.add_all([hardware, unrelated])
    db_session.commit()
    hardware_id, unrelated_id = hardware.id, unrelated.id

    result = seed.seed_demo_data(db_session)
    db_session.expire_all()

    assert result == seed.SeedResult(2, 2, 3, 2)
    assert hardware.id == hardware_id
    assert hardware.description == "Existing local description"
    assert unrelated.id == unrelated_id
    assert unrelated.description == "Keep this category"
    ticket = db_session.scalars(select(Ticket).where(Ticket.ticket_number == "DEMO-000001")).one()
    assert ticket.category_id == hardware_id
    assert row_counts(db_session) == (4, 2, 3, 2)


def test_seed_does_not_recreate_a_physically_deleted_demo_comment(db_session: Session) -> None:
    seed.seed_demo_data(db_session)
    comment = db_session.scalars(select(Comment).where(Comment.is_internal.is_(True))).one()
    db_session.delete(comment)
    db_session.commit()

    assert seed.seed_demo_data(db_session) == seed.SeedResult()
    assert row_counts(db_session) == (3, 2, 3, 1)


def test_seed_does_not_commit(db_session: Session) -> None:
    seed.seed_demo_data(db_session)

    db_session.rollback()

    assert row_counts(db_session) == (0, 0, 0, 0)


def test_failed_seed_can_roll_back_partial_inserts(db_session: Session) -> None:
    # Software is processed after the other tickets, exercising a late failure.
    existing = Category(name="Software", is_active=False)
    db_session.add(existing)
    db_session.commit()
    existing_id = existing.id

    with pytest.raises(ValueError, match="inactive"):
        seed.seed_demo_data(db_session)
    db_session.rollback()
    db_session.expunge_all()

    assert row_counts(db_session) == (1, 0, 0, 0)
    retained = db_session.get(Category, existing_id)
    assert retained is not None
    assert retained.is_active is False


@pytest.mark.parametrize("invalid_agent", ["inactive", "wrong-role"])
def test_seed_rejects_unavailable_existing_demo_agent(db_session: Session, invalid_agent: str) -> None:
    user = User(
        keycloak_sub=seed.DEMO_AGENT_SUB,
        username="existing-demo-agent",
        email="existing-demo-agent@example.invalid",
        role=UserRole.REQUESTER if invalid_agent == "wrong-role" else UserRole.AGENT,
        is_active=invalid_agent != "inactive",
    )
    db_session.add(user)
    db_session.commit()

    with pytest.raises(ValueError, match="demo agent"):
        seed.seed_demo_data(db_session)
    db_session.rollback()

    assert row_counts(db_session) == (0, 1, 0, 0)


@pytest.mark.parametrize("invalid_requester", ["inactive", "wrong-role"])
def test_seed_rejects_unavailable_existing_demo_requester(db_session: Session, invalid_requester: str) -> None:
    user = User(
        keycloak_sub=seed.DEMO_REQUESTER_SUB,
        username="existing-demo-requester",
        email="existing-demo-requester@example.invalid",
        role=UserRole.AGENT if invalid_requester == "wrong-role" else UserRole.REQUESTER,
        is_active=invalid_requester != "inactive",
    )
    db_session.add(user)
    db_session.commit()

    with pytest.raises(ValueError, match="demo requester"):
        seed.seed_demo_data(db_session)
    db_session.rollback()

    assert row_counts(db_session) == (0, 1, 0, 0)


def test_cli_requires_explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["seed"])

    with pytest.raises(SystemExit) as error:
        seed.main()

    assert error.value.code == 2


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [("production", "sqlite:///:memory:"), ("local", "postgresql://localhost/demo")],
)
def test_cli_rejects_unsupported_targets(monkeypatch: pytest.MonkeyPatch, environment: str, database_url: str) -> None:
    monkeypatch.setattr(sys, "argv", ["seed", "--confirm-demo-data"])
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(SystemExit) as error:
        seed.main()

    assert error.value.code == 2


def test_cli_commits_and_reports_rerun_counts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.db import session as session_module

    monkeypatch.setattr(sys, "argv", ["seed", "--confirm-demo-data"])
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(session_module, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    seed.main()
    seed.main()

    output = capsys.readouterr().out
    assert "3 categories, 2 users, 3 tickets, 2 comments created" in output
    assert "0 categories, 0 users, 0 tickets, 0 comments created" in output
    assert row_counts(db_session) == (3, 2, 3, 2)


def test_cli_rolls_back_every_insert_on_failure(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import session as session_module

    monkeypatch.setattr(sys, "argv", ["seed", "--confirm-demo-data"])
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(session_module, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    def fail_after_insert(session: Session) -> seed.SeedResult:
        session.add(Category(name="Rollback example"))
        session.flush()
        raise ValueError("Simulated late failure")

    monkeypatch.setattr(seed, "seed_demo_data", fail_after_insert)

    with pytest.raises(SystemExit) as error:
        seed.main()

    assert error.value.code == 1
    assert row_counts(db_session) == (0, 0, 0, 0)


def test_cli_requires_migrations_and_does_not_create_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import session as session_module

    monkeypatch.setattr(sys, "argv", ["seed", "--confirm-demo-data"])
    monkeypatch.setenv("APP_ENV", "local")
    empty_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(session_module, "SessionLocal", sessionmaker(bind=empty_engine))
    try:
        with pytest.raises(SystemExit) as error:
            seed.main()

        assert error.value.code == 1
        assert inspect(empty_engine).get_table_names() == []
    finally:
        empty_engine.dispose()
