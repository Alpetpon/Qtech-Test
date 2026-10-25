import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import engine
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


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
