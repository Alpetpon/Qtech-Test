from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_clock, get_session
from app.clock import ManualClock
from app.db import engine
from app.main import app


@pytest.fixture
def clock():
    """Часы теста: стоят на времени, указанном в задании, пока тест их не переставит."""
    return ManualClock(datetime(2026, 11, 1, 10, tzinfo=UTC))


@pytest.fixture
def client(session, clock):
    """API на сессии и часах теста: всё, что сделали запросы, откатится вместе с тестом."""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def schema():
    """Схема поднимается миграциями один раз за прогон.

    Без alembic.ini: его fileConfig снял бы обработчики логов pytest.
    """
    config = Config()
    config.set_main_option("script_location", "migrations")
    command.upgrade(config, "head")


@pytest.fixture
def session(schema):
    """Сессия на test-db: всё, что сделал тест, откатывается после него."""
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
