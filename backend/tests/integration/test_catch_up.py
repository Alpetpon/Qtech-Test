"""Догон перемотанного времени: после «+1 день» очередь идёт так, будто сутки прошли на самом деле.

Данные коммитятся по-настоящему: шаг воркера работает на своём соединении, как в test_restart.py.
"""

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.clock import ManualClock
from app.config import settings
from app.db import engine
from app.domain.replies import Incoming
from app.messenger.sender import StubSender
from app.models import Base, Campaign, Chat, InboxMessage, OutboxMessage, SenderState, Supplier
from app.services.campaigns import create
from app.services.sending import campaign_status, current_pause, ready_at, resume
from app.worker import SEND_BATCH, receive, start, tick

NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
DAY = timedelta(days=1)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


class Crash(Exception):
    """Процесс упал: сообщение уже ушло, а записать результат не успели."""


class Messenger:
    """Заглушка отправителя, которая падает на вызове номер crash_on."""

    def __init__(self, crash_on: int) -> None:
        self.calls: list[str] = []
        self.crash_on = crash_on

    def send(self, chat_id: str, text: str) -> None:
        self.calls.append(chat_id)
        if len(self.calls) == self.crash_on:
            raise Crash


@pytest.fixture(autouse=True)
def clean(schema):
    """После теста база снова пустая: остальные тесты работают в откатываемой транзакции и ждут её такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def clock():
    return ManualClock(NOW)


@pytest.fixture
def rng():
    return random.Random(24)


def enqueue(clock, chats):
    """Рассылка в chats чатов, созданная в NOW: у каждого чата свой активный поставщик без прайса."""
    with Session(engine) as session:
        for number in range(chats):
            supplier = Supplier(
                id=f"sc{number:03}",
                name=f"Поставщик {number:03}",
                status="active",
                manager="Анна",
                price_updated_at=None,
                last_activity_at=None,
            )
            session.add(
                Chat(
                    id=f"cc{number:03}",
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


def work(clock, sender, rng):
    """Шаги воркера, запущенного в NOW, на стоящих часах, пока они пробуют отправлять. Возвращает число таких шагов."""
    steps = 0
    while True:
        before = sender.attempts
        tick(clock, sender, rng, NOW)
        if sender.attempts == before:
            return steps
        steps += 1


def sent_times():
    with Session(engine) as session:
        query = select(OutboxMessage.sent_at).where(OutboxMessage.status == "sent").order_by(OutboxMessage.sent_at)
        return list(session.scalars(query))


def pending():
    with Session(engine) as session:
        return session.scalar(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.status == "pending")
        )


def due(clock):
    """Можно ли уже отправить следующее сообщение: с догоном оно уходит, как только ready_at не позже текущего времени."""
    with Session(engine) as session:
        return pending() > 0 and ready_at(session, clock.now()) <= clock.now()


def test_day_forward_sends_a_days_worth_with_pauses(clock, rng):
    campaign = enqueue(clock, 150)
    sender = StubSender()
    tick(clock, sender, rng, NOW)
    assert len(sender.sent) == 1

    clock.advance(DAY)
    work(clock, sender, rng)

    times = sent_times()
    # За сутки по часам — ровно 100; сто первое — ровно через сутки после первого, то есть в текущий момент
    assert sum(moment < NOW + DAY for moment in times) == 100
    assert times[100:] == [NOW + DAY]
    pauses = [later - earlier for earlier, later in pairwise(times[:100])]
    assert all(timedelta(minutes=1) <= pause <= timedelta(minutes=3) for pause in pauses)
    assert pending() == 49

    clock.advance(DAY)
    work(clock, sender, rng)

    times = sent_times()
    assert len(times) == len(sender.sent) == 150
    assert [chat_id for chat_id, _ in sender.sent] == [f"cc{number:03}" for number in range(150)]
    assert all(later - earlier >= timedelta(minutes=1) for earlier, later in pairwise(times))
    # В любые 24 часа — не больше 100: сто первое считая от любого уходит не раньше чем через сутки
    assert all(later - earlier >= DAY for earlier, later in zip(times, times[100:]))
    with Session(engine) as session:
        assert session.get(Campaign, campaign).status == "sent"


def test_catch_up_goes_in_batches(clock, rng):
    enqueue(clock, 30)
    sender = StubSender()
    tick(clock, sender, rng, NOW)
    clock.advance(DAY)

    tick(clock, sender, rng, NOW)
    assert len(sender.sent) == 1 + SEND_BATCH

    assert work(clock, sender, rng) == 2
    assert len(sender.sent) == 30


def test_without_rewind_each_message_goes_at_its_step(clock, rng):
    enqueue(clock, 4)
    sender = StubSender()
    steps = []
    # Часы не перематывают, воркер шагает раз в секунду
    while len(sender.sent) < 4:
        before = len(sender.sent)
        tick(clock, sender, rng, NOW)
        assert len(sender.sent) - before <= 1
        if len(sender.sent) > before:
            steps.append(clock.now())
        clock.advance(timedelta(seconds=1))

    # Как и до догона: каждое сообщение отмечено моментом шага, который его отправил, задним числом — ничего
    times = sent_times()
    assert times == steps
    assert all(timedelta(minutes=1) <= later - earlier <= timedelta(minutes=3) for earlier, later in pairwise(times))


def test_rate_limit_during_catch_up_pauses_in_clock_time(clock, rng):
    enqueue(clock, 20)
    sender = StubSender(lambda attempt: attempt == 5)
    tick(clock, sender, rng, NOW)
    clock.advance(DAY)

    work(clock, sender, rng)

    times = sent_times()
    assert (sender.attempts, len(times)) == (21, 20)
    with Session(engine) as session:
        pause = session.get(SenderState, "@company_shared")
        # RATE_LIMIT на пятой попытке — через 1–3 минуты по часам после четвёртого сообщения
        assert timedelta(minutes=1) <= pause.paused_at - times[3] <= timedelta(minutes=3)
        assert (pause.pause_reason, pause.paused_until) == ("RATE_LIMIT", pause.paused_at + settings.rate_limit_pause)
        # Пауза кончилась ещё в промотанном времени: сейчас её нет, сообщение ушло сразу после неё
        assert current_pause(session, clock.now()) is None
    assert times[4] == pause.paused_until
    assert all(later - earlier >= timedelta(minutes=1) for earlier, later in pairwise(times))


def test_rate_limit_pause_that_is_not_over_stops_catch_up(clock, rng):
    campaign = enqueue(clock, 3)
    sender = StubSender(lambda attempt: attempt == 2)
    tick(clock, sender, rng, NOW)
    clock.advance(timedelta(minutes=5))

    work(clock, sender, rng)

    assert (sender.attempts, len(sender.sent)) == (2, 1)
    with Session(engine) as session:
        pause = current_pause(session, clock.now())
        assert timedelta(minutes=1) <= pause.paused_at - NOW <= timedelta(minutes=3)
        assert campaign_status(session, clock.now(), session.get(Campaign, campaign)) == "paused"
        until = pause.paused_until

    clock.set(until)
    tick(clock, sender, rng, NOW)
    assert sent_times()[1] == until


def test_resume_sends_from_resume_moment_not_inside_pause(clock, rng):
    enqueue(clock, 3)
    sender = StubSender(lambda attempt: attempt == 2)
    tick(clock, sender, rng, NOW)
    clock.advance(timedelta(minutes=5))
    work(clock, sender, rng)

    with Session(engine) as session:
        resume(session, clock.now())
        session.commit()
    tick(clock, sender, rng, NOW)

    assert sent_times()[1] == clock.now()


def test_two_workers_catch_up_without_duplicates(clock):
    enqueue(clock, 120)
    # Отправки идут под блокировкой аккаунта, поэтому один StubSender на двоих не путается
    sender = StubSender()
    clock.advance(DAY)

    def worker(seed):
        rng = random.Random(seed)
        while due(clock):
            tick(clock, sender, rng, NOW)

    with ThreadPoolExecutor(2) as pool:
        for future in [pool.submit(worker, seed) for seed in range(2)]:
            future.result()

    chats = [chat_id for chat_id, _ in sender.sent]
    assert len(chats) == len(set(chats)) == 101
    times = sent_times()
    assert all(later - earlier >= timedelta(minutes=1) for earlier, later in pairwise(times))
    assert all(later - earlier >= DAY for earlier, later in zip(times, times[100:]))


def test_crash_during_catch_up_is_not_sent_again(clock, rng):
    enqueue(clock, 5)
    messenger = Messenger(crash_on=3)
    clock.advance(DAY)
    with pytest.raises(Crash):
        tick(clock, messenger, rng, NOW)

    # Перезапуск: время до него очередь уже не догоняет
    start(clock, rng)
    restarted = clock.now()
    tick(clock, messenger, rng, restarted)
    assert messenger.calls == ["cc000", "cc001", "cc002"]
    clock.advance(timedelta(minutes=10))
    for _ in range(3):
        tick(clock, messenger, rng, restarted)

    assert messenger.calls == ["cc000", "cc001", "cc002", "cc003", "cc004"]
    with Session(engine) as session:
        rows = session.execute(select(OutboxMessage.status, OutboxMessage.sent_at).order_by(OutboxMessage.id)).all()
    assert [status for status, _ in rows] == ["sent", "sent", "unknown", "sent", "sent"]
    assert rows[2].sent_at == restarted
    assert rows[3].sent_at - restarted >= timedelta(minutes=1)


def test_replies_arrive_from_caught_up_sent_at(clock, rng):
    enqueue(clock, 3)
    scenario = [
        Incoming(chat_id=f"cc{number:03}", sender=f"@sales_{number}", t=Decimal("2.03"), text=f"Прайс {number}")
        for number in range(3)
    ]
    sender = StubSender()
    tick(clock, sender, rng, NOW)
    clock.advance(DAY)
    work(clock, sender, rng)

    receive(clock, scenario)

    with Session(engine) as session:
        sent = dict(session.execute(select(OutboxMessage.chat_id, OutboxMessage.sent_at)).all())
        received = dict(session.execute(select(InboxMessage.chat_id, InboxMessage.received_at)).all())
    # Отправки — через 1–3 минуты друг за другом, а не в момент перемотки; ответы — через 2.03 ч после каждой
    assert max(sent.values()) - NOW <= timedelta(minutes=6)
    assert received == {chat_id: at + timedelta(hours=2, minutes=1, seconds=48) for chat_id, at in sent.items()}
