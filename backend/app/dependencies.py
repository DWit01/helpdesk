from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import AccessDenied, AuthenticationRequired
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository


def get_current_user(db: Annotated[Session, Depends(get_db)]) -> User:
    """Temporary local identity dependency, replaced by Keycloak in Week 4."""
    settings = Settings()

    # Fail closed outside local development and when not explicitly enabled.
    if settings.app_env != "local" or not settings.fake_auth_enabled:
        raise AuthenticationRequired("Local fake authentication is disabled.")

    if settings.fake_current_user_id is None:
        raise AuthenticationRequired("No local fake user is configured.")

    user = UserRepository(db).get_by_id(settings.fake_current_user_id)
    if user is None:
        raise AuthenticationRequired()

    if not user.is_active:
        raise AccessDenied()

    return user
