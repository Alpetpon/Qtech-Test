from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.clock import ManualClock
from app.config import settings
from app.models import Campaign, CampaignSupplier, OutboxMessage
from app.seed import load
from app.services.campaigns import Blocked, create, preview

DATA = settings.data_dir
EXTRA = DATA / "extra"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)

TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


@pytest.fixture
def clock():
    return ManualClock(NOW)


def queue(session, campaign):
    rows = session.scalars(select(OutboxMessage).where(OutboxMessage.campaign_id == campaign.id))
    return {row.chat_id: row.text for row in rows}


def snapshot(session, campaign):
    rows = session.scalars(select(CampaignSupplier).where(CampaignSupplier.campaign_id == campaign.id))
    return {row.supplier_id: (row.status, row.chat_id, row.reasons) for row in rows}


def reasons(draft):
    return {item.supplier.id: list(item.reasons) for item in draft.preview.excluded}


def sent(draft):
    return [item.chat.id for item in draft.preview.outgoing]


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def test_active_preset_queues_nordpack(session, clock):
    load(session, DATA)

    campaign = create(session, clock, TEMPLATE, preset="stale_price")

    # У s2 пустой менеджер, но в чат Orbis писали 7 дней назад он исключен и запуск не блокирует
    assert queue(session, campaign) == {
        "c1": "Здравствуйте, NordPack! Пришлите, пожалуйста, актуальный прайс.\nС уважением, Анна",
    }
    assert snapshot(session, campaign) == {
        "s1": ("queued", "c1", []),
        "s2": ("excluded", None, ["писали 7 дней назад"]),
        "s5": ("excluded", None, ["чат выключен"]),
    }
    message = session.scalar(select(OutboxMessage).where(OutboxMessage.campaign_id == campaign.id))
    assert (message.status, message.sent_at) == ("pending", None)
    assert (campaign.preset, campaign.supplier_status, campaign.status) == ("stale_price", None, "sending")
    assert (campaign.template, campaign.created_at) == (TEMPLATE, NOW)


def test_new_preset_one_message_for_shared_chat(session, clock):
    load(session, DATA)

    campaign = create(session, clock, TEMPLATE, preset="inactive_new")

    assert queue(session, campaign) == {
        "c3": "Здравствуйте, Kestrel Foods, Kestrel Foods EU! Пришлите, пожалуйста, актуальный прайс.\n"
        "С уважением, Игорь",
    }
    assert snapshot(session, campaign) == {
        "s3": ("queued", "c3", []),
        "s4": ("queued", "c3", []),
        "s6": ("excluded", None, ["нет чата"]),
    }


def test_blocked_template_creates_nothing(session, clock):
    load(session, DATA, EXTRA)

    with pytest.raises(Blocked) as blocked:
        create(session, clock, TEMPLATE, status="new")

    assert blocked.value.draft.problems == ("У Polar Film пустой менеджер",)
    assert count(session, Campaign) == count(session, CampaignSupplier) == count(session, OutboxMessage) == 0


def test_repeat_right_after_first_excludes_chat(session, clock):
    load(session, DATA)
    first = create(session, clock, TEMPLATE, preset="stale_price")

    with pytest.raises(Blocked) as blocked:
        create(session, clock, TEMPLATE, preset="stale_price")

    draft = blocked.value.draft
    assert draft.problems == ("Отправлять некому: в рассылке ни одного чата",)
    assert reasons(draft)["s1"] == [f"уже в очереди рассылки №{first.id}"]
    assert count(session, Campaign) == count(session, OutboxMessage) == 1


def test_busy_chats_excluded_others_sent(session, clock):
    load(session, DATA, EXTRA)
    first = create(session, clock, TEMPLATE, preset="stale_price")

    # Фильтр active берёт и Pinewood: прайсу ровно 60 дней, в пресет он не попал
    second = create(session, clock, TEMPLATE, status="active")

    assert queue(session, second) == {
        "c8": "Здравствуйте, Pinewood Pallets! Пришлите, пожалуйста, актуальный прайс.\nС уважением, Анна",
    }
    busy = [f"уже в очереди рассылки №{first.id}"]
    rows = snapshot(session, second)
    assert {supplier for supplier, (_, _, why) in rows.items() if why == busy} == {
        "s1", "s8", "s10", "s13", "s15", "s20", "s21",
    }
    assert count(session, OutboxMessage) == 8


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("pending", "уже в очереди рассылки №{}"),
        ("sending", "уже в очереди рассылки №{}"),
        ("unknown", "неизвестно, ушло ли сообщение рассылки №{}, — проверьте чат"),
    ],
)
def test_unsent_message_keeps_chat_busy(session, clock, status, reason):
    load(session, DATA)
    first = create(session, clock, TEMPLATE, preset="stale_price")
    first.messages[0].status = status

    clock.advance(timedelta(days=30))

    assert reasons(preview(session, clock, TEMPLATE, preset="stale_price"))["s1"] == [reason.format(first.id)]


def test_sent_message_keeps_chat_14_days(session, clock):
    load(session, DATA)
    first = create(session, clock, TEMPLATE, preset="stale_price")
    # last_outbound_at чата не трогаем: очередь держит чат и без него
    first.messages[0].status, first.messages[0].sent_at = "sent", NOW

    clock.advance(timedelta(days=14, seconds=-1))
    assert reasons(preview(session, clock, TEMPLATE, preset="stale_price"))["s1"] == [
        f"уже писали в рассылке №{first.id}"
    ]

    clock.advance(timedelta(seconds=1))
    assert "c1" in sent(preview(session, clock, TEMPLATE, preset="stale_price"))


@pytest.mark.parametrize(
    ("preset", "selected", "chats", "excluded"), [("stale_price", 12, 7, 5), ("inactive_new", 5, 3, 1)]
)
def test_full_data_matches_readme(session, clock, preset, selected, chats, excluded):
    load(session, DATA, EXTRA)

    campaign = create(session, clock, TEMPLATE, preset=preset)

    rows = snapshot(session, campaign).values()
    assert len(rows) == selected
    assert len(queue(session, campaign)) == chats
    assert sum(status == "excluded" for status, _, _ in rows) == excluded


@pytest.mark.parametrize("selection", [{}, {"preset": "stale_price", "status": "active"}, {"preset": "fresh"}])
def test_selection_is_preset_or_status(session, clock, selection):
    with pytest.raises(ValueError):
        preview(session, clock, TEMPLATE, **selection)
