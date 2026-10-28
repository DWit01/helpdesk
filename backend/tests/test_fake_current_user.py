from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import AccessDenied, AuthenticationRequired
from app.dependencies import get_current_user
from app.models import User, UserRole


@pytest.fixture
def local_user(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> User:
    user = User(
        keycloak_sub="fake-dependency-test",
        username="local-agent",
        email="local-agent@example.invalid",
        role=UserRole.AGENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "true")
    monkeypatch.setenv("FAKE_CURRENT_USER_ID", str(user.id))
    return user


def test_fake_dependency_returns_configured_database_user(db_session: Session, local_user: User) -> None:
    current = get_current_user(db_session)
    assert current.id == local_user.id
    assert current.role == UserRole.AGENT


@pytest.mark.parametrize("environment", ["development", "production"])
def test_fake_dependency_rejects_nonlocal_environment(
    db_session: Session,
    local_user: User,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    with pytest.raises(AuthenticationRequired):
        get_current_user(db_session)


def test_fake_dependency_requires_explicit_enablement(
    db_session: Session, local_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AUTH_ENABLED", "false")
    with pytest.raises(AuthenticationRequired):
        get_current_user(db_session)


def test_fake_dependency_requires_configured_user(
    db_session: Session, local_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_CURRENT_USER_ID")
    with pytest.raises(AuthenticationRequired):
        get_current_user(db_session)


def test_fake_dependency_rejects_unknown_user(
    db_session: Session, local_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CURRENT_USER_ID", str(uuid4()))
    with pytest.raises(AuthenticationRequired):
        get_current_user(db_session)


def test_fake_dependency_rejects_inactive_user(db_session: Session, local_user: User) -> None:
    local_user.is_active = False
    db_session.commit()

    with pytest.raises(AccessDenied):
        get_current_user(db_session)
