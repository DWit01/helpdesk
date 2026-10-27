import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_loads_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set a known value before creating a fresh Settings instance.
    database_url = "sqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", database_url)

    settings = Settings()

    assert settings.database_url == database_url


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Temporarily remove the value supplied by conftest.py.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as error:
        Settings()

    # Check the specific missing field, not just any validation failure.
    assert any(item["loc"] == ("database_url",) and item["type"] == "missing" for item in error.value.errors())
