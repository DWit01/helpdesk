from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, UserRole


class UserRepository:
    def __init__(self, session: Session) -> None:
        # Keep the Session supplied by the caller.
        self._session = session

    def get_by_id(self, user_id: UUID) -> User | None:
        # Retrieve by primary key; return None when no user matches.
        return self._session.get(User, user_id)

    def get_by_keycloak_sub(self, keycloak_sub: str) -> User | None:
        # Build a query matching the supplied Keycloak subject.
        statement = select(User).where(User.keycloak_sub == keycloak_sub)

        # Return the matching user, or None when no match exists.
        return self._session.scalars(statement).one_or_none()

    def add(self, user: User) -> User:
        # Register the user with the supplied Session.
        self._session.add(user)

        # Send the pending insert to the database without committing.
        self._session.flush()

        return user

    def list_users(self, *, role: UserRole | None = None, active_only: bool = True) -> list[User]:
        statement = select(User)

        if role is not None:
            statement = statement.where(User.role == role)

        if active_only:
            statement = statement.where(User.is_active.is_(True))

        statement = statement.order_by(User.username, User.id)
        return list(self._session.scalars(statement))

    def save(self, user: User) -> User:
        self._session.flush()
        return user
