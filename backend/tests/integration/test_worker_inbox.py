"""Шаг воркера для входящих: отправка, ответы и статус «молчит» вместе.

Данные здесь сохраняются в базу по-настоящему: каждый шаг воркера работает на своем соединении.
"""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.clock import ManualClock
from app.config import settings
from app.db import engine
from app.messenger import inbox_replay
from app.messenger.sender import StubSender
from app.models import Base, CampaignSupplier
from app.seed import load
from app.services.campaigns import create
from app.worker import receive, tick

DATA = settings.data_dir
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


@pytest.fixture(autouse=True)
def clean(schema):
    """После теста база снова пустая: остальные тесты работают в откатываемой транзакции и ждут ее такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def statuses():
    with Session(engine) as session:
        return dict(session.execute(select(CampaignSupplier.supplier_id, CampaignSupplier.status)).all())


def test_silence_and_late_reply():
    clock = ManualClock(NOW)
    with Session(engine) as session:
        load(session, DATA)
        create(session, clock, TEMPLATE, preset="inactive_new")  # c3: Kestrel Foods и Kestrel Foods EU
        session.commit()
    sender = StubSender()
    tick(clock, sender, random.Random(0))
    assert [chat_id for chat_id, _ in sender.sent] == ["c3"]
    scenario = inbox_replay.load(DATA)

    clock.set(NOW + timedelta(hours=71))
    receive(clock, scenario)
    assert statuses() == {"s3": "replied", "s4": "sent", "s6": "excluded"}

    clock.set(NOW + timedelta(hours=72))
    receive(clock, scenario)
    assert statuses() == {"s3": "replied", "s4": "silent", "s6": "excluded"}

    clock.set(NOW + timedelta(hours=110))
    receive(clock, scenario)
    assert statuses() == {"s3": "replied", "s4": "replied", "s6": "excluded"}
