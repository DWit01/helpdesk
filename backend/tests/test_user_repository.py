from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository


def test_get_by_id_returns_existing_user(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-subject",
        username="test",
        email="test@example.com",
        role=UserRole.REQUESTER,
    )
    db_session.add(user)
    db_session.flush()
    user_id = user.id

    # Remove tracked objects so the lookup must read from the database.
    db_session.expunge_all()

    repository = UserRepository(db_session)
    result = repository.get_by_id(user_id)

    assert result is not None
    assert result.id == user_id
    assert result.username == "test"


def test_get_by_id_returns_none_for_missing_user(db_session: Session) -> None:
    repository = UserRepository(db_session)

    result = repository.get_by_id(uuid4())

    assert result is None


def test_get_by_keycloak_sub_returns_matching_user(db_session: Session) -> None:
    alice = User(
        keycloak_sub="test-alice-subject",
        username="alice",
        email="alice@example.com",
        role=UserRole.REQUESTER,
    )
    bob = User(
        keycloak_sub="test-bob-subject",
        username="bob",
        email="bob@example.com",
        role=UserRole.AGENT,
    )
    db_session.add_all([alice, bob])
    db_session.flush()
    bob_id = bob.id
    db_session.expunge_all()

    repository = UserRepository(db_session)
    result = repository.get_by_keycloak_sub("test-bob-subject")

    assert result is not None
    assert result.id == bob_id
    assert result.keycloak_sub == "test-bob-subject"


def test_get_by_keycloak_sub_returns_none_for_missing_user(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-alice-subject",
        username="alice",
        email="alice@example.com",
        role=UserRole.REQUESTER,
    )
    db_session.add(user)
    db_session.flush()

    repository = UserRepository(db_session)
    result = repository.get_by_keycloak_sub("unknown-subject")

    assert result is None


def test_add_assigns_uuid_and_inserts_user(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-alice-subject",
        username="alice",
        email="alice@example.com",
        role=UserRole.REQUESTER,
    )
    repository = UserRepository(db_session)

    result = repository.add(user)

    assert result is user
    assert isinstance(result.id, UUID)

    user_id = result.id
    db_session.expunge_all()
    saved_user = db_session.get(User, user_id)

    assert saved_user is not None
    assert saved_user.keycloak_sub == "test-alice-subject"


def test_add_does_not_commit(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-alice-subject",
        username="alice",
        email="alice@example.com",
        role=UserRole.REQUESTER,
    )
    repository = UserRepository(db_session)
    repository.add(user)
    user_id = user.id

    db_session.rollback()

    assert db_session.get(User, user_id) is None


def test_get_by_id_returns_inactive_user(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-inactive-subject",
        username="inactive_user",
        email="inactive@example.com",
        role=UserRole.REQUESTER,
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()
    user_id = user.id
    db_session.expunge_all()

    repository = UserRepository(db_session)
    result = repository.get_by_id(user_id)

    assert result is not None
    assert result.id == user_id
    assert result.is_active is False


def test_get_by_keycloak_sub_returns_inactive_user(db_session: Session) -> None:
    user = User(
        keycloak_sub="test-inactive-subject",
        username="inactive_user",
        email="inactive@example.com",
        role=UserRole.REQUESTER,
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()
    user_id = user.id
    db_session.expunge_all()

    repository = UserRepository(db_session)
    result = repository.get_by_keycloak_sub("test-inactive-subject")

    assert result is not None
    assert result.id == user_id
    assert result.is_active is False
