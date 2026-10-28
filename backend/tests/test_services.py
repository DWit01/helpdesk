from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AccessDenied, ApplicationError, Conflict, InvalidInput, NotFound
from app.models import Category, Comment, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.repositories.comment_repository import CommentRepository
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.schemas.comment import CommentCreate, CommentUpdate
from app.schemas.pagination import PageParams
from app.schemas.ticket import TicketAssign, TicketCreate, TicketRead, TicketStatusChange, TicketUpdate
from app.schemas.user import UserUpdate
from app.services.category_service import CategoryService
from app.services.comment_service import CommentService
from app.services.ticket_service import TicketService
from app.services.user_service import UserService


@dataclass
class World:
    db: Session
    users: dict[UserRole, User]
    other: User
    inactive: User
    category: Category

    def tickets(self, role: UserRole = UserRole.REQUESTER) -> TicketService:
        return TicketService(self.db, self.users[role].id)

    def comments(self, role: UserRole = UserRole.REQUESTER) -> CommentService:
        return CommentService(self.db, self.users[role].id)

    def create(self, *, actor: User | None = None, title: str = "Laptop will not start") -> TicketRead:
        user = actor if actor is not None else self.users[UserRole.REQUESTER]
        return TicketService(self.db, user.id).create(
            TicketCreate(
                title=title,
                description="The laptop does not respond to its power button.",
                category_id=self.category.id,
            )
        )


@pytest.fixture
def world(db_session: Session) -> World:
    users = {
        role: User(
            keycloak_sub=f"test-{role.value}",
            username=role.value,
            email=f"{role.value}@example.invalid",
            role=role,
            is_active=True,
        )
        for role in UserRole
    }
    other = User(
        keycloak_sub="test-other",
        username="other",
        email="other@example.invalid",
        role=UserRole.REQUESTER,
        is_active=True,
    )
    inactive = User(
        keycloak_sub="test-inactive",
        username="inactive",
        email="inactive@example.invalid",
        role=UserRole.AGENT,
        is_active=False,
    )
    category = Category(name="Hardware", description="Hardware support.", is_active=True)
    db_session.add_all([*users.values(), other, inactive, category])
    db_session.commit()
    return World(db_session, users, other, inactive, category)


def set_state(world: World, ticket_id: int, status: TicketStatus) -> None:
    """Arrange lifecycle state without using the operation under test."""
    ticket = world.db.get(Ticket, ticket_id)
    assert ticket is not None
    ticket.status = status
    ticket.assignee_id = world.users[UserRole.AGENT].id
    ticket.resolved_at = datetime.now(UTC) if status in {TicketStatus.RESOLVED, TicketStatus.CLOSED} else None
    ticket.closed_at = datetime.now(UTC) if status == TicketStatus.CLOSED else None
    world.db.commit()


@pytest.mark.parametrize("role", list(UserRole))
def test_create_defaults_numbering_and_utc(world: World, role: UserRole) -> None:
    ticket = world.create(actor=world.users[role])

    assert ticket.ticket_number == f"HD-{ticket.id:06d}"
    assert ticket.status == TicketStatus.OPEN
    assert ticket.priority == TicketPriority.MEDIUM
    assert ticket.requester.id == world.users[role].id
    assert ticket.assignee is None
    assert ticket.resolved_at is None
    assert ticket.closed_at is None
    assert ticket.created_at.utcoffset() == timedelta(0)
    assert ticket.updated_at.utcoffset() == timedelta(0)


def test_supplied_initial_priority_is_preserved(world: World) -> None:
    ticket = world.tickets().create(
        TicketCreate(
            title="Urgent laptop problem",
            description="The laptop cannot start during an important presentation.",
            category_id=world.category.id,
            priority=TicketPriority.CRITICAL,
        )
    )
    assert ticket.priority == TicketPriority.CRITICAL


def test_number_is_not_reused_after_soft_delete(world: World) -> None:
    first = world.create()
    world.tickets(UserRole.ADMIN).delete(first.id)
    second = world.create()

    assert second.id > first.id
    assert second.ticket_number != first.ticket_number
    assert world.db.get(Ticket, first.id) is not None
    assert world.db.scalar(select(func.count()).select_from(Ticket)) == 2


@pytest.mark.parametrize("operation", ["get", "update", "assign", "status", "delete", "comments"])
def test_other_requester_cannot_reach_ticket(world: World, operation: str) -> None:
    ticket = world.create(actor=world.other)
    service = world.tickets()

    with pytest.raises(NotFound) as error:
        if operation == "get":
            service.get(ticket.id)
        elif operation == "update":
            service.update(ticket.id, TicketUpdate(title="Changed laptop title"))
        elif operation == "assign":
            service.assign(ticket.id, TicketAssign(assignee_id=None))
        elif operation == "status":
            service.change_status(ticket.id, TicketStatusChange(status=TicketStatus.CLOSED))
        elif operation == "delete":
            service.delete(ticket.id)
        else:
            world.comments().list(ticket.id)

    assert error.value.status_code == 404


def test_requester_search_and_stats_are_scoped_before_pagination(world: World) -> None:
    own = world.create()
    world.create(actor=world.other)

    page = world.tickets().search(requester_id=world.other.id, params=PageParams(page_size=1))
    assert page.total == 1
    assert [ticket.id for ticket in page.items] == [own.id]
    assert world.tickets().stats()["by_status"]["OPEN"] == 1
    assert world.tickets(UserRole.AGENT).search().total == 2
    assert world.tickets(UserRole.AGENT).stats()["by_status"]["OPEN"] == 2


def test_search_text_multiple_filters_and_literal_wildcards(world: World) -> None:
    match = world.create(title="VPN failure at 100% load")
    world.create(title="VPN failure at normal load")
    world.create(title="Printer queue stopped")

    page = world.tickets().search(
        q="vpn",
        statuses=[TicketStatus.OPEN, TicketStatus.RESOLVED],
        priorities=[TicketPriority.MEDIUM, TicketPriority.HIGH],
    )
    assert page.total == 2
    assert world.tickets().search(q="100%").items[0].id == match.id
    assert world.tickets().search(q="%").total == 1
    assert world.tickets().search(q="_").total == 0
    assert world.tickets().search(statuses=[TicketStatus.RESOLVED]).total == 0


def test_search_description_and_page_total(world: World) -> None:
    for _ in range(3):
        world.create()

    page = world.tickets().search(q="POWER BUTTON", params=PageParams(page=2, page_size=2))
    assert page.total == 3
    assert page.pages == 2
    assert len(page.items) == 1


def test_bad_service_search_becomes_validation_error(world: World) -> None:
    with pytest.raises(InvalidInput):
        world.tickets().search(sort="invalid")


def test_assignee_me_is_staff_only(world: World) -> None:
    ticket = world.create()
    world.tickets(UserRole.AGENT).assign(ticket.id, TicketAssign(assignee_id=world.users[UserRole.AGENT].id))
    assert world.tickets(UserRole.AGENT).search(assignee_me=True).total == 1

    with pytest.raises(AccessDenied):
        world.tickets().search(assignee_me=True)


def test_requester_edit_rules(world: World) -> None:
    ticket = world.create()
    changed = world.tickets().update(ticket.id, TicketUpdate(title="Laptop still will not start"))
    assert changed.title == "Laptop still will not start"

    with pytest.raises(AccessDenied):
        world.tickets().update(ticket.id, TicketUpdate(priority=TicketPriority.HIGH))

    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)
    with pytest.raises(AccessDenied):
        world.tickets().update(ticket.id, TicketUpdate(description="A longer replacement description."))


@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.ADMIN])
def test_staff_can_edit_details_and_priority(world: World, role: UserRole) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)
    changed = world.tickets(role).update(
        ticket.id, TicketUpdate(title="New support title", priority=TicketPriority.HIGH)
    )
    assert changed.title == "New support title"
    assert changed.priority == TicketPriority.HIGH


@pytest.mark.parametrize("target", ["requester", "inactive", "missing"])
def test_invalid_assignee_is_rejected(world: World, target: str) -> None:
    ticket = world.create()
    user_id = {
        "requester": world.users[UserRole.REQUESTER].id,
        "inactive": world.inactive.id,
        "missing": uuid4(),
    }[target]

    with pytest.raises(Conflict) as error:
        world.tickets(UserRole.AGENT).assign(ticket.id, TicketAssign(assignee_id=user_id))

    assert error.value.code == "INVALID_ASSIGNEE"
    assert world.tickets().get(ticket.id).assignee is None


@pytest.mark.parametrize("target_role", [UserRole.AGENT, UserRole.ADMIN])
def test_active_staff_can_be_assigned(world: World, target_role: UserRole) -> None:
    ticket = world.create()
    assigned = world.tickets(UserRole.AGENT).assign(ticket.id, TicketAssign(assignee_id=world.users[target_role].id))
    assert assigned.assignee is not None
    assert assigned.assignee.id == world.users[target_role].id

    unassigned = world.tickets(UserRole.AGENT).assign(ticket.id, TicketAssign(assignee_id=None))
    assert unassigned.assignee is None


def test_requester_cannot_assign(world: World) -> None:
    ticket = world.create()
    with pytest.raises(AccessDenied):
        world.tickets().assign(ticket.id, TicketAssign(assignee_id=world.users[UserRole.AGENT].id))


def test_start_and_reopen_require_active_assignee(world: World) -> None:
    ticket = world.create()
    staff = world.tickets(UserRole.AGENT)

    with pytest.raises(Conflict) as error:
        staff.change_status(ticket.id, TicketStatusChange(status=TicketStatus.IN_PROGRESS))
    assert error.value.code == "ASSIGNEE_REQUIRED"

    set_state(world, ticket.id, TicketStatus.RESOLVED)
    world.users[UserRole.AGENT].is_active = False
    world.db.commit()

    with pytest.raises(Conflict) as error:
        world.tickets().change_status(ticket.id, TicketStatusChange(status=TicketStatus.IN_PROGRESS))
    assert error.value.code == "INVALID_ASSIGNEE"


def test_in_progress_ticket_cannot_be_unassigned(world: World) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)

    with pytest.raises(Conflict):
        world.tickets(UserRole.AGENT).assign(ticket.id, TicketAssign(assignee_id=None))


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("source", list(TicketStatus))
@pytest.mark.parametrize("target", list(TicketStatus))
def test_transition_matrix(world: World, role: UserRole, source: TicketStatus, target: TicketStatus) -> None:
    ticket = world.create()
    set_state(world, ticket.id, source)
    service = world.tickets(role)
    data = TicketStatusChange(
        status=target,
        resolution_comment="Replaced the faulty power supply." if target == TicketStatus.RESOLVED else None,
    )

    pair = (source, target)
    # Expected transitions come from plan Section 4.2, independently
    # of the service's implementation.
    allowed = pair in {
        (TicketStatus.OPEN, TicketStatus.IN_PROGRESS),
        (TicketStatus.IN_PROGRESS, TicketStatus.OPEN),
        (TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED),
        (TicketStatus.RESOLVED, TicketStatus.CLOSED),
        (TicketStatus.RESOLVED, TicketStatus.IN_PROGRESS),
        (TicketStatus.OPEN, TicketStatus.CLOSED),
    }
    if role == UserRole.REQUESTER:
        allowed = (
            allowed
            and source == TicketStatus.RESOLVED
            and target
            in {
                TicketStatus.CLOSED,
                TicketStatus.IN_PROGRESS,
            }
        )
    if pair == (TicketStatus.OPEN, TicketStatus.CLOSED):
        allowed = allowed and role == UserRole.ADMIN

    if not allowed:
        with pytest.raises(ApplicationError):
            service.change_status(ticket.id, data)
        assert world.tickets(UserRole.ADMIN).get(ticket.id).status == source
        return

    result = service.change_status(ticket.id, data)
    assert result.status == target
    assert (result.closed_at is not None) == (target == TicketStatus.CLOSED)

    if target == TicketStatus.RESOLVED:
        assert result.resolved_at is not None
        assert result.resolved_at.utcoffset() == timedelta(0)
        comments = world.comments().list(ticket.id)
        assert comments[-1].body == "Replaced the faulty power supply."
        assert comments[-1].is_internal is False

    if pair == (TicketStatus.RESOLVED, TicketStatus.IN_PROGRESS):
        assert result.resolved_at is None


@pytest.mark.parametrize("body", [None, "   "])
def test_resolve_requires_nonblank_comment(world: World, body: str | None) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)

    with pytest.raises(Conflict) as error:
        world.tickets(UserRole.AGENT).change_status(
            ticket.id, TicketStatusChange(status=TicketStatus.RESOLVED, resolution_comment=body)
        )

    assert error.value.code == "RESOLUTION_REQUIRED"
    assert world.tickets().get(ticket.id).resolved_at is None
    assert world.comments().list(ticket.id) == []


def test_resolution_comment_rejected_for_other_transitions(world: World) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.RESOLVED)

    with pytest.raises(InvalidInput):
        world.tickets().change_status(
            ticket.id, TicketStatusChange(status=TicketStatus.CLOSED, resolution_comment="Unrelated text.")
        )


def test_closing_preserves_resolution_timestamp(world: World) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.RESOLVED)
    before = world.tickets().get(ticket.id).resolved_at

    closed = world.tickets().change_status(ticket.id, TicketStatusChange(status=TicketStatus.CLOSED))
    assert closed.resolved_at == before
    assert closed.closed_at is not None
    assert closed.closed_at.utcoffset() == timedelta(0)


def test_resolution_failure_rolls_back_status_and_comment(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)

    def fail_add(self: CommentRepository, comment: Comment) -> Comment:
        raise RuntimeError("Simulated comment persistence failure")

    monkeypatch.setattr(CommentRepository, "add", fail_add)

    with pytest.raises(RuntimeError, match="Simulated"):
        world.tickets(UserRole.AGENT).change_status(
            ticket.id,
            TicketStatusChange(status=TicketStatus.RESOLVED, resolution_comment="A valid resolution."),
        )

    current = world.tickets().get(ticket.id)
    assert current.status == TicketStatus.IN_PROGRESS
    assert current.resolved_at is None
    assert world.comments().list(ticket.id) == []


@pytest.mark.parametrize("operation", ["update", "assign", "add_comment", "edit_comment", "delete_comment"])
def test_closed_ticket_rejects_mutations(world: World, operation: str) -> None:
    ticket = world.create()
    comment = world.comments(UserRole.ADMIN).create(ticket.id, CommentCreate(body="Initial comment."))
    set_state(world, ticket.id, TicketStatus.CLOSED)

    with pytest.raises(Conflict) as error:
        if operation == "update":
            world.tickets(UserRole.ADMIN).update(ticket.id, TicketUpdate(title="Changed closed title"))
        elif operation == "assign":
            world.tickets(UserRole.ADMIN).assign(ticket.id, TicketAssign(assignee_id=world.users[UserRole.AGENT].id))
        elif operation == "add_comment":
            world.comments(UserRole.ADMIN).create(ticket.id, CommentCreate(body="Another comment."))
        elif operation == "edit_comment":
            world.comments(UserRole.ADMIN).update(comment.id, CommentUpdate(body="Edited comment."))
        else:
            world.comments(UserRole.ADMIN).delete(comment.id)

    assert error.value.code == "TICKET_CLOSED"


def test_internal_comments_are_hidden_from_requester(world: World) -> None:
    ticket = world.create()
    public = world.comments(UserRole.AGENT).create(ticket.id, CommentCreate(body="Public update."))
    internal = world.comments(UserRole.AGENT).create(
        ticket.id, CommentCreate(body="Private engineering note.", is_internal=True)
    )

    assert [comment.id for comment in world.comments().list(ticket.id)] == [public.id]
    assert len(world.comments(UserRole.AGENT).list(ticket.id)) == 2

    with pytest.raises(AccessDenied):
        world.comments().create(ticket.id, CommentCreate(body="Not allowed.", is_internal=True))

    with pytest.raises(NotFound):
        world.comments().update(internal.id, CommentUpdate(body="Attempted edit."))

    with pytest.raises(NotFound):
        world.comments().delete(internal.id)


def test_comment_id_cannot_bypass_ticket_ownership(world: World) -> None:
    ticket = world.create(actor=world.other)
    comment = world.comments(UserRole.AGENT).create(ticket.id, CommentCreate(body="Public comment."))

    with pytest.raises(NotFound):
        world.comments().update(comment.id, CommentUpdate(body="Attempted edit."))


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_comment_author_and_admin_permissions(world: World, operation: str) -> None:
    ticket = world.create()
    own = world.comments().create(ticket.id, CommentCreate(body="Requester comment."))

    with pytest.raises(AccessDenied):
        if operation == "update":
            world.comments(UserRole.AGENT).update(own.id, CommentUpdate(body="Agent edit."))
        else:
            world.comments(UserRole.AGENT).delete(own.id)

    if operation == "update":
        changed = world.comments().update(own.id, CommentUpdate(body="My corrected comment."))
        assert changed.body == "My corrected comment."
        changed = world.comments(UserRole.ADMIN).update(own.id, CommentUpdate(body="Admin correction."))
        assert changed.body == "Admin correction."
    else:
        world.comments(UserRole.ADMIN).delete(own.id)
        assert world.comments().list(ticket.id) == []


def test_comment_author_can_delete_own_comment(world: World) -> None:
    ticket = world.create()
    comment = world.comments().create(ticket.id, CommentCreate(body="Temporary comment."))
    world.comments().delete(comment.id)
    assert world.comments().list(ticket.id) == []


@pytest.mark.parametrize("role", [UserRole.REQUESTER, UserRole.AGENT])
def test_only_admin_can_soft_delete(world: World, role: UserRole) -> None:
    ticket = world.create()
    with pytest.raises(AccessDenied):
        world.tickets(role).delete(ticket.id)


def test_soft_delete_hides_ticket_comments_and_stats(world: World) -> None:
    ticket = world.create()
    comment = world.comments().create(ticket.id, CommentCreate(body="Retained history."))
    world.tickets(UserRole.ADMIN).delete(ticket.id)

    for role in UserRole:
        assert world.tickets(role).search().total == 0
        assert sum(world.tickets(role).stats()["by_status"].values()) == 0
        with pytest.raises(NotFound):
            world.tickets(role).get(ticket.id)
        with pytest.raises(NotFound):
            world.comments(role).list(ticket.id)
        with pytest.raises(NotFound):
            world.comments(role).update(comment.id, CommentUpdate(body="Attempted edit."))

    stored = world.db.get(Ticket, ticket.id)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.deleted_at.utcoffset() == timedelta(0)
    assert world.db.get(Comment, comment.id) is not None


def test_inactive_category_rules_and_existing_ticket(world: World) -> None:
    ticket = world.create()
    service = CategoryService(world.db, world.users[UserRole.ADMIN].id)
    service.update(
        world.category.id,
        CategoryUpdate(name="Hardware", is_active=False),
    )

    assert world.tickets().get(ticket.id).category.id == world.category.id
    assert CategoryService(world.db, world.users[UserRole.REQUESTER].id).list() == []
    assert len(service.list(include_inactive=True)) == 1

    # An existing ticket can retain its inactive category during unrelated edits.
    world.tickets().update(ticket.id, TicketUpdate(title="Existing hardware issue"))

    with pytest.raises(Conflict):
        world.create()
    with pytest.raises(Conflict):
        world.tickets().update(ticket.id, TicketUpdate(category_id=world.category.id))


def test_used_category_cannot_be_deleted_even_after_ticket_soft_delete(world: World) -> None:
    ticket = world.create()
    world.tickets(UserRole.ADMIN).delete(ticket.id)

    with pytest.raises(Conflict) as error:
        CategoryService(world.db, world.users[UserRole.ADMIN].id).delete(world.category.id)

    assert error.value.code == "CATEGORY_IN_USE"


def test_category_create_replace_duplicate_and_delete(world: World) -> None:
    service = CategoryService(world.db, world.users[UserRole.ADMIN].id)
    category = service.create(CategoryCreate(name="Network", description="Original description."))

    with pytest.raises(Conflict):
        service.create(CategoryCreate(name="Network"))

    changed = service.update(category.id, CategoryUpdate(name="Connectivity", is_active=True))
    assert changed.description is None
    assert changed.name == "Connectivity"

    service.delete(category.id)
    with pytest.raises(NotFound):
        service.get(category.id)


@pytest.mark.parametrize("role", [UserRole.REQUESTER, UserRole.AGENT])
@pytest.mark.parametrize("operation", ["create", "update", "delete", "include_inactive"])
def test_category_admin_permissions(world: World, role: UserRole, operation: str) -> None:
    service = CategoryService(world.db, world.users[role].id)

    with pytest.raises(AccessDenied):
        if operation == "create":
            service.create(CategoryCreate(name="Network"))
        elif operation == "update":
            service.update(world.category.id, CategoryUpdate(name="Hardware", is_active=False))
        elif operation == "delete":
            service.delete(world.category.id)
        else:
            service.list(include_inactive=True)


def test_user_listing_activation_and_safe_response(world: World) -> None:
    agent = UserService(world.db, world.users[UserRole.AGENT].id)
    agents = agent.list(role=UserRole.AGENT)
    assert [user.id for user in agents] == [world.users[UserRole.AGENT].id]
    assert "keycloak_sub" not in agent.me().model_dump()

    with pytest.raises(AccessDenied):
        UserService(world.db, world.users[UserRole.REQUESTER].id).list()

    with pytest.raises(AccessDenied):
        agent.update(world.other.id, UserUpdate(is_active=False))

    admin = UserService(world.db, world.users[UserRole.ADMIN].id)
    admin.update(world.other.id, UserUpdate(is_active=False))

    with pytest.raises(AccessDenied):
        TicketService(world.db, world.other.id).search()

    admin.update(world.other.id, UserUpdate(is_active=True))
    assert UserService(world.db, world.other.id).me().is_active


def test_invalid_current_user_is_rejected(world: World) -> None:
    with pytest.raises(ApplicationError) as error:
        UserService(world.db, uuid4()).me()
    assert error.value.status_code == 401


def test_resolution_commit_failure_rolls_back_and_session_recovers(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = world.create()
    set_state(world, ticket.id, TicketStatus.IN_PROGRESS)
    staff = world.tickets(UserRole.AGENT)
    private_marker = "synthetic-private-commit-detail"
    commit_attempts: list[bool] = []

    def fail_commit() -> None:
        # Prove both changes reached the transaction before failure.
        stored = world.db.get(Ticket, ticket.id)
        assert stored is not None
        assert stored.status == TicketStatus.RESOLVED
        assert stored.resolved_at is not None
        assert world.db.scalar(select(func.count()).select_from(Comment).where(Comment.ticket_id == ticket.id)) == 1

        commit_attempts.append(True)
        raise IntegrityError(
            f"synthetic statement: {private_marker}",
            {"private": private_marker},
            RuntimeError(private_marker),
        )

    # Patch only this test's Session and restore commit before retrying.
    with monkeypatch.context() as patch:
        patch.setattr(world.db, "commit", fail_commit)

        with pytest.raises(Conflict) as error:
            staff.change_status(
                ticket.id,
                TicketStatusChange(
                    status=TicketStatus.RESOLVED,
                    resolution_comment="Replaced the faulty power supply.",
                ),
            )

    assert commit_attempts == [True]
    assert error.value.code == "DATA_CONFLICT"
    assert error.value.status_code == 409
    assert str(error.value) == "The operation conflicts with existing data."
    assert private_marker not in str(error.value)

    current = staff.get(ticket.id)
    assert current.status == TicketStatus.IN_PROGRESS
    assert current.resolved_at is None
    assert current.closed_at is None
    assert world.comments().list(ticket.id) == []

    # The same Session must remain usable after rollback.
    resolved = staff.change_status(
        ticket.id,
        TicketStatusChange(
            status=TicketStatus.RESOLVED,
            resolution_comment="Replaced the faulty power supply.",
        ),
    )

    assert resolved.status == TicketStatus.RESOLVED
    assert resolved.resolved_at is not None

    comments = world.comments().list(ticket.id)
    assert len(comments) == 1
    assert comments[0].body == "Replaced the faulty power supply."
    assert comments[0].is_internal is False
