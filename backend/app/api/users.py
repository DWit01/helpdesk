from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import Users
from app.models.user import UserRole
from app.schemas.user import UserRead, UserUpdate

router = APIRouter(tags=["Users"])


@router.get("/me", summary="Get the current user")
def get_me(service: Users) -> UserRead:
    """Return the existing authenticated local user without exposing identity internals."""
    return service.me()


@router.get("/users", summary="List users")
def list_users(
    service: Users,
    role: UserRole | None = None,
    active_only: bool = True,
) -> list[UserRead]:
    """Allow staff to list users, optionally filtering role and activation."""
    return service.list(role=role, active_only=active_only)


@router.patch("/users/{user_id}", summary="Change user activation")
def update_user(
    user_id: UUID,
    data: UserUpdate,
    service: Users,
) -> UserRead:
    """Allow an admin to activate or deactivate an existing user."""
    return service.update(user_id, data)
