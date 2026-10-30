from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AccessDenied, AuthenticationRequired, Conflict
from app.core.security import TokenIdentity
from app.db.base import Base
from app.db.session import build_engine
from app.models import Category, Ticket, User, UserRole
from app.repositories.user_repository import UserRepository
from app.services.identity_service import IdentityService


@pytest.fixture
def sessions(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    database = (tmp_path / "identity-tests.sqlite3").as_posix()
    engine = build_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)

    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


@pytest.fixture
def identity() -> TokenIdentity:
    return TokenIdentity(
        subject="alice-subject",
        username="alice",
        email="alice@example.test",
        roles=frozenset({"requester"}),
        full_name="Alice Requester",
    )


def seed_user(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    *,
    role: UserRole = UserRole.REQUESTER,
    active: bool = True,
) -> UUID:
    with sessions() as db:
        user = User(
            keycloak_sub=identity.subject,
            username=identity.username,
            email=identity.email,
            full_name=identity.full_name,
            role=role,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user.id


def user_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as db:
        return int(db.scalar(select(func.count()).select_from(User)) or 0)


def test_first_login_is_committed_and_repeat_login_reuses_user(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    with sessions() as db:
        first = IdentityService(db).resolve(identity)
        user_id = first.id
        created_at = first.created_at
        assert first.role == UserRole.REQUESTER
        assert first.is_active is True

    with sessions() as db:
        second = IdentityService(db).resolve(identity)
        assert second.id == user_id
        assert second.created_at == created_at

    assert user_count(sessions) == 1


def test_unchanged_profile_does_not_commit(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = seed_user(sessions, identity)

    def unexpected_commit() -> None:
        raise AssertionError("An unchanged profile must not commit.")

    with sessions() as db:
        monkeypatch.setattr(db, "commit", unexpected_commit)
        user = IdentityService(db).resolve(identity)

        assert user.id == user_id
        assert not db.dirty


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (frozenset({"requester"}), UserRole.REQUESTER),
        (frozenset({"agent"}), UserRole.AGENT),
        (frozenset({"admin"}), UserRole.ADMIN),
        (frozenset({"requester", "agent"}), UserRole.AGENT),
        (
            frozenset({"requester", "agent", "admin", "offline_access"}),
            UserRole.ADMIN,
        ),
    ],
)
def test_highest_recognised_role_is_selected(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    roles: frozenset[str],
    expected: UserRole,
) -> None:
    with sessions() as db:
        user = IdentityService(db).resolve(replace(identity, roles=roles))
        assert user.role == expected


@pytest.mark.parametrize(
    "roles",
    [
        frozenset(),
        frozenset({"offline_access", "uma_authorization"}),
        frozenset({"ADMIN"}),
    ],
)
def test_no_application_role_denies_access_without_creation(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    roles: frozenset[str],
) -> None:
    with sessions() as db:
        with pytest.raises(AccessDenied):
            IdentityService(db).resolve(replace(identity, roles=roles))

    assert user_count(sessions) == 0


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (UserRole.REQUESTER, UserRole.ADMIN),
        (UserRole.ADMIN, UserRole.REQUESTER),
    ],
)
def test_profile_and_role_updates_preserve_local_identity(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    before: UserRole,
    after: UserRole,
) -> None:
    user_id = seed_user(sessions, identity, role=before)
    changed = replace(
        identity,
        username="alice-updated",
        email="alice-updated@example.test",
        full_name="Alice Updated",
        roles=frozenset({after.value}),
    )

    with sessions() as db:
        user = IdentityService(db).resolve(changed)
        assert user.id == user_id
        assert user.keycloak_sub == identity.subject
        assert user.username == changed.username
        assert user.email == changed.email
        assert user.full_name == changed.full_name
        assert user.role == after

    assert user_count(sessions) == 1


def test_missing_optional_name_clears_the_local_copy(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    user_id = seed_user(sessions, identity)

    with sessions() as db:
        user = IdentityService(db).resolve(replace(identity, full_name=None))
        assert user.id == user_id
        assert user.full_name is None


def test_inactive_user_is_not_reactivated_or_synchronised(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    user_id = seed_user(sessions, identity, active=False)
    changed = replace(
        identity,
        username="changed",
        roles=frozenset({"admin"}),
    )

    with sessions() as db:
        with pytest.raises(AccessDenied):
            IdentityService(db).resolve(changed)

    with sessions() as db:
        user = db.get(User, user_id)
        assert user is not None
        assert user.is_active is False
        assert user.username == identity.username
        assert user.role == UserRole.REQUESTER


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", ""),
        ("subject", "   "),
        ("subject", "s" * 256),
        ("username", ""),
        ("username", "   "),
        ("username", "u" * 51),
        ("email", ""),
        ("email", "   "),
        ("email", "e" * 256),
        ("full_name", "n" * 256),
    ],
)
def test_invalid_profile_is_rejected_without_creation(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    field: str,
    value: str,
) -> None:
    # Name each field explicitly so the type checker can verify its type.
    invalid_profiles = {
        "subject": replace(identity, subject=value),
        "username": replace(identity, username=value),
        "email": replace(identity, email=value),
        "full_name": replace(identity, full_name=value),
    }
    invalid = invalid_profiles[field]

    with sessions() as db:
        with pytest.raises(AuthenticationRequired):
            IdentityService(db).resolve(invalid)

    assert user_count(sessions) == 0


def test_profile_accepts_existing_storage_boundaries(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    boundary = replace(
        identity,
        subject="s" * 255,
        username="u" * 50,
        email="e" * 255,
        full_name="n" * 255,
    )

    with sessions() as db:
        user = IdentityService(db).resolve(boundary)
        assert user.keycloak_sub == boundary.subject
        assert user.username == boundary.username


def test_new_subject_does_not_relink_an_account_by_email(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    original_id = seed_user(sessions, identity)

    with sessions() as db:
        different = IdentityService(db).resolve(replace(identity, subject="different-keycloak-subject"))
        assert different.id != original_id
        assert different.email == identity.email

    assert user_count(sessions) == 2


def test_synchronisation_preserves_ticket_ownership(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    user_id = seed_user(sessions, identity)

    with sessions() as db:
        category = Category(name="Hardware", is_active=True)
        db.add(category)
        db.flush()
        ticket = Ticket(
            ticket_number="HD-000001",
            title="Laptop problem",
            description="The laptop cannot start successfully.",
            category_id=category.id,
            requester_id=user_id,
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id

    with sessions() as db:
        user = IdentityService(db).resolve(replace(identity, username="alice-renamed"))
        assert user.id == user_id

    with sessions() as db:
        reloaded_ticket = db.get(Ticket, ticket_id)
        assert reloaded_ticket is not None
        assert reloaded_ticket.requester_id == user_id


@pytest.mark.parametrize("winner_active", [True, False])
def test_creation_race_reloads_the_unique_winner(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    monkeypatch: pytest.MonkeyPatch,
    winner_active: bool,
) -> None:
    original_add = UserRepository.add
    winner_ids: list[UUID] = []

    def competing_add(repository: UserRepository, user: User) -> User:
        # Deterministically insert a winner between lookup and losing flush.
        with sessions() as winner_db:
            winner = User(
                keycloak_sub=user.keycloak_sub,
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                is_active=winner_active,
            )
            winner_db.add(winner)
            winner_db.commit()
            winner_ids.append(winner.id)

        # This real insert now violates the unique subject constraint.
        return original_add(repository, user)

    monkeypatch.setattr(UserRepository, "add", competing_add)

    with sessions() as db:
        service = IdentityService(db)

        if winner_active:
            user = service.resolve(identity)
            assert user.id == winner_ids[0]
        else:
            with pytest.raises(AccessDenied):
                service.resolve(identity)

        assert db.is_active

    assert user_count(sessions) == 1

    with sessions() as db:
        winner = db.get(User, winner_ids[0])
        assert winner is not None
        assert winner.is_active is winner_active


def test_unrelated_integrity_failure_is_not_treated_as_a_race(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_add(_repository: UserRepository, _user: User) -> User:
        raise IntegrityError(
            "synthetic insert",
            {},
            RuntimeError("synthetic unrelated constraint failure"),
        )

    with sessions() as db:
        service = IdentityService(db)

        with monkeypatch.context() as patch:
            patch.setattr(UserRepository, "add", fail_add)

            with pytest.raises(Conflict) as error:
                service.resolve(identity)

            assert error.value.code == "IDENTITY_CONFLICT"
            assert db.is_active

        # The same Session remains usable after recovery.
        user = service.resolve(identity)
        assert user.keycloak_sub == identity.subject

    assert user_count(sessions) == 1


def test_failed_creation_commit_rolls_back_and_allows_retry(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_commit() -> None:
        raise RuntimeError("synthetic commit failure")

    with sessions() as db:
        service = IdentityService(db)

        with monkeypatch.context() as patch:
            patch.setattr(db, "commit", fail_commit)

            with pytest.raises(RuntimeError, match="synthetic commit failure"):
                service.resolve(identity)

        assert db.is_active

        # Resolve again before any other query starts a new transaction.
        user = service.resolve(identity)
        assert user.keycloak_sub == identity.subject

    assert user_count(sessions) == 1


def test_failed_update_rolls_back_existing_profile(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = seed_user(sessions, identity)

    def fail_save(_repository: UserRepository, _user: User) -> User:
        raise RuntimeError("synthetic save failure")

    with sessions() as db:
        with monkeypatch.context() as patch:
            patch.setattr(UserRepository, "save", fail_save)

            with pytest.raises(RuntimeError, match="synthetic save failure"):
                IdentityService(db).resolve(
                    replace(
                        identity,
                        username="should-not-persist",
                        roles=frozenset({"admin"}),
                    )
                )

        assert db.is_active

    with sessions() as db:
        user = db.get(User, user_id)
        assert user is not None
        assert user.username == identity.username
        assert user.role == UserRole.REQUESTER


def test_unrelated_pending_work_is_not_committed(
    sessions: sessionmaker[Session],
    identity: TokenIdentity,
) -> None:
    with sessions() as db:
        category = Category(name="Uncommitted category", is_active=True)
        db.add(category)

        with pytest.raises(RuntimeError, match="before other database work"):
            IdentityService(db).resolve(identity)

        assert category in db.new
        db.rollback()

    assert user_count(sessions) == 0
