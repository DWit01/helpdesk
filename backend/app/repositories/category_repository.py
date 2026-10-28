from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.ticket import Ticket


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

    def get_by_name(self, name: str) -> Category | None:
        statement = select(Category).where(Category.name == name)
        return self._session.scalars(statement).one_or_none()

    def is_used(self, category_id: int) -> bool:
        # Deleted tickets still retain their category foreign key.
        statement = select(Ticket.id).where(Ticket.category_id == category_id).limit(1)
        return self._session.scalar(statement) is not None

    def delete(self, category: Category) -> None:
        self._session.delete(category)
        self._session.flush()
