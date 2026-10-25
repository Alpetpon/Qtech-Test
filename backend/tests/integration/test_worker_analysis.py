"""Шаг воркера для разбора ответов: отправка, приём и разбор вместе.

Данные здесь коммитятся по-настоящему: каждый шаг воркера работает на своём соединении.
"""

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.clock import ManualClock
from app.config import settings
from app.db import engine
from app.llm import LLMError
from app.llm.client import ReplayClient
from app.messenger import inbox_replay
from app.messenger.sender import StubSender
from app.models import Base, CampaignSupplier, Offer
from app.seed import load
from app.services.campaigns import create
from app.worker import analyze, receive, tick

DATA = settings.data_dir
FIXTURES = Path(__file__).parents[1] / "fixtures" / "llm"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


class Down:
    def complete(self, request):
        raise LLMError("Модель недоступна: таймаут")


@pytest.fixture(autouse=True)
def clean(schema):
    """После теста база снова пустая: остальные тесты работают в откатываемой транзакции и ждут её такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def status(supplier_id):
    with Session(engine) as session:
        return session.scalar(select(CampaignSupplier.status).where(CampaignSupplier.supplier_id == supplier_id))


def test_reply_is_analyzed_after_model_comes_back():
    clock = ManualClock(NOW)
    with Session(engine) as session:
        load(session, DATA)
        create(session, clock, TEMPLATE, preset="stale_price")  # c1 NordPack
        session.commit()
    tick(clock, StubSender(), random.Random(0))
    scenario = inbox_replay.load(DATA)

    clock.set(NOW + timedelta(hours=3))
    receive(clock, scenario)
    assert analyze(clock, Down()) is True
    assert status("s1") == "replied"

    assert analyze(clock, ReplayClient(FIXTURES)) is False
    assert status("s1") == "offer"
    with Session(engine) as session:
        assert session.scalars(select(Offer.product)).all() == ["Коробки 40x30x30"]
