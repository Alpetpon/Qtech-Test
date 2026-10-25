from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities import Chat
from app.domain.preview import Totals, ago, build_preview

# Выбраны вручную по правилам пресетов на полном наборе
# «Активные» — прайс старше 60 календарных дней или его нет, «Новые» — активность старше 30
ACTIVE = ("s1", "s2", "s5", "s7", "s8", "s10", "s13", "s14", "s15", "s18", "s20", "s21")
NEW = ("s3", "s4", "s6", "s12", "s17")


def pick(suppliers, *ids):
    by_id = {supplier.id: supplier for supplier in suppliers}
    return [by_id[i] for i in ids]


def sent(preview):
    return {item.chat.id: [supplier.id for supplier in item.suppliers] for item in preview.outgoing}


def reasons(preview):
    return {item.supplier.id: list(item.reasons) for item in preview.excluded}


def test_task_data_only_nordpack_is_sent(task_data, now):
    suppliers, chats = task_data

    preview = build_preview(pick(suppliers, "s1", "s2", "s5"), chats, now)

    assert sent(preview) == {"c1": ["s1"]}
    assert reasons(preview) == {"s2": ["писали 7 дней назад"], "s5": ["чат выключен"]}
    assert preview.totals == Totals(selected=3, chats=1, excluded=2)


def test_task_data_shared_chat_is_one_message(task_data, now):
    suppliers, chats = task_data

    preview = build_preview(pick(suppliers, "s3", "s4", "s6"), chats, now)

    assert sent(preview) == {"c3": ["s3", "s4"]}
    assert reasons(preview) == {"s6": ["нет чата"]}
    assert preview.totals == Totals(selected=3, chats=1, excluded=1)


def test_full_data_traps(full_data, now):
    suppliers, chats = full_data

    preview = build_preview(pick(suppliers, "s7", "s8", "s13", "s14", "s15", "s17", "s18"), chats, now)

    # c7: не писали никогда; c12: ровно 14 календарных дней; c14: из общего чата выбрана только s15;
    # c15: ссылка на s99 отброшена при загрузке
    assert sent(preview) == {"c7": ["s8"], "c12": ["s13"], "c14": ["s15"], "c15": ["s17"]}
    assert reasons(preview) == {
        "s7": ["аккаунт не состоит в чате"],
        "s14": ["писали 13 дней назад"],
        "s18": ["дата последней отправки в будущем (03.11) — проверьте данные"],
    }


def test_exactly_14_days_can_send(full_data):
    suppliers, chats = full_data
    sigma = pick(suppliers, "s13")
    written = datetime(2026, 10, 18, tzinfo=UTC)  # c12.last_outbound_at

    assert sent(build_preview(sigma, chats, written + timedelta(days=14))) == {"c12": ["s13"]}
    assert reasons(build_preview(sigma, chats, written + timedelta(days=14, seconds=-1))) == {
        "s13": ["писали 13 дней назад"],
    }


def test_threshold_is_parameter(task_data, now):
    suppliers, chats = task_data
    orbis = pick(suppliers, "s2")  # c2: писали 7 дней 10 часов назад

    assert sent(build_preview(orbis, chats, now, resend_after=timedelta(days=7))) == {"c2": ["s2"]}
    assert reasons(build_preview(orbis, chats, now, resend_after=timedelta(days=30))) == {
        "s2": ["писали 7 дней назад"],
    }


def test_all_chat_reasons_at_once(task_data, now):
    suppliers, chats = task_data
    broken = [
        replace(chat, enabled=False, member=False, last_outbound_at=now - timedelta(hours=5))
        if chat.id == "c1" else chat
        for chat in chats
    ]

    preview = build_preview(pick(suppliers, "s1"), broken, now)

    assert reasons(preview) == {"s1": ["аккаунт не состоит в чате", "чат выключен", "писали меньше суток назад"]}


def test_supplier_in_several_chats_is_excluded(task_data, now):
    suppliers, chats = task_data
    second = Chat(id="c20", title="NordPack old", supplier_ids=("s1",), enabled=True, member=True, last_outbound_at=None)

    preview = build_preview(pick(suppliers, "s1", "s3"), [*chats, second], now)

    assert sent(preview) == {"c3": ["s3"]}
    assert reasons(preview) == {"s1": ["несколько чатов (NordPack x Us, NordPack old), нужно выбрать вручную"]}


@pytest.mark.parametrize(
    ("selected", "chats_sent", "excluded", "totals"),
    [
        (
            ACTIVE,
            ["c1", "c7", "c9", "c12", "c14", "c18", "c19"],
            ["s2", "s5", "s7", "s14", "s18"],
            Totals(selected=12, chats=7, excluded=5),
        ),
        (NEW, ["c3", "c11", "c15"], ["s6"], Totals(selected=5, chats=3, excluded=1)),
    ],
    ids=["active", "new"],
)
def test_full_data_totals_match_readme(full_data, now, selected, chats_sent, excluded, totals):
    suppliers, chats = full_data

    preview = build_preview(pick(suppliers, *selected), chats, now)

    assert list(sent(preview)) == chats_sent
    assert list(reasons(preview)) == excluded
    assert preview.totals == totals


@pytest.mark.parametrize(
    ("days", "text"),
    [(1, "1 день назад"), (3, "3 дня назад"), (5, "5 дней назад"), (11, "11 дней назад"),
     (13, "13 дней назад"), (21, "21 день назад"), (22, "22 дня назад")],
)
def test_days_ago_wording(days, text):
    assert ago(timedelta(days=days, hours=3)) == text
