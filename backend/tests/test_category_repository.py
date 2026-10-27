from sqlalchemy.orm import Session

from app.models.category import Category
from app.repositories.category_repository import CategoryRepository


def test_get_by_id_returns_existing_category(db_session: Session) -> None:
    category = Category(name="Hardware")
    db_session.add(category)
    db_session.flush()

    repository = CategoryRepository(db_session)
    result = repository.get_by_id(category.id)
    assert result is not None
    assert result.id == category.id
    assert result.name == "Hardware"


def test_get_by_id_returns_none_for_missing_category(db_session: Session) -> None:
    repository = CategoryRepository(db_session)
    result = repository.get_by_id(999)
    assert result is None


def test_get_by_id_returns_inactive_category(db_session: Session) -> None:
    category = Category(name="Hardware", is_active=False)
    db_session.add(category)
    db_session.flush()

    repository = CategoryRepository(db_session)
    result = repository.get_by_id(category.id)
    assert result is not None
    assert result.id == category.id
    assert result.is_active is False


def test_add_assigns_id_and_returns_category(db_session: Session) -> None:
    category = Category(name="Networking")
    repository = CategoryRepository(db_session)
    result = repository.add(category)

    assert result is category
    assert result.id is not None


def test_add_does_not_commit(db_session: Session) -> None:
    category = Category(name="Networking")
    repository = CategoryRepository(db_session)
    repository.add(category)
    category_id = category.id
    db_session.rollback()
    result = repository.get_by_id(category_id)
    assert result is None


def test_list_all_returns_empty_list(db_session: Session) -> None:
    repository = CategoryRepository(db_session)
    result = repository.list_all()
    assert result == []


def test_list_all_orders_by_id_and_includes_inactive(db_session: Session) -> None:
    higher_id = Category(id=20, name="Hardware", is_active=True)
    lower_id = Category(id=10, name="Networking", is_active=False)
    db_session.add_all([higher_id, lower_id])
    db_session.flush()

    repository = CategoryRepository(db_session)
    result = repository.list_all()

    assert [category.id for category in result] == [10, 20]
    assert result[0].is_active is False
    assert result[1].is_active is True


def test_update_changes_category_fields(db_session: Session) -> None:
    repository = CategoryRepository(db_session)
    category = repository.add(Category(name="Hardware", description="Original description"))

    result = repository.update(
        category,
        name="Networking",
        description=None,
        is_active=False,
    )

    assert result is category

    # Reload from the database to verify that changes were flushed.
    db_session.refresh(category)

    assert category.name == "Networking"
    assert category.description is None
    assert category.is_active is False


def test_update_does_not_commit(db_session: Session) -> None:
    repository = CategoryRepository(db_session)
    category = repository.add(Category(name="Hardware", description="Original description"))

    # Commit the starting record so rollback only undoes the update.
    db_session.commit()

    repository.update(
        category,
        name="Networking",
        description=None,
        is_active=False,
    )

    db_session.rollback()
    db_session.refresh(category)

    assert category.name == "Hardware"
    assert category.description == "Original description"
    assert category.is_active is True


def test_list_active_excludes_inactive_and_orders_by_id(db_session: Session) -> None:
    db_session.add_all(
        [
            Category(id=30, name="Hardware", is_active=True),
            Category(id=20, name="Retired Support", is_active=False),
            Category(id=10, name="Networking", is_active=True),
        ]
    )
    db_session.flush()

    repository = CategoryRepository(db_session)

    result = repository.list_active()

    assert [category.id for category in result] == [10, 30]


def test_list_active_returns_empty_when_all_inactive(db_session: Session) -> None:
    db_session.add(Category(name="Retired Support", is_active=False))
    db_session.flush()

    repository = CategoryRepository(db_session)

    result = repository.list_active()

    assert result == []
