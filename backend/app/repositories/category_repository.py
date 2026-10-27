from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category


class CategoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, category_id: int) -> Category | None:
        return self._session.get(Category, category_id)

    def add(self, category: Category) -> Category:
        self._session.add(category)
        self._session.flush()
        return category

    def list_all(self) -> list[Category]:
        statement = select(Category).order_by(Category.id)
        categories = self._session.scalars(statement)
        return list(categories)

    def update(self, category: Category, *, name: str, description: str | None, is_active: bool) -> Category:
        category.name = name
        category.description = description
        category.is_active = is_active

        self._session.flush()

        return category

    def list_active(self) -> list[Category]:
        statement = select(Category).where(Category.is_active.is_(True)).order_by(Category.id)
        return list(self._session.scalars(statement))
