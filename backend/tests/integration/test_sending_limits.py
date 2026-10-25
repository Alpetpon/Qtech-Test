import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.clock import ManualClock
from app.db import engine
from app.messenger.sender import StubSender
from app.models import Campaign, CampaignSupplier, Chat, OutboxMessage, SenderState, Supplier
from app.services.campaigns import create
from app.services.sending import LOCK, ready_at, send_next

NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


@pytest.fixture
def clock():
    return ManualClock(NOW)


@pytest.fixture
def sender():
    return StubSender()


@pytest.fixture
def rng():
    return random.Random(8)


@pytest.fixture
def step(session, clock, sender, rng):
    return lambda: send_next(session, clock, sender, rng)


def enqueue(session, clock, chats):
    """Рассылка в chats чатов: у каждого свой активный поставщик без прайса."""
    for number in range(chats):
        supplier = Supplier(
            id=f"sq{number:03}",
            name=f"Поставщик {number:03}",
            status="active",
            manager="Анна",
            price_updated_at=None,
            last_activity_at=None,
        )
        chat = Chat(
            id=f"cq{number:03}",
            title=f"Чат {number:03}",
            enabled=True,
            member=True,
            last_outbound_at=None,
            suppliers=[supplier],
        )
        session.add(chat)
    session.flush()
    return create(session, clock, TEMPLATE, status="active")


def drain(session, clock, step):
    """Отправляет всю очередь, каждый раз переводя часы на момент, когда отправка разрешена."""
    while True:
        clock.set(ready_at(session, clock.now()))
        if step() is None:
            query = select(OutboxMessage.sent_at).where(OutboxMessage.status == "sent").order_by(OutboxMessage.sent_at)
            return list(session.scalars(query))


def test_empty_queue_does_nothing(session, sender, step):
    assert step() is None

    assert sender.sent == []
    assert session.scalar(select(func.count()).select_from(SenderState)) == 0


def test_sent_with_clock_time(session, clock, sender, step):
    campaign = enqueue(session, clock, 2)
    # Часы ушли далеко от создания рассылки и от системного времени
    clock.advance(timedelta(days=3, hours=5, seconds=17))

    message = step()

    assert (message.chat_id, message.status, message.sent_at) == ("cq000", "sent", clock.now())
    assert session.get(Chat, "cq000").last_outbound_at == clock.now()
    assert session.get(Chat, "cq001").last_outbound_at is None
    assert sender.sent == [("cq000", message.text)]
    statuses = dict(session.execute(
        select(CampaignSupplier.supplier_id, CampaignSupplier.status).where(CampaignSupplier.campaign_id == campaign.id)
    ).all())
    assert statuses == {"sq000": "sent", "sq001": "queued"}


def test_nothing_goes_before_pause(session, clock, sender, step):
    enqueue(session, clock, 3)
    step()
    pause_end = session.get(SenderState, "@company_shared").next_send_at

    clock.advance(timedelta(seconds=59))
    assert step() is None
    clock.set(pause_end - timedelta(seconds=1))
    assert step() is None
    assert len(sender.sent) == 1

    clock.set(pause_end)
    assert step().sent_at == pause_end
    assert len(sender.sent) == 2


def test_pause_between_sends_is_random_1_to_3_minutes(session, clock, sender, step):
    enqueue(session, clock, 60)

    times = drain(session, clock, step)

    pauses = [later - earlier for earlier, later in zip(times, times[1:])]
    assert len(times) == 60
    assert all(timedelta(minutes=1) <= pause <= timedelta(minutes=3) for pause in pauses)
    assert len(set(pauses)) > 10
    assert [chat_id for chat_id, _ in sender.sent] == [f"cq{number:03}" for number in range(60)]


def test_150_chats_100_in_first_day(session, clock, sender, step):
    campaign = enqueue(session, clock, 150)

    times = drain(session, clock, step)

    assert len(times) == len(sender.sent) == 150
    assert sum(moment < NOW + timedelta(days=1) for moment in times) == 100
    # В любые 24 часа — не больше 100: сто первое считая от любого уходит не раньше чем через сутки
    assert all(later - earlier >= timedelta(days=1) for earlier, later in zip(times, times[100:]))
    assert times[100] == NOW + timedelta(days=1)
    assert session.get(Campaign, campaign.id).status == "sent"


def test_limit_holds_until_window_frees(session, clock, sender, step):
    enqueue(session, clock, 101)
    while len(sender.sent) < 100:
        clock.set(ready_at(session, clock.now()))
        step()

    clock.set(NOW + timedelta(days=1, seconds=-1))
    assert step() is None
    assert len(sender.sent) == 100

    clock.set(NOW + timedelta(days=1))
    assert step() is not None


def test_campaign_done_when_all_sent(session, clock, step):
    campaign = enqueue(session, clock, 2)

    step()
    assert session.get(Campaign, campaign.id).status == "sending"

    clock.set(ready_at(session, clock.now()))
    step()
    assert session.get(Campaign, campaign.id).status == "sent"


def test_one_sender_at_a_time(session, clock, sender, step):
    enqueue(session, clock, 1)
    # Другой воркер посреди своей отправки держит блокировку аккаунта
    with engine.connect() as other:
        other.execute(select(func.pg_advisory_lock(LOCK)))
        try:
            assert step() is None
            assert sender.sent == []
        finally:
            other.execute(select(func.pg_advisory_unlock(LOCK)))

    assert step() is not None
