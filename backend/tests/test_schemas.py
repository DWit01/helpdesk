from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models import Category, Comment, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.pagination import Page, PageParams
from app.schemas.ticket import TicketAssign, TicketCreate, TicketRead, TicketStatusChange, TicketUpdate
from app.schemas.user import UserCreate, UserRead, UserUpdate

VALID_TICKET: dict[str, Any] = {
    "title": "Laptop screen flickers",
    "description": "The screen flickers when connected to an external monitor.",
    "category_id": 1,
}

VALID_USER: dict[str, Any] = {
    "keycloak_sub": "example-subject",
    "username": "alice",
    "email": "alice@example.invalid",
    "role": "requester",
}


@pytest.mark.parametrize(
    ("schema", "payload", "field", "minimum", "maximum"),
    [
        (CategoryCreate, {"name": "Hardware"}, "name", 2, 60),
        (CategoryCreate, {"name": "Hardware"}, "description", 0, 255),
        (CategoryUpdate, {"name": "Hardware", "is_active": True}, "name", 2, 60),
        (CategoryUpdate, {"name": "Hardware", "is_active": True}, "description", 0, 255),
        (TicketCreate, VALID_TICKET, "title", 5, 120),
        (TicketCreate, VALID_TICKET, "description", 10, 5000),
        (TicketUpdate, {}, "title", 5, 120),
        (TicketUpdate, {}, "description", 10, 5000),
        (CommentCreate, {}, "body", 1, 2000),
        (CommentUpdate, {}, "body", 1, 2000),
        (UserCreate, VALID_USER, "keycloak_sub", 1, 255),
        (UserCreate, VALID_USER, "username", 1, 50),
        (UserCreate, VALID_USER, "email", 1, 255),
        (UserCreate, VALID_USER, "full_name", 0, 255),
        (TicketStatusChange, {"status": "RESOLVED"}, "resolution_comment", 1, 2000),
    ],
)
@pytest.mark.parametrize("boundary", ["minimum", "maximum", "below", "above"])
def test_string_length_boundaries(
    schema: type[BaseModel],
    payload: dict[str, Any],
    field: str,
    minimum: int,
    maximum: int,
    boundary: str,
) -> None:
    lengths = {
        "minimum": minimum,
        "maximum": maximum,
        "below": minimum - 1,
        "above": maximum + 1,
    }
    length = lengths[boundary]

    if length < 0:
        pytest.skip("A string cannot have a negative length")

    data = {**payload, field: "x" * length}

    if minimum <= length <= maximum:
        result = schema.model_validate(data)
        assert getattr(result, field) == data[field]
    else:
        with pytest.raises(ValidationError):
            schema.model_validate(data)


def test_create_defaults() -> None:
    category = CategoryCreate(name="Hardware")
    ticket = TicketCreate.model_validate(VALID_TICKET)
    comment = CommentCreate(body="Please restart the laptop.")

    assert category.description is None
    assert ticket.priority is TicketPriority.MEDIUM
    assert comment.is_internal is False


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (CategoryCreate, {}),
        (CategoryUpdate, {"name": "Hardware"}),
        (CategoryUpdate, {"is_active": True}),
        (TicketCreate, {"title": "Valid title", "category_id": 1}),
        (UserCreate, {"username": "alice"}),
        (UserUpdate, {}),
        (CommentCreate, {}),
        (CommentUpdate, {}),
        (TicketAssign, {}),
        (TicketStatusChange, {}),
    ],
)
def test_required_fields(schema: type[BaseModel], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize(
    ("schema", "payload", "field", "value"),
    [
        (CategoryCreate, {"name": "Hardware"}, "id", 99),
        (CategoryCreate, {"name": "Hardware"}, "is_active", False),
        (TicketCreate, VALID_TICKET, "requester_id", str(uuid4())),
        (TicketCreate, VALID_TICKET, "ticket_number", "HD-000099"),
        (TicketCreate, VALID_TICKET, "status", "CLOSED"),
        (TicketCreate, VALID_TICKET, "created_at", "2026-10-28T10:00:00Z"),
        (TicketUpdate, {"title": "Updated title"}, "status", "RESOLVED"),
        (TicketUpdate, {"title": "Updated title"}, "assignee_id", str(uuid4())),
        (UserUpdate, {"is_active": False}, "role", "admin"),
        (CommentCreate, {"body": "A valid comment"}, "author_id", str(uuid4())),
        (CommentUpdate, {"body": "Updated comment"}, "is_internal", True),
    ],
)
def test_unexpected_and_protected_fields_rejected(
    schema: type[BaseModel],
    payload: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, field: value})


def test_category_description_can_be_null() -> None:
    created = CategoryCreate(name="Hardware", description=None)
    updated = CategoryUpdate(name="Hardware", description=None, is_active=False)

    assert created.description is None
    assert updated.description is None
    assert updated.is_active is False


def test_category_put_omitted_description_clears_it() -> None:
    changes = CategoryUpdate(name="Hardware", is_active=True).model_dump()

    assert changes == {"name": "Hardware", "description": None, "is_active": True}


def test_ticket_patch_contains_only_supplied_changes() -> None:
    changes = TicketUpdate(title="Updated ticket title").model_dump(exclude_unset=True)

    assert changes == {"title": "Updated ticket title"}


@pytest.mark.parametrize("field", ["title", "description", "category_id", "priority"])
def test_ticket_patch_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        TicketUpdate.model_validate({field: None})


def test_empty_ticket_patch_rejected() -> None:
    with pytest.raises(ValidationError):
        TicketUpdate.model_validate({})


@pytest.mark.parametrize("category_id", [0, -1])
def test_category_id_must_be_positive(category_id: int) -> None:
    with pytest.raises(ValidationError):
        TicketCreate.model_validate({**VALID_TICKET, "category_id": category_id})

    with pytest.raises(ValidationError):
        TicketUpdate(category_id=category_id)


def test_assignment_accepts_uuid_or_explicit_null() -> None:
    user_id = uuid4()

    assert TicketAssign.model_validate({"assignee_id": str(user_id)}).assignee_id == user_id
    assert TicketAssign(assignee_id=None).assignee_id is None


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (TicketCreate, {**VALID_TICKET, "priority": "URGENT"}),
        (TicketStatusChange, {"status": "FINISHED"}),
        (UserCreate, {**VALID_USER, "role": "superuser"}),
        (TicketAssign, {"assignee_id": "not-a-uuid"}),
    ],
)
def test_invalid_enum_and_uuid_values(schema: type[BaseModel], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_valid_enum_values() -> None:
    ticket = TicketCreate.model_validate({**VALID_TICKET, "priority": "HIGH"})
    change = TicketStatusChange(status=TicketStatus.RESOLVED, resolution_comment="Replaced the monitor.")
    user = UserCreate.model_validate(VALID_USER)

    assert ticket.priority is TicketPriority.HIGH
    assert change.status is TicketStatus.RESOLVED
    assert user.role is UserRole.REQUESTER


@pytest.fixture
def stored_records(db_session: Session) -> tuple[Category, User, Ticket, Comment]:
    category = Category(name="Hardware")
    user = User(
        keycloak_sub="schema-test-user",
        username="alice",
        email="alice@example.invalid",
        role=UserRole.REQUESTER,
    )
    db_session.add_all([category, user])
    db_session.flush()

    ticket = Ticket(
        ticket_number="HD-000001",
        title="Laptop screen flickers",
        description="The laptop screen flickers when an external monitor is connected.",
        category=category,
        requester=user,
    )
    db_session.add(ticket)
    db_session.flush()

    comment = Comment(ticket=ticket, author=user, body="Please try another cable.")
    db_session.add(comment)
    db_session.flush()

    return category, user, ticket, comment


def test_read_schemas_accept_orm_objects(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    category, user, ticket, comment = stored_records

    category_read = CategoryRead.model_validate(category)
    user_read = UserRead.model_validate(user)
    ticket_read = TicketRead.model_validate(ticket)
    comment_read = CommentRead.model_validate(comment)

    assert category_read.id == category.id
    assert category_read.description is None
    assert user_read.id == user.id
    assert ticket_read.category.id == category.id
    assert ticket_read.requester.id == user.id
    assert ticket_read.assignee is None
    assert ticket_read.resolved_at is None
    assert ticket_read.closed_at is None
    assert comment_read.ticket_id == ticket.id
    assert comment_read.author_id == user.id


def test_assigned_ticket_has_nested_user_summary(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    _, user, ticket, _ = stored_records
    ticket.assignee = user

    response = TicketRead.model_validate(ticket)

    assert response.assignee is not None
    assert response.assignee.id == user.id
    assert response.assignee.username == "alice"


def test_json_output_excludes_internal_fields(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    _, user, ticket, _ = stored_records

    user_data = UserRead.model_validate(user).model_dump(mode="json")
    ticket_data = TicketRead.model_validate(ticket).model_dump(mode="json")

    assert user_data["id"] == str(user.id)
    assert user_data["role"] == "requester"
    assert "keycloak_sub" not in user_data
    assert "deleted_at" not in ticket_data
    assert ticket_data["category"] == {"id": ticket.category_id, "name": "Hardware"}
    assert ticket_data["requester"] == {"id": str(user.id), "username": "alice"}


def test_read_description_required_but_nullable(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    category, _, _, _ = stored_records
    data = CategoryRead.model_validate(category).model_dump()
    del data["description"]

    with pytest.raises(ValidationError):
        CategoryRead.model_validate(data)


def test_read_timestamps_normalised_to_utc(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    category, _, _, _ = stored_records
    data = CategoryRead.model_validate(category).model_dump()
    data["created_at"] = datetime(2026, 10, 28, 12, 0, tzinfo=timezone(timedelta(hours=2)))

    response = CategoryRead.model_validate(data)

    assert response.created_at == datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    assert response.created_at.utcoffset() == timedelta(0)


def test_naive_read_timestamp_rejected(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    category, _, _, _ = stored_records
    data = CategoryRead.model_validate(category).model_dump()
    data["created_at"] = datetime(2026, 10, 28, 10, 0)

    with pytest.raises(ValidationError):
        CategoryRead.model_validate(data)


@pytest.mark.parametrize(
    ("payload", "expected_pages"),
    [
        ({"items": [], "total": 0}, 0),
        ({"items": [1], "total": 1}, 1),
        ({"items": list(range(20)), "total": 20}, 1),
        ({"items": list(range(20)), "total": 21}, 2),
        ({"items": [1], "total": 135}, 7),
        ({"items": [], "total": 1, "page": 2}, 1),
    ],
)
def test_page_count(payload: dict[str, Any], expected_pages: int) -> None:
    result = Page[int].model_validate(payload)

    assert result.pages == expected_pages
    assert result.model_dump()["pages"] == expected_pages


def test_pagination_defaults() -> None:
    params = PageParams()

    assert params.page == 1
    assert params.page_size == 20


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 0},
        {"page_size": 101},
    ],
)
def test_invalid_page_parameters(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PageParams.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "total": -1},
        {"items": [1, 2], "total": 2, "page_size": 1},
        {"items": [1], "total": 0},
        {"items": [1], "total": 1, "page": 2},
        {"items": ["not-an-integer"], "total": 1},
    ],
)
def test_invalid_page_contents(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Page[int].model_validate(payload)


def test_page_validates_nested_orm_items(stored_records: tuple[Category, User, Ticket, Comment]) -> None:
    category, _, _, _ = stored_records
    result = Page[CategoryRead].model_validate({"items": [category], "total": 1})

    assert isinstance(result.items[0], CategoryRead)
    assert result.items[0].id == category.id
    assert result.model_dump(mode="json")["pages"] == 1
