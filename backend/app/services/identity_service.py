import sqlite3

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AccessDenied, AuthenticationRequired, Conflict
from app.core.security import TokenIdentity
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate


class IdentityService:
    """Resolve an already verified identity before endpoint business work."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._users = UserRepository(session)

    def resolve(self, identity: TokenIdentity) -> User:
        # Authentication runs first in the request. Refuse to commit unrelated
        # work if this service is accidentally called inside another transaction.
        if self._session.in_transaction():
            raise RuntimeError("Identity resolution must start before other database work.")

        profile = self._validated_profile(identity)
        creating = False

        try:
            user = self._users.get_by_keycloak_sub(profile.keycloak_sub)

            if user is None:
                creating = True
                return self._create(profile)

            return self._synchronise(user, profile)

        except IntegrityError as error:
            self._session.rollback()

            # Recover only from a recognised unique violation during creation.
            # A matching subject must also exist after rollback.
            if not creating or not self._is_unique_violation(error):
                raise self._conflict() from None

            return self._recover_creation_race(profile)

        except Exception:
            self._session.rollback()
            raise

    @staticmethod
    def _highest_role(roles: frozenset[str]) -> UserRole:
        for role in (UserRole.ADMIN, UserRole.AGENT, UserRole.REQUESTER):
            if role.value in roles:
                return role

        # Authentication succeeded, but no application role grants access.
        raise AccessDenied()

    @classmethod
    def _validated_profile(cls, identity: TokenIdentity) -> UserCreate:
        role = cls._highest_role(identity.roles)

        required_values = (
            identity.subject,
            identity.username,
            identity.email,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required_values):
            raise AuthenticationRequired("Invalid authentication token.")

        try:
            return UserCreate(
                keycloak_sub=identity.subject,
                username=identity.username,
                email=identity.email,
                full_name=identity.full_name,
                role=role,
            )
        except ValidationError:
            # Do not expose claim values or validation details.
            raise AuthenticationRequired("Invalid authentication token.") from None

    def _create(self, profile: UserCreate) -> User:
        user = User(
            keycloak_sub=profile.keycloak_sub,
            username=profile.username,
            email=profile.email,
            full_name=profile.full_name,
            role=profile.role,
            is_active=True,
        )
        self._users.add(user)
        self._session.commit()
        return user

    def _synchronise(self, user: User, profile: UserCreate) -> User:
        if not user.is_active:
            raise AccessDenied()

        unchanged = (
            user.username == profile.username
            and user.email == profile.email
            and user.full_name == profile.full_name
            and user.role == profile.role
        )

        if unchanged:
            return user

        # Preserve local identity, activation and relationships.
        user.username = profile.username
        user.email = profile.email
        user.full_name = profile.full_name
        user.role = profile.role

        self._users.save(user)
        self._session.commit()
        return user

    def _recover_creation_race(self, profile: UserCreate) -> User:
        """Reload the winning record after the losing insert was rolled back."""
        try:
            user = self._users.get_by_keycloak_sub(profile.keycloak_sub)

            if user is None:
                raise self._conflict()

            # Recheck activation and apply the same synchronisation policy.
            return self._synchronise(user, profile)

        except IntegrityError:
            self._session.rollback()
            raise self._conflict() from None

        except Exception:
            self._session.rollback()
            raise

    @staticmethod
    def _is_unique_violation(error: IntegrityError) -> bool:
        # Local SQLite support only. PostgreSQL compatibility remains unverified.
        code = getattr(error.orig, "sqlite_errorcode", None)
        return isinstance(code, int) and code == sqlite3.SQLITE_CONSTRAINT_UNIQUE

    @staticmethod
    def _conflict() -> Conflict:
        return Conflict(
            "IDENTITY_CONFLICT",
            "The current user could not be synchronised.",
        )
