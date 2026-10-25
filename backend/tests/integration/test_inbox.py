from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import select

from app.clock import ManualClock
from app.config import settings
from app.messenger import inbox_replay
from app.models import CampaignSupplier, InboxMessage, Reply, SupplierContact
from app.seed import load
from app.services.campaigns import create
from app.services.inbox import process

DATA = settings.data_dir
EXTRA = DATA / "extra"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)

TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"

UNKNOWN = "непонятно, от какой компании"


@pytest.fixture
def clock():
    return ManualClock(NOW)


def launch(session, clock, preset):
    """Рассылка, которую очередь уже отправила: воркера в тестах нет, отправку ставим сами."""
    campaign = create(session, clock, TEMPLATE, preset=preset)
    for message in campaign.messages:
        message.status, message.sent_at = "sent", clock.now()
    session.flush()
    return campaign


def after(t, sent=NOW):
    return sent + timedelta(seconds=int(Decimal(str(t)) * 3600))


def run(session, clock, scenario, until):
    clock.set(until)
    inbox_replay.deliver(session, clock, scenario)
    return process(session)


def marks(session):
    rows = session.scalars(
        select(InboxMessage)
        .where(InboxMessage.mark.is_not(None))
        .order_by(InboxMessage.received_at, InboxMessage.id)
    )
    return [(row.chat_id, row.sender, row.received_at, row.mark) for row in rows]


def replies(session):
    rows = session.scalars(select(Reply).order_by(Reply.id))
    return {
        (reply.sender, reply.supplier_id, reply.mark, tuple(m.received_at for m in reply.messages)) for reply in rows
    }


numbers = count()


def receive(session, chat_id, sender, received_at, text="Добрый день!"):
    """Сообщение не из сценария — как пришло бы из настоящего мессенджера."""
    session.add(
        InboxMessage(
            chat_id=chat_id, external_id=f"test:{next(numbers)}", sender=sender, received_at=received_at, text=text
        )
    )
    session.flush()


def test_replay_delivers_at_sent_plus_t_once(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")  # только c1 NordPack
    scenario = inbox_replay.load(DATA)

    clock.set(after(2) - timedelta(seconds=1))
    assert inbox_replay.deliver(session, clock, scenario) == 0
    clock.set(after(2))
    assert inbox_replay.deliver(session, clock, scenario) == 1
    assert inbox_replay.deliver(session, clock, scenario) == 0
    clock.set(after(400))
    assert inbox_replay.deliver(session, clock, scenario) == 3

    rows = session.scalars(select(InboxMessage).order_by(InboxMessage.received_at))
    # В c3 не отправляли — сообщения Kestrel не пришли
    assert [(row.chat_id, row.sender, row.received_at) for row in rows] == [
        ("c1", "@nordpack_sales", after(2)),
        ("c1", "@nordpack_sales", NOW + timedelta(seconds=7308)),
        ("c1", "@anna_buy", after("2.5")),
        ("c1", "@nordpack_sales", after(400)),
    ]


def test_full_data(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    launch(session, clock, "inactive_new")

    run(session, clock, inbox_replay.load(DATA, EXTRA), NOW + timedelta(days=20))

    # В c2 не отправляли: Orbis исключен, его сообщение не пришло
    assert marks(session) == [
        ("c7", "@helio_sales", after(-3), "раньше отправки"),
        ("c1", "@company_shared", after("0.01"), "наш сотрудник"),
        ("c1", "@anna_buy", after("2.5"), "наш сотрудник"),
        ("c7", "@helio_sales", after(5), "точная копия"),
        ("c9", "@Anna_Buy", after(21), "наш сотрудник"),
        ("c3", "@igor_buy", after(31), "наш сотрудник"),
        ("c19", "@fjord_sales", after(336), "вне окна"),
        ("c1", "@nordpack_sales", after(400), "вне окна"),
    ]
    assert replies(session) == {
        ("@nordpack_sales", "s1", None, (after(2), after("2.03"))),
        ("@kestrel_sales", "s3", None, (after(30),)),
        ("@kestrel_office", None, UNKNOWN, (after(40),)),
        ("@kestrel_eu", "s4", None, (after(110),)),
        ("@helio_sales", "s8", None, (after(5),)),
        ("@lumen_sales", "s10", None, (after(20),)),
        ("@atlas_sales", "s12", None, (after(12),)),
        ("@sigma_sales", "s13", None, (after(48),)),
        ("@delta_sales", "s15", None, (after(26),)),
        # Позднее дополнение — отдельный ответ
        ("@orion_sales", "s17", None, (after(8),)),
        ("@orion_sales", "s17", None, (after(50),)),
        ("@ridge_sales", "s20", None, (after("335.9"),)),
    }


def test_repeat_adds_nothing(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA, EXTRA)
    run(session, clock, scenario, NOW + timedelta(days=20))
    before = (marks(session), replies(session))

    assert run(session, clock, scenario, NOW + timedelta(days=20)) == 0
    assert (marks(session), replies(session)) == before


def test_reply_continues_across_calls(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)

    assert run(session, clock, scenario, after(2)) == 1
    assert run(session, clock, scenario, after("2.03")) == 1

    [reply] = session.scalars(select(Reply)).all()
    assert [m.received_at for m in reply.messages] == [after(2), after("2.03")]
    assert reply.last_at == after("2.03")


def test_message_after_10_minutes_of_silence_is_new_reply(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    run(session, clock, inbox_replay.load(DATA), after("2.03"))
    last = after("2.03")

    receive(session, "c1", "@nordpack_sales", last + timedelta(minutes=9, seconds=59), "И ещё: доставка бесплатно")
    receive(session, "c1", "@nordpack_sales", last + timedelta(minutes=19, seconds=59), "Ждём ответа")
    process(session)

    assert replies(session) == {
        ("@nordpack_sales", "s1", None, (after(2), last, last + timedelta(minutes=9, seconds=59))),
        ("@nordpack_sales", "s1", None, (last + timedelta(minutes=19, seconds=59),)),
    }


def test_company_not_asked_in_mailing(session, clock):
    load(session, DATA, EXTRA)
    # В c14 Delta Group пресет выбрал только Delta Tools (s15)
    campaign = launch(session, clock, "stale_price")
    session.add(SupplierContact(handle="@delta_logistics", supplier_id="s16"))
    # Исключенный поставщик тоже есть в снимке рассылки, но сообщение ему не уходило
    session.add(
        CampaignSupplier(campaign_id=campaign.id, supplier_id="s16", reasons=["нет чата"], status="excluded")
    )

    receive(session, "c14", "@delta_sales", NOW + timedelta(hours=3))
    receive(session, "c14", "@delta_logistics", NOW + timedelta(hours=3, minutes=2))
    process(session)

    # Две компании в одном чате с паузой 2 минуты — два ответа
    assert replies(session) == {
        ("@delta_sales", "s15", None, (NOW + timedelta(hours=3),)),
        ("@delta_logistics", "s16", "не по рассылке", (NOW + timedelta(hours=3, minutes=2),)),
    }


def test_not_replies_from_real_messenger(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    receive(session, "c2", "@orbis_sales", after(5), "Есть новый прайс, интересно?")
    receive(session, "c14", "@Delta_Sales", after(26), "Инструмент: от 0.40 до 0.45 EUR/шт")
    process(session)
    # Копия пришла позже оригинала, отдельным вызовом
    receive(session, "c14", "@Delta_Sales", after(26), "Инструмент: от 0.40 до 0.45 EUR/шт")
    process(session)

    assert marks(session) == [
        ("c2", "@orbis_sales", after(5), "в чат не отправляли"),
        ("c14", "@Delta_Sales", after(26), "точная копия"),
    ]
    # Общий чат, в справочнике ник @delta_sales: регистр не важен
    assert replies(session) == {("@Delta_Sales", "s15", None, (after(26),))}


def test_reply_belongs_to_its_mailing(session, clock):
    load(session, DATA)
    first = launch(session, clock, "inactive_new")  # c3 Kestrel
    scenario = inbox_replay.load(DATA)
    run(session, clock, scenario, after(111))
    clock.set(NOW + timedelta(days=20))
    second = launch(session, clock, "inactive_new")

    run(session, clock, scenario, after(111, sent=NOW + timedelta(days=20)))

    rows = session.scalars(select(Reply).order_by(Reply.id))
    by_mailing = [(reply.outbox_id, reply.sender) for reply in rows]
    assert by_mailing == [
        (first.messages[0].id, "@kestrel_sales"),
        (first.messages[0].id, "@kestrel_eu"),
        (second.messages[0].id, "@kestrel_sales"),
        (second.messages[0].id, "@kestrel_eu"),
    ]
