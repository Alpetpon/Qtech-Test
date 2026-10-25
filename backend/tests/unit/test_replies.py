from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.domain.replies import Author, Binding, Message, bind, from_row, group, identify, unique
from app.seed import read

DIRS = (settings.data_dir, settings.data_dir / "extra")
# Наша отправка в чат: t в сценарии отсчитывается от нее
SENT = datetime(2026, 11, 1, 10, tzinfo=UTC)


@pytest.fixture
def scenario():
    return [from_row(row) for row in read(DIRS, "incoming_messages.json")]


@pytest.fixture
def our_accounts():
    return list(read(DIRS, "our_accounts.json"))


@pytest.fixture
def contacts():
    return {row["handle"]: row["supplier_id"] for row in read(DIRS, "supplier_contacts.json")}


def find(scenario, chat_id, t):
    """Сообщение сценария по чату и t; у копии c7 t=5 — первое."""
    return next(m for m in scenario if m.chat_id == chat_id and m.t == Decimal(t))


def arrived(scenario, chat_id):
    return [m.arrive(SENT) for m in scenario if m.chat_id == chat_id]


def replies(messages, our_accounts):
    """Тексты ответов: без копий, наших и не ответов, после склейки."""
    bound = [m for m in unique(messages) if bind(m, [SENT], our_accounts).sent_at is not None]
    return [[m.text for m in reply.messages] for reply in group(bound)]


def chat(full_data, chat_id):
    _, chats = full_data
    return next(c for c in chats if c.id == chat_id)


def test_row_is_read_exactly(scenario):
    nordpack = find(scenario, "c1", "2.03")
    kestrel = find(scenario, "c3", 30)

    assert (nordpack.sender, nordpack.t, nordpack.attachment) == ("@nordpack_sales", Decimal("2.03"), None)
    assert kestrel.attachment == "price_2026.pdf"
    # float дал бы 7307.999… с
    assert nordpack.arrive(SENT).received_at == SENT + timedelta(seconds=7308)


def test_nordpack_two_messages_are_one_reply(scenario, our_accounts):
    # c1: эхо @company_shared t=0.01, NordPack t=2 и t=2.03, @anna_buy t=2.5, NordPack t=400
    assert replies(arrived(scenario, "c1"), our_accounts) == [[
        "Добрый день! Коробки 40x30x30 — 0.42 EUR/шт, минимальная партия 5000 шт",
        "Срок поставки 10 дней, доставляем по EU",
    ]]


def test_reply_closes_after_10_minutes_of_silence(scenario):
    [reply] = group([find(scenario, "c1", 2).arrive(SENT), find(scenario, "c1", "2.03").arrive(SENT)])
    last = SENT + timedelta(seconds=7308)

    assert not reply.closed(last + timedelta(minutes=10, seconds=-1))
    assert reply.closed(last + timedelta(minutes=10))


def test_pause_of_10_minutes_starts_new_reply():
    first = Message("c1", "@nordpack_sales", SENT, "Коробки — 0.42 EUR/шт")
    later = replace(first, text="Срок 10 дней")

    assert len(group([first, replace(later, received_at=SENT + timedelta(minutes=9, seconds=59))])) == 1
    assert len(group([first, replace(later, received_at=SENT + timedelta(minutes=10))])) == 2


def test_shared_chat_merges_each_sender_separately():
    sales = Message("c3", "@kestrel_sales", SENT, "Контейнеры, прайс во вложении")
    eu = Message("c3", "@kestrel_eu", SENT + timedelta(minutes=3), "По EU ничего не можем")
    sales_more = replace(sales, received_at=SENT + timedelta(minutes=6), text="Срок 2 недели")

    assert [[m.text for m in reply.messages] for reply in group([sales_more, eu, sales])] == [
        ["Контейнеры, прайс во вложении", "Срок 2 недели"],
        ["По EU ничего не можем"],
    ]


def test_late_addition_is_new_reply(scenario, our_accounts):
    # c15: «пришлем прайс до конца недели» t=8, сам прайс t=50
    assert len(replies(arrived(scenario, "c15"), our_accounts)) == 2


@pytest.mark.parametrize(
    ("chat_id", "t"),
    [("c1", "2.5"), ("c1", "0.01"), ("c3", 31), ("c9", 21)],
    ids=["anna_buy", "company_shared-echo", "igor_buy", "Anna_Buy-case"],
)
def test_our_accounts_are_not_replies(scenario, our_accounts, chat_id, t):
    message = find(scenario, chat_id, t).arrive(SENT)

    assert bind(message, [SENT], our_accounts) == Binding(None, "наш сотрудник")


def test_after_14_days_is_out_of_window(scenario, our_accounts):
    nordpack = find(scenario, "c1", 400).arrive(SENT)

    assert bind(nordpack, [SENT], our_accounts) == Binding(None, "вне окна")


def test_reply_window_is_less_than_14_days(scenario, our_accounts):
    ridge = find(scenario, "c18", "335.9").arrive(SENT)
    fjord = find(scenario, "c19", 336).arrive(SENT)

    assert bind(ridge, [SENT], our_accounts) == Binding(SENT)
    assert bind(fjord, [SENT], our_accounts) == Binding(None, "вне окна")


def test_before_send_is_not_reply_at_send_is(scenario, our_accounts):
    helio = find(scenario, "c7", -3)

    assert bind(helio.arrive(SENT), [SENT], our_accounts) == Binding(None, "раньше отправки")
    assert bind(replace(helio, t=Decimal(0)).arrive(SENT), [SENT], our_accounts) == Binding(SENT)


def test_chat_without_send_is_not_reply(scenario, our_accounts):
    # Время прихода любое: в c2 мы не отправляли
    orbis = find(scenario, "c2", 5).arrive(SENT)

    assert bind(orbis, [], our_accounts) == Binding(None, "в чат не отправляли")


def test_reply_belongs_to_last_send_before_it():
    second = SENT + timedelta(days=20)
    message = Message("c1", "@nordpack_sales", SENT + timedelta(days=21), "Коробки — 0.40 EUR/шт")

    assert bind(message, [SENT, second], []) == Binding(second)
    assert bind(replace(message, received_at=SENT + timedelta(days=15)), [SENT, second], []) == Binding(
        None, "вне окна"
    )


def test_duplicate_delivery_is_dropped(scenario, our_accounts):
    # c7: t=-3, t=5 и точная копия t=5
    helio = arrived(scenario, "c7")
    same_text_later = replace(helio[1], received_at=helio[1].received_at + timedelta(hours=1))

    assert len(unique(helio)) == 2
    assert replies(helio, our_accounts) == [[helio[1].text]]
    assert len(unique([helio[1], same_text_later])) == 2


@pytest.mark.parametrize(
    ("sender", "author"),
    [
        ("@kestrel_sales", Author("s3")),
        ("@kestrel_eu", Author("s4")),
        ("@kestrel_office", Author(None, "непонятно, от какой компании")),
        ("@nordpack_sales", Author(None, "непонятно, от какой компании")),
    ],
    ids=["kestrel_sales", "kestrel_eu", "unknown-handle", "other-chat-supplier"],
)
def test_shared_chat_supplier_is_found_by_handle(full_data, contacts, sender, author):
    kestrel = chat(full_data, "c3")

    assert identify(sender, kestrel.supplier_ids, contacts, asked={"s3", "s4"}) == author


def test_single_supplier_chat_needs_no_handle(full_data):
    nordpack = chat(full_data, "c1")

    assert identify("@nordpack_ceo", nordpack.supplier_ids, {}, asked={"s1"}) == Author("s1")


def test_company_not_asked_in_mailing(full_data, contacts):
    # c14: спросили только Delta Tools (s15), ответила Delta Logistics (s16)
    delta = chat(full_data, "c14")
    known = {**contacts, "@delta_logistics": "s16"}

    assert identify("@delta_logistics", delta.supplier_ids, known, asked={"s15"}) == Author("s16", "не по рассылке")
    assert identify("@delta_sales", delta.supplier_ids, known, asked={"s15"}) == Author("s15")
