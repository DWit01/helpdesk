from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Category, Comment, Ticket, TicketStatus, User
from app.repositories.comment_repository import CommentRepository
from app.repositories.ticket_repository import TicketRepository
from tests.test_comment_repository import create_comment
from tests.test_ticket_repository import create_agent, create_ticket


@pytest.mark.parametrize("status", list(TicketStatus))
def test_soft_delete_retains_ticket_comments_and_relationships(db_session: Session, status: TicketStatus) -> None:
    ticket = create_ticket(db_session)
    agent = create_agent(db_session)
    ticket.assignee_id = agent.id
    ticket.status = status
    if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        ticket.resolved_at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    if status == TicketStatus.CLOSED:
        ticket.closed_at = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    public = create_comment(db_session, ticket, body="Public reply")
    internal = create_comment(db_session, ticket, body="Internal note", internal=True)
    ticket_id = ticket.id
    comment_ids = {public.id, internal.id}
    original_values = (
        ticket.ticket_number,
        ticket.title,
        ticket.description,
        ticket.priority,
        ticket.created_at,
        ticket.category_id,
        ticket.requester_id,
        ticket.assignee_id,
        ticket.resolved_at,
        ticket.closed_at,
    )
    before = datetime.now(UTC)

    result = TicketRepository(db_session).soft_delete(ticket)

    after = datetime.now(UTC)
    assert result is ticket
    # Detaching makes the following assertions examine stored database values.
    db_session.expunge_all()
    stored = db_session.get(Ticket, ticket_id)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.deleted_at.utcoffset() == timedelta(0)
    assert before <= stored.deleted_at <= after
    assert stored.status == status
    assert (
        stored.ticket_number,
        stored.title,
        stored.description,
        stored.priority,
        stored.created_at,
        stored.category_id,
        stored.requester_id,
        stored.assignee_id,
        stored.resolved_at,
        stored.closed_at,
    ) == original_values
    assert db_session.get(Category, stored.category_id) is not None
    assert db_session.get(User, stored.requester_id) is not None
    assert stored.assignee is not None
    assert stored.assignee.username == "test-agent"
    assert stored.category.id == stored.category_id
    assert stored.requester.id == stored.requester_id
    assert {comment.id for comment in stored.comments} == comment_ids
    for comment_id in comment_ids:
        retained = db_session.get(Comment, comment_id)
        assert retained is not None
        assert retained.ticket_id == ticket_id
        assert retained.author_id == stored.requester_id
    assert {comment.body for comment in stored.comments} == {"Public reply", "Internal note"}
    assert {comment.is_internal for comment in stored.comments} == {False, True}


def test_soft_delete_preserves_original_timestamp_on_repeat_calls(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    repository = TicketRepository(db_session)
    repository.soft_delete(ticket)
    original_deleted_at = ticket.deleted_at
    assert original_deleted_at is not None

    # Repeat after reloading, as well as with the object already in memory.
    db_session.refresh(ticket)
    result = repository.soft_delete(ticket)
    repository.soft_delete(ticket)
    db_session.refresh(ticket)

    assert result is ticket
    assert ticket.deleted_at == original_deleted_at


@pytest.mark.parametrize("detach", [False, True], ids=["tracked", "reloaded"])
def test_soft_delete_hides_all_repository_reads_and_updates_page_totals(db_session: Session, detach: bool) -> None:
    ticket = create_ticket(db_session)
    other = create_ticket(db_session, ticket_number="HD-000002")
    public = create_comment(db_session, ticket)
    internal = create_comment(db_session, ticket, internal=True)
    other_comment = create_comment(db_session, other, body="Visible reply")
    ticket_id, other_id = ticket.id, other.id
    comment_ids = [public.id, internal.id]
    other_comment_id = other_comment.id
    repository = TicketRepository(db_session)
    comments = CommentRepository(db_session)

    repository.soft_delete(ticket)
    if detach:
        db_session.expunge_all()

    assert repository.get_by_id(ticket_id) is None
    assert repository.get_by_id(other_id) is not None
    for include_internal in (False, True):
        assert comments.list_for_ticket(ticket_id, include_internal=include_internal) == []
        for comment_id in comment_ids:
            assert comments.get_by_id(comment_id, include_internal=include_internal) is None
    assert comments.get_by_id(other_comment_id) is not None
    assert [comment.id for comment in comments.list_for_ticket(other_id)] == [other_comment_id]

    # A deleted row must neither occupy a page slot nor contribute to the count.
    page, total = repository.search(sort="created_at", page_size=1)
    assert [item.id for item in page] == [other_id]
    assert total == 1
    next_page, next_total = repository.search(sort="created_at", page=2, page_size=1)
    assert next_page == []
    assert next_total == 1
    empty_page, empty_total = repository.search(requester_id=ticket.requester_id)
    assert empty_page == []
    assert empty_total == 0


def test_soft_delete_last_visible_ticket_returns_empty_search(db_session: Session) -> None:
    ticket = create_ticket(db_session)

    TicketRepository(db_session).soft_delete(ticket)

    assert TicketRepository(db_session).search() == ([], 0)


def test_caller_can_commit_soft_delete_and_read_it_in_another_session(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    comment = create_comment(db_session, ticket)
    ticket_id, comment_id = ticket.id, comment.id
    TicketRepository(db_session).soft_delete(ticket)

    # Only the caller commits. The second Session must see the retained, hidden row.
    db_session.commit()
    with Session(db_session.get_bind()) as another_session:
        stored = another_session.get(Ticket, ticket_id)
        assert stored is not None
        assert stored.deleted_at is not None
        assert stored.assignee_id is None
        assert another_session.get(Comment, comment_id) is not None
        assert TicketRepository(another_session).get_by_id(ticket_id) is None
        assert TicketRepository(another_session).search() == ([], 0)
        assert CommentRepository(another_session).get_by_id(comment_id) is None


def test_soft_delete_does_not_commit_and_rollback_restores_visibility(db_session: Session) -> None:
    ticket = create_ticket(db_session)
    public = create_comment(db_session, ticket)
    internal = create_comment(db_session, ticket, internal=True)
    ticket_id, public_id, internal_id = ticket.id, public.id, internal.id
    # Commit only the starting fixture so rollback specifically tests the deletion.
    db_session.commit()
    original_updated_at = ticket.updated_at
    repository = TicketRepository(db_session)
    comments = CommentRepository(db_session)
    repository.soft_delete(ticket)
    assert repository.get_by_id(ticket_id) is None
    assert comments.list_for_ticket(ticket_id, include_internal=True) == []

    db_session.rollback()
    db_session.expunge_all()

    restored = repository.get_by_id(ticket_id)
    assert restored is not None
    assert restored.deleted_at is None
    assert restored.updated_at == original_updated_at
    page, total = repository.search()
    assert [item.id for item in page] == [ticket_id]
    assert total == 1
    assert comments.get_by_id(public_id) is not None
    assert comments.get_by_id(internal_id) is None
    assert comments.get_by_id(internal_id, include_internal=True) is not None
    assert [comment.id for comment in comments.list_for_ticket(ticket_id)] == [public_id]
    assert {comment.id for comment in comments.list_for_ticket(ticket_id, include_internal=True)} == {
        public_id,
        internal_id,
    }
