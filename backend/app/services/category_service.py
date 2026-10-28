from app.core.exceptions import Conflict, NotFound
from app.models.category import Category
from app.repositories.category_repository import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services.base import Service


class CategoryService(Service):
    def _category(self, category_id: int) -> Category:
        category = CategoryRepository(self.session).get_by_id(category_id)
        if category is None:
            raise NotFound("CATEGORY_NOT_FOUND", "Category was not found.")
        return category

    def _check_name(self, name: str, *, excluding_id: int | None = None) -> None:
        existing = CategoryRepository(self.session).get_by_name(name)
        if existing is not None and existing.id != excluding_id:
            raise Conflict("CATEGORY_NAME_EXISTS", "A category with this name already exists.")

    def list(self, *, include_inactive: bool = False) -> list[CategoryRead]:
        actor = self._actor()
        repository = CategoryRepository(self.session)

        if include_inactive:
            self._require_admin(actor)
            categories = repository.list_all()
        else:
            categories = repository.list_active()

        return [CategoryRead.model_validate(category) for category in categories]

    def get(self, category_id: int) -> CategoryRead:
        self._actor()
        return CategoryRead.model_validate(self._category(category_id))

    def create(self, data: CategoryCreate) -> CategoryRead:
        with self._write():
            self._require_admin(self._actor())
            self._check_name(data.name)
            category = CategoryRepository(self.session).add(
                Category(name=data.name, description=data.description, is_active=True)
            )
            result = CategoryRead.model_validate(category)

        return result

    def update(self, category_id: int, data: CategoryUpdate) -> CategoryRead:
        with self._write():
            self._require_admin(self._actor())
            category = self._category(category_id)
            self._check_name(data.name, excluding_id=category.id)
            CategoryRepository(self.session).update(
                category,
                name=data.name,
                description=data.description,
                is_active=data.is_active,
            )
            result = CategoryRead.model_validate(category)

        return result

    def delete(self, category_id: int) -> None:
        with self._write():
            self._require_admin(self._actor())
            category = self._category(category_id)
            repository = CategoryRepository(self.session)

            if repository.is_used(category.id):
                raise Conflict("CATEGORY_IN_USE", "A used category can only be deactivated.")

            repository.delete(category)
