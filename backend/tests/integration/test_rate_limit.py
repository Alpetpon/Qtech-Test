import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import exists, select

from app.clock import ManualClock
from app.config import settings
from app.messenger.sender import StubSender
from app.models import CampaignSupplier, Chat, OutboxMessage, Supplier
from app.services.campaigns import create
from app.services.sending import campaign_status, current_pause, ready_at, resume, send_next

NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"
PAUSE = settings.rate_limit_pause


@pytest.fixture
def clock():
    return ManualClock(NOW)


@pytest.fixture
def rng():
    return random.Random(9)


def enqueue(session, clock, numbers):
    """Рассылка в чаты с номерами numbers: у каждого свой активный поставщик без прайса."""
    for number in numbers:
        supplier = Supplier(
            id=f"sr{number:03}",
            name=f"Поставщик {number:03}",
            status="active",
            manager="Анна",
            price_updated_at=None,
            last_activity_at=None,
        )
        chat = Chat(
            id=f"cr{number:03}",
            title=f"Чат {number:03}",
            enabled=True,
            member=True,
            last_outbound_at=None,
            suppliers=[supplier],
        )
        session.add(chat)
    session.flush()
    return create(session, clock, TEMPLATE, status="active")


def drain(session, clock, sender, rng):
    """Отправляет всю очередь, каждый раз переводя часы на момент, когда отправка разрешена, и пережидая паузы."""
    while session.scalar(select(exists().where(OutboxMessage.status == "pending"))):
        clock.set(ready_at(session, clock.now()))
        send_next(session, clock, sender, rng)
    query = select(OutboxMessage.sent_at).where(OutboxMessage.status == "sent").order_by(OutboxMessage.sent_at)
    return list(session.scalars(query))


def sent_chats(sender):
    return [chat_id for chat_id, _ in sender.sent]


def test_rate_limited_message_goes_first_after_pause(session, clock, rng):
    sender = StubSender(lambda attempt: attempt == 3)
    campaign = enqueue(session, clock, range(5))
    for _ in range(2):
        clock.set(ready_at(session, clock.now()))
        send_next(session, clock, sender, rng)
    clock.set(ready_at(session, clock.now()))
    hit_at = clock.now()

    assert send_next(session, clock, sender, rng) is None

    message = session.scalars(select(OutboxMessage).where(OutboxMessage.chat_id == "cr002")).one()
    assert (message.status, message.sent_at) == ("pending", None)
    assert session.get(Chat, "cr002").last_outbound_at is None
    assert session.get(CampaignSupplier, (campaign.id, "sr002")).status == "queued"
    pause = current_pause(session, clock.now())
    assert (pause.pause_reason, pause.paused_at, pause.paused_until) == ("RATE_LIMIT", hit_at, hit_at + PAUSE)
    assert campaign_status(session, clock.now(), campaign) == "paused"

    # Во время паузы отправитель даже не вызывается
    clock.set(hit_at + PAUSE - timedelta(seconds=1))
    assert send_next(session, clock, sender, rng) is None
    assert (sender.attempts, sent_chats(sender)) == (3, ["cr000", "cr001"])

    clock.set(hit_at + PAUSE)
    assert campaign_status(session, clock.now(), campaign) == "sending"
    message = send_next(session, clock, sender, rng)
    assert (message.chat_id, message.sent_at) == ("cr002", hit_at + PAUSE)

    drain(session, clock, sender, rng)
    assert sent_chats(sender) == ["cr000", "cr001", "cr002", "cr003", "cr004"]
    assert campaign_status(session, clock.now(), campaign) == "sent"


def test_resume_lifts_pause_at_once(session, clock, rng):
    sender = StubSender(lambda attempt: attempt == 1)
    enqueue(session, clock, range(2))
    assert send_next(session, clock, sender, rng) is None
    clock.advance(timedelta(minutes=1))
    assert send_next(session, clock, sender, rng) is None

    resume(session, clock.now())
    session.commit()

    assert current_pause(session, clock.now()) is None
    message = send_next(session, clock, sender, rng)
    assert (message.chat_id, message.sent_at) == ("cr000", NOW + timedelta(minutes=1))


def test_rate_limit_again_after_pause_pauses_again(session, clock, rng):
    sender = StubSender(lambda attempt: attempt in (1, 2))
    enqueue(session, clock, range(2))
    send_next(session, clock, sender, rng)

    clock.set(NOW + PAUSE)
    assert send_next(session, clock, sender, rng) is None

    pause = current_pause(session, clock.now())
    assert (pause.paused_at, pause.paused_until) == (NOW + PAUSE, NOW + 2 * PAUSE)
    clock.set(NOW + 2 * PAUSE - timedelta(seconds=1))
    assert send_next(session, clock, sender, rng) is None
    assert sender.attempts == 2

    clock.set(NOW + 2 * PAUSE)
    assert send_next(session, clock, sender, rng).chat_id == "cr000"


def test_rate_limit_does_not_use_daily_limit(session, clock, rng):
    # RATE_LIMIT на сотой попытке: место в лимите не сгорает, сотое сообщение уходит в те же сутки
    sender = StubSender(lambda attempt: attempt == 100)
    enqueue(session, clock, range(101))

    times = drain(session, clock, sender, rng)

    assert (sender.attempts, len(times)) == (102, 101)
    assert sum(moment < NOW + timedelta(days=1) for moment in times) == 100
    assert times[100] == NOW + timedelta(days=1)


def test_pause_stops_all_campaigns(session, clock, rng):
    sender = StubSender(lambda attempt: attempt == 1)
    first = enqueue(session, clock, range(1))
    second = enqueue(session, clock, range(1, 3))

    assert send_next(session, clock, sender, rng) is None

    # Сообщения второй рассылки RATE_LIMIT не получали, но отправитель у всех один
    clock.set(NOW + PAUSE - timedelta(seconds=1))
    assert send_next(session, clock, sender, rng) is None
    assert sender.attempts == 1
    assert campaign_status(session, clock.now(), first) == campaign_status(session, clock.now(), second) == "paused"

    drain(session, clock, sender, rng)
    assert sent_chats(sender) == ["cr000", "cr001", "cr002"]
    assert campaign_status(session, clock.now(), first) == campaign_status(session, clock.now(), second) == "sent"
