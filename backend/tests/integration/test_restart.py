"""Перезапуск воркера и два воркера сразу: сообщение не должно уходить в чат дважды.

Данные здесь сохраняются в базу по-настоящему: каждый воркер работает на своих соединениях и должен видеть ту же очередь.
"""

import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import exists, func, select, text, update
from sqlalchemy.orm import Session

from app.clock import ManualClock
from app.db import engine
from app.models import Base, Campaign, CampaignSupplier, Chat, OutboxMessage, SenderState, Supplier
from app.services import sending
from app.services.campaigns import create, preview
from app.services.sending import LOCK, ready_at
from app.worker import start, tick

NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"
UNKNOWN = "неизвестно, ушло ли сообщение рассылки №{}, — проверьте чат"


class Crash(Exception):
    """Процесс упал: сообщение уже ушло, а записать результат не успели."""


class Messenger:
    """Заглушка отправителя: помнит каждый вызов. crash — упасть сразу после отправки."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.crash = False

    def send(self, chat_id: str, text: str) -> None:
        self.calls.append(chat_id)
        # Отправка не мгновенная: второй воркер успевает постучаться посреди нее
        time.sleep(0.001)
        if self.crash:
            raise Crash


@pytest.fixture(autouse=True)
def clean(schema):
    """После теста база снова пустая, остальные тесты работают в откатываемой транзакции и ждут ее такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def clock():
    return ManualClock(NOW)


@pytest.fixture
def rng():
    return random.Random(10)


@pytest.fixture
def no_pause(monkeypatch):
    # Здесь проверяем не паузу, а то, что двое не шлют одно и то же: без паузы воркеры толкаются на каждом сообщении
    monkeypatch.setattr(sending, "PAUSE_SECONDS", (0, 0))


def enqueue(clock, chats, first=0):
    """Новые чаты first… с одним активным поставщиком без прайса и рассылка по всем активным."""
    with Session(engine) as session:
        for number in range(first, first + chats):
            supplier = Supplier(
                id=f"sr{number:03}",
                name=f"Поставщик {number:03}",
                status="active",
                manager="Анна",
                price_updated_at=None,
                last_activity_at=None,
            )
            session.add(
                Chat(
                    id=f"cr{number:03}",
                    title=f"Чат {number:03}",
                    enabled=True,
                    member=True,
                    last_outbound_at=None,
                    suppliers=[supplier],
                )
            )
        session.flush()
        campaign = create(session, clock, TEMPLATE, status="active")
        session.commit()
        return campaign.id


def crash(clock, rng, messenger):
    """Шаг, на котором процесс падает после отправки. Воркер закрывает соединение, с ним уходит блокировка."""
    messenger.crash = True
    with pytest.raises(Crash):
        tick(clock, messenger, rng)
    messenger.crash = False


def drain(clock, messenger, rng):
    """Один воркер отправляет всю очередь; часы каждый раз переводятся на момент, когда отправка разрешена."""
    while has_pending():
        with Session(engine) as session:
            clock.set(ready_at(session, clock.now()))
        tick(clock, messenger, rng)


def run_workers(clock, messenger, count=2):
    """Воркеры параллельно, пока очередь не опустеет или не упрётся в лимит. Часы стоят."""

    def work(seed):
        rng = random.Random(seed)
        while has_pending():
            with Session(engine) as session:
                if ready_at(session, clock.now()) > clock.now():
                    return
            tick(clock, messenger, rng)

    with ThreadPoolExecutor(count) as pool:
        for future in [pool.submit(work, seed) for seed in range(count)]:
            future.result()


def has_pending():
    with Session(engine) as session:
        return session.scalar(select(exists().where(OutboxMessage.status == "pending")))


def outbox():
    with Session(engine) as session:
        rows = session.execute(select(OutboxMessage.chat_id, OutboxMessage.status).order_by(OutboxMessage.id))
        return dict(rows.all())


def snapshot(campaign_id):
    with Session(engine) as session:
        rows = session.scalars(select(CampaignSupplier).where(CampaignSupplier.campaign_id == campaign_id))
        return {row.supplier_id: (row.status, row.reasons) for row in rows}


def excluded(clock):
    with Session(engine) as session:
        draft = preview(session, clock, TEMPLATE, status="active")
    return {item.supplier.id: list(item.reasons) for item in draft.preview.excluded}


def state():
    with Session(engine) as session:
        return (
            session.execute(
                select(OutboxMessage.chat_id, OutboxMessage.status, OutboxMessage.sent_at).order_by(OutboxMessage.id)
            ).all(),
            session.execute(
                select(CampaignSupplier.supplier_id, CampaignSupplier.status).order_by(CampaignSupplier.supplier_id)
            ).all(),
            session.scalars(select(SenderState.next_send_at)).all(),
            session.scalars(select(Campaign.status)).all(),
        )


def test_crash_after_send_becomes_unknown(clock, rng):
    campaign = enqueue(clock, 2)
    messenger = Messenger()
    crash(clock, rng, messenger)
    assert outbox() == {"cr000": "sending", "cr001": "pending"}

    clock.advance(timedelta(seconds=10))
    restarted = clock.now()
    start(clock, rng)
    drain(clock, messenger, rng)

    assert messenger.calls == ["cr000", "cr001"]
    assert outbox() == {"cr000": "unknown", "cr001": "sent"}
    assert snapshot(campaign) == {"sr000": ("unknown", []), "sr001": ("sent", [])}
    with Session(engine) as session:
        assert session.get(Campaign, campaign).status == "sent"
        # Писали ли в чат, неизвестно
        assert session.get(Chat, "cr000").last_outbound_at is None
        unknown_at, sent_at = session.scalars(select(OutboxMessage.sent_at).order_by(OutboxMessage.id)).all()
    assert unknown_at == restarted
    # Пауза после unknown отсчитывается заново, как после отправки
    assert sent_at - restarted >= timedelta(minutes=1)


def test_unknown_is_never_sent_again(clock, rng):
    first = enqueue(clock, 2)
    messenger = Messenger()
    crash(clock, rng, messenger)
    restarted = clock.now()
    start(clock, rng)
    drain(clock, messenger, rng)

    for _ in range(3):
        clock.advance(timedelta(days=1))
        start(clock, rng)
        tick(clock, messenger, rng)
    assert messenger.calls == ["cr000", "cr001"]

    second = enqueue(clock, 1, first=2)
    drain(clock, messenger, rng)

    assert messenger.calls == ["cr000", "cr001", "cr002"]
    rows = snapshot(second)
    assert rows["sr000"] == ("excluded", [UNKNOWN.format(first)])
    assert rows["sr002"] == ("sent", [])
    # Через 14 дней — как после отправки: писать в чат снова можно
    clock.set(restarted + timedelta(days=14, seconds=-1))
    assert excluded(clock)["sr000"] == [UNKNOWN.format(first)]
    clock.set(restarted + timedelta(days=14))
    assert "sr000" not in excluded(clock)


def test_unknown_counts_in_daily_limit(clock, rng):
    enqueue(clock, 101)
    messenger = Messenger()
    crash(clock, rng, messenger)
    start(clock, rng)

    drain(clock, messenger, rng)

    with Session(engine) as session:
        times = session.scalars(
            select(OutboxMessage.sent_at).where(OutboxMessage.status == "sent").order_by(OutboxMessage.sent_at)
        ).all()
    assert len(times) == 100
    # Вместе с unknown в первые сутки ушло 100, последнее — ровно через сутки после него
    assert sum(moment < NOW + timedelta(days=1) for moment in times) == 99
    assert times[-1] == NOW + timedelta(days=1)


def test_two_workers_send_each_message_once(clock, no_pause):
    enqueue(clock, 20)
    messenger = Messenger()

    run_workers(clock, messenger)

    assert sorted(messenger.calls) == [f"cr{number:03}" for number in range(20)]
    assert set(outbox().values()) == {"sent"}


def test_two_workers_keep_daily_limit(clock, no_pause):
    enqueue(clock, 105)
    messenger = Messenger()

    run_workers(clock, messenger)

    assert len(messenger.calls) == len(set(messenger.calls)) == 100
    assert Counter(outbox().values()) == {"sent": 100, "pending": 5}


def test_restart_waits_for_working_worker(clock, rng):
    campaign = enqueue(clock, 1)
    with ThreadPoolExecutor(1) as pool, engine.connect() as other:
        # Другой воркер посреди отправки: держит блокировку аккаунта, его строка в sending
        other.execute(select(func.pg_advisory_lock(LOCK)))
        other.execute(update(OutboxMessage).values(status="sending"))
        other.commit()

        restart = pool.submit(start, clock, rng)
        with pytest.raises(TimeoutError):
            restart.result(timeout=0.5)

        other.execute(update(OutboxMessage).values(status="sent", sent_at=NOW))
        other.commit()
        other.execute(select(func.pg_advisory_unlock(LOCK)))
        restart.result(timeout=5)

    assert outbox() == {"cr000": "sent"}
    assert snapshot(campaign) == {"sr000": ("queued", [])}


def test_clean_restart_changes_nothing(clock, rng):
    enqueue(clock, 3)
    messenger = Messenger()
    tick(clock, messenger, rng)
    before = state()

    clock.advance(timedelta(seconds=10))
    start(clock, rng)

    assert state() == before
    drain(clock, messenger, rng)
    assert messenger.calls == ["cr000", "cr001", "cr002"]
