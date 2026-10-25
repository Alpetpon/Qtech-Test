import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_clock
from app.clock import ManualClock
from app.config import settings
from app.db import engine
from app.domain.replies import QUIET, Incoming
from app.domain.statuses import SILENCE
from app.llm.client import ReplayClient
from app.main import app
from app.messenger import inbox_replay
from app.messenger.sender import StubSender
from app.models import Base, CampaignSupplier, InboxMessage, OutboxMessage
from app.seed import load
from app.services.sending import ready_at
from app.worker import analyze, receive, start, tick

DATA = settings.data_dir
SETS = {"tz": (DATA,), "full": (DATA, DATA / "extra")}
FIXTURES = Path(__file__).parents[1] / "fixtures" / "llm"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
# Последнее сообщение сценария — t = 400 ч (16,7 дня) от отправки в c1
END = NOW + timedelta(days=17)
ACTIVE, NEW = "stale_price", "inactive_new"

NORDPACK_OFFER = "Добрый день! Коробки 40x30x30 — 0.42 EUR/шт, минимальная партия 5000 шт"
NORDPACK_TERMS = "Срок поставки 10 дней, доставляем по EU"
NORDPACK_UPDATE = "Обновление: коробки теперь 0.39 EUR/шт"
KESTREL_EU_REFUSAL = "По EU сейчас ничего не можем, спасибо"

# Итоги README: «Активные» — 12 выбрано, 7 чатов, 5 исключено; «Новые» — 5, 3, 1; на данных ТЗ — c1 и c3
PREVIEWS = {
    "tz": {
        ACTIVE: ({"selected": 3, "chats": 1, "excluded": 2}, ["c1"]),
        NEW: ({"selected": 3, "chats": 1, "excluded": 1}, ["c3"]),
    },
    "full": {
        ACTIVE: ({"selected": 12, "chats": 7, "excluded": 5}, ["c1", "c12", "c14", "c18", "c19", "c7", "c9"]),
        NEW: ({"selected": 5, "chats": 3, "excluded": 1}, ["c11", "c15", "c3"]),
    },
}

FINAL = {
    "tz": (
        {"s1": "offer", "s2": "excluded", "s3": "offer", "s4": "refusal", "s5": "excluded", "s6": "excluded"},
        [],
    ),
    "full": (
        {
            "s1": "offer",
            "s2": "excluded",
            "s3": "offer",
            "s4": "refusal",
            "s5": "excluded",
            "s6": "excluded",
            "s7": "excluded",
            "s8": "offer",
            "s10": "offer",
            "s12": "other",
            "s13": "offer",
            "s14": "excluded",
            "s15": "offer",
            "s17": "offer",
            "s18": "excluded",
            "s20": "offer",
            "s21": "silent",
        },
        ["@kestrel_office"],
    ),
}


@dataclass(frozen=True)
class Run:
    """Что произошло за сценарий и что по дороге видел проверяющий."""

    name: str
    template: str
    previews: dict[str, Any]
    campaigns: dict[str, int]
    # Каждый процесс воркера — свой отправитель: чаты, в которые он отправил, по порядку
    sent: list[list[str]]
    # Список рассылок и общий аккаунт, как их показывал API во время паузы RATE_LIMIT
    paused: dict[str, Any]
    # Статусы поставщиков в рассылках по времени: (момент, статус), только смены
    history: dict[str, list[tuple[datetime, str]]]


@pytest.fixture(scope="module", params=SETS)
def run(request, schema):
    try:
        yield play(request.param)
    finally:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def clock(run):
    """Человек открывает отчёт, когда сценарий закончился."""
    return ManualClock(END)


def play(name: str) -> Run:
    dirs = SETS[name]
    clock = ManualClock(NOW)
    with Session(engine) as session:
        load(session, *dirs)
        session.commit()

    app.dependency_overrides[get_clock] = lambda: clock
    try:
        with TestClient(app) as api:
            template = api.get("/api/options").json()["default_template"]
            previews, campaigns = {}, {}
            for preset in (ACTIVE, NEW):
                launch = {"preset": preset, "template": template}
                previews[preset] = api.post("/api/campaigns/preview", json=launch).json()
                created = api.post("/api/campaigns", json=launch)
                assert created.status_code == 201, created.json()
                campaigns[preset] = created.json()["id"]

            scenario = inbox_replay.load(*dirs)
            model = ReplayClient(FIXTURES)
            rng = random.Random(23)
            # Мессенджер отвечает RATE_LIMIT на вторую попытку отправки
            senders = [StubSender(lambda attempt: attempt == 2)]
            paused: dict[str, Any] = {}
            history: dict[str, list[tuple[datetime, str]]] = {}
            remember(history, clock.now())
            start(clock, rng)
            while True:
                tick(clock, senders[-1], rng)
                receive(clock, scenario)
                assert not analyze(clock, model), "ответ не разобран: записи ответа модели нет, причина — в логе"
                remember(history, clock.now())
                if not paused and senders[-1].attempts > len(senders[-1].sent):
                    paused = {
                        "campaigns": api.get("/api/campaigns").json(),
                        "sender": api.get(f"/api/campaigns/{campaigns[ACTIVE]}").json()["sender"],
                    }
                    # Перезапуск сервиса посреди паузы: новый процесс воркера со своим отправителем
                    senders.append(StubSender())
                    start(clock, rng)
                moment = next_event(clock.now(), scenario)
                if moment is None or moment > END:
                    break
                clock.set(moment)
    finally:
        app.dependency_overrides.pop(get_clock, None)

    sent = [[chat_id for chat_id, _ in sender.sent] for sender in senders]
    return Run(name, template, previews, campaigns, sent, paused, history)


def remember(history: dict[str, list[tuple[datetime, str]]], now: datetime) -> None:
    with Session(engine) as session:
        for supplier_id, status in session.execute(select(CampaignSupplier.supplier_id, CampaignSupplier.status)):
            changes = history.setdefault(supplier_id, [])
            if not changes or changes[-1][1] != status:
                changes.append((now, status))


def next_event(now: datetime, scenario: Sequence[Incoming]) -> datetime | None:
    """Ближайший момент, когда воркеру есть что делать. None — больше ничего не случится."""
    moments = []
    with Session(engine) as session:
        if session.scalar(select(exists().where(OutboxMessage.status == "pending"))):
            # Конец паузы 1–3 минуты или паузы RATE_LIMIT, место в суточном лимите
            moments.append(ready_at(session, now))
        for outbox in session.scalars(select(OutboxMessage).where(OutboxMessage.status == "sent")):
            moments.append(outbox.sent_at + SILENCE)
            for incoming in scenario:
                if incoming.chat_id == outbox.chat_id:
                    received = incoming.arrive(outbox.sent_at).received_at
                    # Через 10 минут тишины ответ закрыт и уходит в модель
                    moments += [received, received + QUIET]
    return min((moment for moment in moments if moment > now), default=None)


def sent_at() -> dict[str, datetime]:
    with Session(engine) as session:
        return dict(session.execute(select(OutboxMessage.chat_id, OutboxMessage.sent_at)).all())


def received_at(chat_id: str, message: str) -> datetime:
    with Session(engine) as session:
        return session.scalar(
            select(InboxMessage.received_at).where(InboxMessage.chat_id == chat_id, InboxMessage.text == message)
        )


def excluded(preview: dict[str, Any]) -> dict[str, list[str]]:
    return {item["supplier"]["id"]: item["reasons"] for item in preview["excluded"]}


def outgoing(preview: dict[str, Any]) -> list[str]:
    return [supplier["id"] for item in preview["outgoing"] for supplier in item["suppliers"]]


def supplier(client: TestClient, run: Run, preset: str, supplier_id: str) -> dict[str, Any]:
    report = client.get(f"/api/campaigns/{run.campaigns[preset]}/report").json()
    return next(item for item in report["suppliers"] if item["id"] == supplier_id)


def fields(offer: dict[str, Any]) -> dict[str, Any]:
    keys = ("product", "price", "currency", "moq", "lead_time_days", "regions")
    return {key: offer[key] for key in keys}


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_presets_and_previews_match_readme(run):
    for preset, (totals, chats) in PREVIEWS[run.name].items():
        preview = run.previews[preset]
        assert preview["totals"] == totals
        assert sorted(item["chat"]["id"] for item in preview["outgoing"]) == chats
        assert preview["problems"] == []


def test_orbis_is_excluded_by_recent_message_and_its_blank_manager_does_not_block(run):
    preview = run.previews[ACTIVE]
    # Писали в c2 25.10 — меньше 14 дней назад; пустой менеджер Orbis Trade неважен: сообщение ему не уходит
    assert excluded(preview)["s2"] == ["писали 7 дней назад"]
    assert preview["problems"] == []


def test_velta_in_disabled_chat_is_excluded(run):
    assert excluded(run.previews[ACTIVE])["s5"] == ["чат выключен"]


def test_quasar_without_chat_is_excluded(run):
    assert excluded(run.previews[NEW])["s6"] == ["нет чата"]


def test_orbis_blank_manager_blocks_launch_once_its_chat_is_free(client, run):
    # Через 17 дней в c2 уже можно писать, и сообщение Orbis Trade собирать не из чего
    launch = {"preset": ACTIVE, "template": run.template}
    preview = client.post("/api/campaigns/preview", json=launch).json()
    assert "s2" in outgoing(preview)
    assert "У Orbis Trade пустой менеджер" in preview["problems"]

    refused = client.post("/api/campaigns", json=launch)
    assert refused.status_code == 422
    assert "У Orbis Trade пустой менеджер" in refused.json()["detail"]


def test_kestrel_group_gets_one_message_for_both_suppliers(run):
    [item] = [item for item in run.previews[NEW]["outgoing"] if item["chat"]["id"] == "c3"]
    assert [supplier["id"] for supplier in item["suppliers"]] == ["s3", "s4"]
    with Session(engine) as session:
        [message] = session.scalars(select(OutboxMessage).where(OutboxMessage.chat_id == "c3")).all()
    assert message.text.startswith("Здравствуйте, Kestrel Foods, Kestrel Foods EU!")
    assert message.text.endswith("С уважением, Игорь")
    assert sum(chats.count("c3") for chats in run.sent) == 1


def test_queue_waits_1_to_3_minutes_and_stops_on_rate_limit(run):
    with Session(engine) as session:
        times = session.scalars(select(OutboxMessage.sent_at).order_by(OutboxMessage.sent_at)).all()
    pause = run.paused["sender"]["pause"]
    assert pause["reason"] == "RATE_LIMIT"
    # Вторая отправка получила RATE_LIMIT через 1–3 минуты после первой, сообщение ушло сразу после паузы
    assert timedelta(minutes=1) <= moment(pause["since"]) - times[0] <= timedelta(minutes=3)
    assert moment(pause["until"]) - moment(pause["since"]) == settings.rate_limit_pause
    assert times[1] == moment(pause["until"])
    assert all(
        timedelta(minutes=1) <= later - earlier <= timedelta(minutes=3) for earlier, later in pairwise(times[1:])
    )
    # На паузе стоят все рассылки, где ещё есть что отправлять
    waiting = [campaign["status"] for campaign in run.paused["campaigns"] if campaign["queue"]["pending"]]
    assert waiting and set(waiting) == {"paused"}
    assert (run.paused["sender"]["sent_last_day"], run.paused["sender"]["daily_limit"]) == (1, 100)


def test_restart_during_pause_sends_nothing_twice(run):
    with Session(engine) as session:
        chats = session.scalars(select(OutboxMessage.chat_id).order_by(OutboxMessage.sent_at)).all()
        statuses = set(session.scalars(select(OutboxMessage.status)))
    before, after = run.sent
    assert before == chats[:1]
    # Сообщение, получившее RATE_LIMIT, ушло после перезапуска одним сообщением, остальные — по разу
    assert after == chats[1:]
    assert statuses == {"sent"}


def test_replies_are_timed_from_actual_send_not_from_task_now(run):
    sent = sent_at()
    # c3 ушёл не во время, указанное в задании: после паузы 1–3 минуты и паузы RATE_LIMIT
    assert sent["c3"] > NOW
    assert received_at("c1", NORDPACK_TERMS) == sent["c1"] + timedelta(hours=2, minutes=1, seconds=48)
    assert received_at("c3", KESTREL_EU_REFUSAL) == sent["c3"] + timedelta(hours=110)


def test_anna_buy_message_is_not_a_reply(client, run):
    with Session(engine) as session:
        message = session.scalars(select(InboxMessage).where(InboxMessage.sender == "@anna_buy")).one()
    assert (message.mark, message.reply_id) == ("наш сотрудник", None)
    nordpack = supplier(client, run, ACTIVE, "s1")
    assert [message["sender"] for reply in nordpack["replies"] for message in reply["messages"]] == [
        "@nordpack_sales",
        "@nordpack_sales",
    ]


def test_nordpack_two_messages_make_one_offer(client, run):
    nordpack = supplier(client, run, ACTIVE, "s1")
    assert nordpack["status"] == "offer"
    [reply] = nordpack["replies"]
    assert [message["text"] for message in reply["messages"]] == [NORDPACK_OFFER, NORDPACK_TERMS]
    [offer] = reply["offers"]
    assert fields(offer) == {
        "product": "Коробки 40x30x30",
        "price": "0.42",
        "currency": "EUR",
        "moq": 5000,
        "lead_time_days": 10,
        "regions": ["EU"],
    }
    assert offer["quote"] == f"{NORDPACK_OFFER}\n{NORDPACK_TERMS}"
    assert offer["review"] == []


def test_kestrel_offer_without_price_cannot_be_applied_until_price_is_entered(client, run):
    kestrel = supplier(client, run, NEW, "s3")
    assert kestrel["status"] == "offer"
    [reply] = kestrel["replies"]
    assert reply["notes"] == ["цена во вложении: price_2026.pdf", "прежний товар больше не производят"]
    [offer] = reply["offers"]
    assert (offer["product"], offer["price"], offer["currency"]) == ("пищевые контейнеры", None, None)

    refused = client.post(f"/api/offers/{offer['id']}/apply")
    assert refused.status_code == 422
    assert refused.json()["detail"] == ["цена не указана", "валюта не указана"]

    edited = client.put(f"/api/offers/{offer['id']}", json=fields(offer) | {"price": "1.15", "currency": "EUR"})
    assert edited.json()["review"] == []
    applied = client.post(f"/api/offers/{offer['id']}/apply")
    assert applied.status_code == 200
    assert (applied.json()["price"], applied.json()["currency"]) == ("1.15", "EUR")


def test_kestrel_eu_is_silent_after_72_hours_then_refuses(run):
    sent = sent_at()["c3"]
    assert run.history["s4"] == [
        (NOW, "queued"),
        (sent, "sent"),
        (sent + timedelta(hours=72), "silent"),
        (sent + timedelta(hours=110), "replied"),
        (sent + timedelta(hours=110) + QUIET, "refusal"),
    ]


def test_nordpack_update_after_14_days_stays_unbound(client, run):
    late = [item for item in client.get("/api/inbox/unbound").json() if item["sender"] == "@nordpack_sales"]
    assert [(item["text"], item["mark"], item["campaign_id"]) for item in late] == [(NORDPACK_UPDATE, "вне окна", None)]
    assert moment(late[0]["received_at"]) == sent_at()["c1"] + timedelta(hours=400)
    [offer] = supplier(client, run, ACTIVE, "s1")["replies"][0]["offers"]
    assert (offer["price"], offer["supplement_at"]) == ("0.42", None)


def test_offer_is_applied_once(client, run):
    [offer] = supplier(client, run, ACTIVE, "s1")["replies"][0]["offers"]
    client.put(f"/api/offers/{offer['id']}", json=fields(offer) | {"price": "0.40"})

    applied = client.post(f"/api/offers/{offer['id']}/apply")
    assert applied.status_code == 200
    assert fields(applied.json()) == fields(offer) | {"price": "0.40"}
    again = client.post(f"/api/offers/{offer['id']}/apply")
    assert again.status_code == 409
    assert again.json() == {"detail": ["предложение уже применено"]}
    assert client.put(f"/api/offers/{offer['id']}", json=fields(offer)).status_code == 409

    assert moment(supplier(client, run, ACTIVE, "s1")["price_updated_at"]) == END
    # Прайс свежий — пресет «Активные, прайс устарел» NordPack больше не выбирает
    preview = client.post("/api/campaigns/preview", json={"preset": ACTIVE, "template": run.template}).json()
    assert "s1" not in outgoing(preview) + list(excluded(preview))


def test_every_supplier_ends_with_expected_status(client, run):
    statuses, unassigned = {}, []
    for campaign_id in run.campaigns.values():
        report = client.get(f"/api/campaigns/{campaign_id}/report").json()
        statuses |= {item["id"]: item["status"] for item in report["suppliers"]}
        unassigned += [reply["sender"] for reply in report["unassigned"]]
    assert (statuses, unassigned) == FINAL[run.name]
