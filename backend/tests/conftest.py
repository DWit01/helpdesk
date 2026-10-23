import os

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:
    # Set safe configuration before pytest collects test modules.
    # Override any inherited development database URL.
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
