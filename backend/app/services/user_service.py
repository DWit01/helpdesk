from uuid import UUID

from app.core.exceptions import NotFound
from app.models.user import UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserRead, UserUpdate
from app.services.base import Service


class UserService(Service):
    def me(self) -> UserRead:
        return UserRead.model_validate(self._actor())

    def list(self, *, role: UserRole | None = None, active_only: bool = True) -> list[UserRead]:
        actor = self._actor()
        self._require_staff(actor)
        users = UserRepository(self.session).list_users(role=role, active_only=active_only)
        return [UserRead.model_validate(user) for user in users]

    def update(self, user_id: UUID, data: UserUpdate) -> UserRead:
        with self._write():
            self._require_admin(self._actor())
            repository = UserRepository(self.session)
            user = repository.get_by_id(user_id)
            if user is None:
                raise NotFound("USER_NOT_FOUND", "User was not found.")

            user.is_active = data.is_active
            repository.save(user)
            result = UserRead.model_validate(user)

        return result
