from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Category, Comment, Ticket, User, UserRole
from app.repositories.comment_repository import CommentRepository


def create_ticket(db_session: Session, number: str = "HD-000001") -> Ticket:
    requester = User(
        keycloak_sub=f"subject-{number}",
        username=f"user-{number}",
        email=f"{number.lower()}@example.com",
        role=UserRole.REQUESTER,
    )
    category = Category(name=f"Category {number}")
    db_session.add_all([requester, category])
    db_session.flush()

    ticket = Ticket(
        ticket_number=number,
        title="Laptop does not start",
        description="The laptop does not respond when the power button is pressed.",
        requester_id=requester.id,
        category_id=category.id,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def create_comment(db_session: Session, ticket: Ticket, *, body: str = "Comment", internal: bool = False) -> Comment:
    comment = Comment(
        ticket_id=ticket.id,
        author_id=ticket.requester_id,
        body=body,
        is_internal=internal,
    )
    db_session.add(comment)
    db_session.flush()
    return comment


def test_get_by_id_returns_matching_public_comment(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    create_comment(db_session, ticket, body="Unrelated comment")
    target = create_comment(db_session, ticket, body="Requested comment")
    comment_id = target.id
    db_session.expunge_all()

    result = CommentRepository(db_session).get_by_id(comment_id)

    assert result is not None
    assert result.id == comment_id
    assert result.body == "Requested comment"


def test_get_by_id_returns_none_for_missing_comment(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    existing = create_comment(db_session, ticket)
    missing_id = existing.id + 1
    db_session.expunge_all()

    assert CommentRepository(db_session).get_by_id(missing_id) is None


@pytest.mark.parametrize("include_internal", [False, True])
def test_get_by_id_filters_internal_comment(db_session: Session, include_internal: bool) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket, internal=True)
    comment_id = comment.id
    db_session.expunge_all()
    repository = CommentRepository(db_session)

    # Omitting the flag must use the public-only default.
    assert repository.get_by_id(comment_id) is None
    result = repository.get_by_id(comment_id, include_internal=include_internal)

    if include_internal:
        assert result is not None
        assert result.id == comment_id
        assert result.is_internal is True
    else:
        assert result is None


@pytest.mark.parametrize("include_internal", [False, True])
def test_get_by_id_hides_comment_of_deleted_ticket(db_session: Session, include_internal: bool) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket)
    comment_id = comment.id
    ticket.deleted_at = datetime.now(UTC)
    db_session.flush()

    # Keep objects tracked to exercise the Session identity-map case too.
    result = CommentRepository(db_session).get_by_id(comment_id, include_internal=include_internal)

    assert result is None
    assert db_session.get(Comment, comment_id) is not None


def test_add_assigns_id_and_inserts_comment(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = Comment(ticket_id=ticket.id, author_id=ticket.requester_id, body="A new comment")

    result = CommentRepository(db_session).add(comment)

    assert result is comment
    assert isinstance(result.id, int)
    comment_id = result.id
    ticket_id = ticket.id
    author_id = ticket.requester_id
    db_session.expunge_all()
    saved_comment = db_session.get(Comment, comment_id)

    assert saved_comment is not None
    assert saved_comment.body == "A new comment"
    assert saved_comment.ticket_id == ticket_id
    assert saved_comment.author_id == author_id
    assert saved_comment.is_internal is False


def test_add_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = Comment(ticket_id=ticket.id, author_id=ticket.requester_id, body="A new comment")
    CommentRepository(db_session).add(comment)
    comment_id = comment.id

    db_session.rollback()

    assert db_session.get(Comment, comment_id) is None


@pytest.mark.parametrize("missing_ticket", [False, True])
def test_list_for_ticket_returns_empty_list(db_session: Session, missing_ticket: bool) -> None:
    ticket = create_ticket(db_session)
    ticket_id = ticket.id + 1 if missing_ticket else ticket.id

    assert CommentRepository(db_session).list_for_ticket(ticket_id) == []


@pytest.mark.parametrize("include_internal", [False, True])
def test_list_for_ticket_filters_and_orders_comments(db_session: Session, include_internal: bool) -> None:
    ticket = create_ticket(db_session)
    other_ticket = create_ticket(db_session, number="HD-000002")
    shared_time = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    first = create_comment(db_session, ticket, body="First tied comment")
    second = create_comment(db_session, ticket, body="Second tied comment")
    earlier = create_comment(db_session, ticket, body="Earlier comment")
    internal = create_comment(db_session, ticket, body="Internal note", internal=True)
    create_comment(db_session, other_ticket, body="Other ticket comment")
    first.created_at = shared_time
    second.created_at = shared_time
    earlier.created_at = shared_time - timedelta(hours=1)
    internal.created_at = shared_time - timedelta(hours=2)
    ticket_id = ticket.id
    expected_ids = [earlier.id, first.id, second.id]
    if include_internal:
        expected_ids.insert(0, internal.id)
    db_session.flush()
    db_session.expunge_all()
    repository = CommentRepository(db_session)

    public_results = repository.list_for_ticket(ticket_id)
    assert all(comment.is_internal is False for comment in public_results)
    assert len(public_results) == 3

    results = repository.list_for_ticket(ticket_id, include_internal=include_internal)

    assert [comment.id for comment in results] == expected_ids
    assert all(comment.ticket_id == ticket_id for comment in results)


@pytest.mark.parametrize("include_internal", [False, True])
def test_list_for_ticket_hides_deleted_ticket_comments(db_session: Session, include_internal: bool) -> None:
    ticket = create_ticket(db_session)
    create_comment(db_session, ticket)
    create_comment(db_session, ticket, internal=True)
    ticket.deleted_at = datetime.now(UTC)
    db_session.flush()

    result = CommentRepository(db_session).list_for_ticket(ticket.id, include_internal=include_internal)

    assert result == []


def test_update_body_persists_text_and_preserves_metadata(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket, internal=True)
    original_metadata = (comment.ticket_id, comment.author_id, comment.is_internal, comment.created_at)

    result = CommentRepository(db_session).update_body(comment, body="Corrected internal note")

    assert result is comment
    # Refresh must reload the edited text, rather than discard an unflushed assignment.
    db_session.refresh(comment)
    assert comment.body == "Corrected internal note"
    assert (comment.ticket_id, comment.author_id, comment.is_internal, comment.created_at) == original_metadata


def test_update_body_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket, body="Original text")
    db_session.commit()

    CommentRepository(db_session).update_body(comment, body="Changed text")
    db_session.rollback()
    db_session.refresh(comment)

    assert comment.body == "Original text"


def test_delete_removes_only_selected_comment(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    selected = create_comment(db_session, ticket, body="Remove this comment")
    retained = create_comment(db_session, ticket, body="Keep this comment")
    selected_id = selected.id
    retained_id = retained.id
    ticket_id = ticket.id

    CommentRepository(db_session).delete(selected)
    db_session.expunge_all()

    assert db_session.get(Comment, selected_id) is None
    assert db_session.get(Comment, retained_id) is not None
    assert db_session.get(Ticket, ticket_id) is not None


def test_delete_does_not_commit(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket)
    comment_id = comment.id
    db_session.commit()

    CommentRepository(db_session).delete(comment)
    db_session.rollback()
    db_session.expunge_all()
    restored = db_session.get(Comment, comment_id)

    assert restored is not None
    assert restored.body == "Comment"
