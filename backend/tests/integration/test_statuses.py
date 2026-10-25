from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.clock import ManualClock
from app.config import settings
from app.messenger import inbox_replay
from app.models import CampaignSupplier, InboxMessage, SupplierContact
from app.seed import load
from app.services.campaigns import create
from app.services.inbox import process
from app.services.statuses import refresh

DATA = settings.data_dir
EXTRA = DATA / "extra"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)

# Пресет «Активные, прайс устарел» на данных ТЗ: Orbis — писали 7 дней назад, чат Velta Textile выключен
EXCLUDED = {"s2": "excluded", "s5": "excluded"}

TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


@pytest.fixture
def clock():
    return ManualClock(NOW)


def launch(session, clock, preset):
    """Рассылка, которую очередь уже отправила: воркера в тестах нет, отметки ставим, как ставит он."""
    campaign = create(session, clock, TEMPLATE, preset=preset)
    for message in campaign.messages:
        message.status, message.sent_at = "sent", clock.now()
    for row in campaign.suppliers:
        if row.status == "queued":
            row.status = "sent"
    session.flush()
    return campaign


def after(t):
    return NOW + timedelta(seconds=int(Decimal(str(t)) * 3600))


def statuses_at(session, clock, scenario, moment):
    """На момент moment: входящие пришли и разобраны, статусы пересчитаны."""
    clock.set(moment)
    inbox_replay.deliver(session, clock, scenario)
    process(session)
    refresh(session, clock)
    return {row.supplier_id: row.status for row in session.scalars(select(CampaignSupplier))}


def receive(session, chat_id, sender, received_at):
    """Сообщение не из сценария — как пришло бы из настоящего мессенджера."""
    session.add(
        InboxMessage(
            chat_id=chat_id, external_id=f"test:{sender}", sender=sender, received_at=received_at, text="Добрый день!"
        )
    )
    session.flush()


def test_reply_within_72_hours_is_never_silent(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")  # c1 NordPack
    scenario = inbox_replay.load(DATA)

    assert statuses_at(session, clock, scenario, after(2) - timedelta(seconds=1)) == {"s1": "sent"} | EXCLUDED
    assert statuses_at(session, clock, scenario, after(2)) == {"s1": "replied"} | EXCLUDED
    assert statuses_at(session, clock, scenario, after(72)) == {"s1": "replied"} | EXCLUDED


def test_late_reply_lifts_silence(session, clock):
    load(session, DATA)
    launch(session, clock, "inactive_new")  # c3: Kestrel Foods и Kestrel Foods EU в одном чате
    scenario = inbox_replay.load(DATA)

    assert statuses_at(session, clock, scenario, after(71)) == {"s3": "replied", "s4": "sent", "s6": "excluded"}
    assert statuses_at(session, clock, scenario, after(72)) == {"s3": "replied", "s4": "silent", "s6": "excluded"}
    assert refresh(session, clock) == 0
    assert statuses_at(session, clock, scenario, after(110)) == {"s3": "replied", "s4": "replied", "s6": "excluded"}


def test_unknown_handle_in_shared_chat_affects_nobody(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "inactive_new")
    scenario = inbox_replay.load(DATA, EXTRA)

    # @kestrel_sales — 30 ч, наш @igor_buy — 31 ч, @kestrel_office (ника нет в справочнике) — 40 ч
    statuses = statuses_at(session, clock, scenario, after(40))
    assert (statuses["s3"], statuses["s4"]) == ("replied", "sent")
    statuses = statuses_at(session, clock, scenario, after(72))
    assert (statuses["s3"], statuses["s4"]) == ("replied", "silent")


def test_reply_window_boundary(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA, EXTRA)

    # c18 Ridge ответил через 335.9 ч — в окне, c19 Fjord через 336 ч — уже нет
    statuses = statuses_at(session, clock, scenario, after("335.9") - timedelta(seconds=1))
    assert (statuses["s20"], statuses["s21"]) == ("silent", "silent")
    statuses = statuses_at(session, clock, scenario, after("335.9"))
    assert (statuses["s20"], statuses["s21"]) == ("replied", "silent")
    statuses = statuses_at(session, clock, scenario, after(336))
    assert (statuses["s20"], statuses["s21"]) == ("replied", "silent")


def test_reply_without_price_is_reply(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "inactive_new")
    scenario = inbox_replay.load(DATA, EXTRA)

    # Orion: «пришлём прайс до конца недели», Atlas: «цена по запросу»
    statuses = statuses_at(session, clock, scenario, after(72))
    assert (statuses["s17"], statuses["s12"]) == ("replied", "replied")


def test_our_employee_is_not_reply(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    receive(session, "c1", "@Anna_Buy", after(1))

    assert statuses_at(session, clock, [], after(72))["s1"] == "silent"


def test_company_not_asked_changes_nothing(session, clock):
    load(session, DATA, EXTRA)
    # В c14 Delta Group пресет выбрал только Delta Tools (s15)
    launch(session, clock, "stale_price")
    session.add(SupplierContact(handle="@delta_logistics", supplier_id="s16"))
    receive(session, "c14", "@delta_logistics", after(3))

    statuses = statuses_at(session, clock, [], after(72))
    assert statuses["s15"] == "silent"
    assert "s16" not in statuses


def test_not_sent_statuses_stay(session, clock):
    load(session, DATA)
    campaign = create(session, clock, TEMPLATE, preset="stale_price")

    assert statuses_at(session, clock, [], after(72)) == {"s1": "queued"} | EXCLUDED

    # Отправка прервалась: неизвестно, ушло ли сообщение
    [message] = campaign.messages
    message.status, message.sent_at = "unknown", NOW
    session.get(CampaignSupplier, (campaign.id, "s1")).status = "unknown"

    assert statuses_at(session, clock, [], after(72)) == {"s1": "unknown"} | EXCLUDED


def test_full_data(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    launch(session, clock, "inactive_new")
    scenario = inbox_replay.load(DATA, EXTRA)

    def silent(moment):
        return {s for s, status in statuses_at(session, clock, scenario, moment).items() if status == "silent"}

    assert silent(after(72)) == {"s4", "s20", "s21"}
    assert silent(NOW + timedelta(days=20)) == {"s21"}
