"""HTTP API на данных задания: превью, запуск, статус рассылки, отчёт и «Применить».

Воркера в тестах нет: его шаги — отправку, входящие и разбор — тест делает сам, на часах API.
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY

from app.config import settings
from app.domain.replies import QUIET
from app.llm.client import ReplayClient
from app.messenger import inbox_replay
from app.messenger.sender import StubSender
from app.models import InboxMessage
from app.seed import load
from app.services import analysis, inbox, statuses
from app.services.sending import send_next

DATA = settings.data_dir
FIXTURES = Path(__file__).parents[1] / "fixtures" / "llm"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"
NORDPACK = {"id": "c1", "title": "NordPack x Us"}


def launch(client, **selection):
    response = client.post("/api/campaigns", json={"template": TEMPLATE, **selection})
    assert response.status_code == 201, response.json()
    return response.json()


def send(session, clock, sender=None):
    """Шаг очереди, как его делает воркер."""
    send_next(session, clock, sender or StubSender(), random.Random(0))


def receive(session, clock, moment):
    """Шаг воркера на момент moment: входящие, статусы, затем разбор закрытых ответов моделью."""
    clock.set(moment)
    inbox_replay.deliver(session, clock, inbox_replay.load(DATA))
    inbox.process(session)
    statuses.refresh(session, clock)
    analysis.run(session, clock, ReplayClient(FIXTURES), settings.llm_model)


def after(t):
    """Момент через t часов после отправки: очередь отправляет в момент NOW."""
    return NOW + timedelta(seconds=int(Decimal(str(t)) * 3600))


def closed(t):
    """Момент, когда ответ с последним сообщением в t закрыт: 10 минут тишины."""
    return after(t) + QUIET


def moment(value):
    return datetime.fromisoformat(value)


def fields(offer):
    return {name: offer[name] for name in ("product", "price", "currency", "moq", "lead_time_days", "regions")}


def test_health(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_openapi_lists_routes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths) >= {
        "/api/options",
        "/api/campaigns/preview",
        "/api/campaigns",
        "/api/campaigns/{campaign_id}",
        "/api/campaigns/{campaign_id}/report",
        "/api/sender/resume",
        "/api/offers/{offer_id}",
        "/api/offers/{offer_id}/supplier",
        "/api/offers/{offer_id}/apply",
        "/api/inbox/unbound",
    }
    # Отказы и неверные запросы — одна модель: фронтенд разбирает их одинаково
    refusal = paths["/api/offers/{offer_id}/apply"]["post"]["responses"]
    assert {refusal[code]["content"]["application/json"]["schema"]["$ref"] for code in ("404", "409", "422")} == {
        "#/components/schemas/Problem"
    }


def test_options(client):
    body = client.get("/api/options").json()

    assert body["presets"] == [
        {"id": "stale_price", "title": "Активные, прайс устарел"},
        {"id": "inactive_new", "title": "Новые без движения"},
    ]
    assert body["statuses"] == ["active", "new"]
    assert "{supplier}" in body["default_template"] and "{manager}" in body["default_template"]


def test_preview_active_preset(client, session):
    load(session, DATA)

    response = client.post("/api/campaigns/preview", json={"preset": "stale_price", "template": TEMPLATE})

    assert response.status_code == 200
    assert response.json() == {
        "totals": {"selected": 3, "chats": 1, "excluded": 2},
        "outgoing": [
            {
                "chat": NORDPACK,
                "suppliers": [{"id": "s1", "name": "NordPack"}],
                "text": "Здравствуйте, NordPack! Пришлите, пожалуйста, актуальный прайс.\nС уважением, Анна",
            }
        ],
        "excluded": [
            {"supplier": {"id": "s2", "name": "Orbis Trade"}, "reasons": ["писали 7 дней назад"]},
            {"supplier": {"id": "s5", "name": "Velta Textile"}, "reasons": ["чат выключен"]},
        ],
        "problems": [],
    }
    # Превью ничего не создаёт
    assert client.get("/api/campaigns").json() == []


def test_blank_manager_blocks_launch(client, session, clock):
    load(session, DATA)
    # Через 14 дней после отправки в c2 Orbis Trade уже не исключён, а менеджера у него нет
    clock.set(datetime(2026, 11, 8, tzinfo=UTC))
    selection = {"preset": "stale_price", "template": TEMPLATE}

    preview = client.post("/api/campaigns/preview", json=selection).json()
    response = client.post("/api/campaigns", json=selection)

    assert [(item["chat"]["id"], item["text"] is None) for item in preview["outgoing"]] == [("c1", False), ("c2", True)]
    assert preview["problems"] == ["У Orbis Trade пустой менеджер"]
    assert response.status_code == 422
    assert response.json() == {"detail": ["У Orbis Trade пустой менеджер"]}
    assert client.get("/api/campaigns").json() == []


def test_selection_is_preset_or_status(client, session):
    both = client.post(
        "/api/campaigns", json={"preset": "stale_price", "supplier_status": "active", "template": TEMPLATE}
    )
    unknown = client.post("/api/campaigns/preview", json={"preset": "everyone", "template": TEMPLATE})

    assert both.status_code == 422
    assert both.json() == {"detail": ["Выберите пресет или статус поставщиков — ровно одно из двух"]}
    assert unknown.status_code == 422
    [reason] = unknown.json()["detail"]
    assert reason.startswith("preset: ")


def test_launch_queues_and_sends(client, session, clock):
    load(session, DATA)

    campaign = launch(client, preset="stale_price")

    assert campaign == {
        "id": ANY,
        "preset": "stale_price",
        "supplier_status": None,
        "template": TEMPLATE,
        "created_at": ANY,
        "status": "sending",
        "queue": {"pending": 1, "sending": 0, "sent": 0, "unknown": 0},
    }
    assert moment(campaign["created_at"]) == NOW
    assert client.get("/api/campaigns").json() == [campaign]

    send(session, clock)

    status = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert (status["campaign"]["status"], status["campaign"]["queue"]) == (
        "sent",
        {"pending": 0, "sending": 0, "sent": 1, "unknown": 0},
    )
    assert status["sender"] == {"account": "@company_shared", "pause": None, "sent_last_day": 1, "daily_limit": 100}

    # Через сутки место в лимите освобождается
    clock.advance(timedelta(hours=24))
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["sender"]["sent_last_day"] == 0


def test_rate_limit_pauses_until_resume(client, session, clock):
    load(session, DATA)
    campaign = launch(client, preset="stale_price")

    send(session, clock, StubSender(lambda attempt: attempt == 1))

    status = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert (status["campaign"]["status"], status["campaign"]["queue"]["pending"]) == ("paused", 1)
    pause = status["sender"]["pause"]
    assert pause["reason"] == "RATE_LIMIT"
    assert (moment(pause["since"]), moment(pause["until"])) == (NOW, NOW + settings.rate_limit_pause)
    assert status["sender"]["sent_last_day"] == 0

    resumed = client.post("/api/sender/resume")
    assert resumed.status_code == 200
    assert resumed.json()["pause"] is None
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["campaign"]["status"] == "sending"

    send(session, clock)
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["campaign"]["status"] == "sent"


def test_unknown_campaign_is_404(client, session):
    for path in ("/api/campaigns/404", "/api/campaigns/404/report"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": ["рассылки №404 нет"]}


def test_report_edit_and_apply_once(client, session, clock):
    load(session, DATA)
    campaign = launch(client, preset="stale_price")
    send(session, clock)
    receive(session, clock, closed("2.03"))

    report = client.get(f"/api/campaigns/{campaign['id']}/report").json()

    nordpack, orbis, velta = report["suppliers"]
    assert (orbis["id"], orbis["status"], orbis["chat"], orbis["reasons"]) == (
        "s2",
        "excluded",
        None,
        ["писали 7 дней назад"],
    )
    assert (velta["id"], velta["status"], velta["reasons"]) == ("s5", "excluded", ["чат выключен"])
    assert (nordpack["id"], nordpack["status"], nordpack["chat"], nordpack["reasons"]) == ("s1", "offer", NORDPACK, [])
    # Сообщение @anna_buy — наша сотрудница, не ответ
    [reply] = nordpack["replies"]
    assert reply | {"messages": len(reply["messages"]), "offers": len(reply["offers"])} == {
        "id": ANY,
        "chat": NORDPACK,
        "sender": "@nordpack_sales",
        "messages": 2,
        "type": "offer",
        "notes": [],
        "review": [],
        "mark": None,
        "supplier": {"id": "s1", "name": "NordPack"},
        "supplier_hint": None,
        "candidates": [],
        "offers": 1,
    }
    [offer] = reply["offers"]
    assert offer == {
        "id": ANY,
        "supplier_id": "s1",
        "product": "Коробки 40x30x30",
        "price": "0.42",
        "currency": "EUR",
        "moq": 5000,
        "lead_time_days": 10,
        "regions": ["EU"],
        "quote": "Добрый день! Коробки 40x30x30 — 0.42 EUR/шт, минимальная партия 5000 шт\n"
        "Срок поставки 10 дней, доставляем по EU",
        "review": [],
        "edited_at": None,
        "applied_at": None,
        "supplement_at": None,
    }
    assert report["unassigned"] == []

    clock.advance(timedelta(hours=1))
    edited = client.put(f"/api/offers/{offer['id']}", json=fields(offer) | {"price": "0.40"})
    assert edited.status_code == 200
    assert (edited.json()["price"], moment(edited.json()["edited_at"])) == ("0.40", clock.now())

    clock.advance(timedelta(minutes=5))
    applied = client.post(f"/api/offers/{offer['id']}/apply")
    assert applied.status_code == 200
    assert applied.json() | {"applied_at": moment(applied.json()["applied_at"])} == {
        "id": ANY,
        "supplier_id": "s1",
        "offer_id": offer["id"],
        "product": "Коробки 40x30x30",
        "price": "0.40",
        "currency": "EUR",
        "moq": 5000,
        "lead_time_days": 10,
        "regions": ["EU"],
        "applied_at": clock.now(),
    }

    again = client.post(f"/api/offers/{offer['id']}/apply")
    assert again.status_code == 409
    assert again.json() == {"detail": ["предложение уже применено"]}
    late_edit = client.put(f"/api/offers/{offer['id']}", json=fields(offer))
    assert late_edit.status_code == 409

    nordpack = client.get(f"/api/campaigns/{campaign['id']}/report").json()["suppliers"][0]
    [offer] = nordpack["replies"][0]["offers"]
    assert (offer["price"], moment(offer["applied_at"])) == ("0.40", clock.now())
    assert moment(nordpack["price_updated_at"]) == clock.now()


def test_offer_without_price_is_not_applied(client, session, clock):
    load(session, DATA)
    campaign = launch(client, preset="inactive_new")  # c3: Kestrel Foods и Kestrel Foods EU
    send(session, clock)
    receive(session, clock, closed(30))

    kestrel, kestrel_eu, quasar = client.get(f"/api/campaigns/{campaign['id']}/report").json()["suppliers"]

    assert (quasar["id"], quasar["status"], quasar["reasons"]) == ("s6", "excluded", ["нет чата"])
    assert (kestrel_eu["id"], kestrel_eu["status"], kestrel_eu["replies"]) == ("s4", "sent", [])
    [reply] = kestrel["replies"]
    assert reply["notes"] == ["цена во вложении: price_2026.pdf", "прежний товар больше не производят"]
    assert reply["messages"][0]["attachment"] == "price_2026.pdf"
    [offer] = reply["offers"]
    assert (offer["product"], offer["price"], offer["review"]) == (
        "пищевые контейнеры",
        None,
        ["цена не указана", "валюта не указана"],
    )

    response = client.post(f"/api/offers/{offer['id']}/apply")

    assert response.status_code == 422
    assert response.json() == {"detail": ["цена не указана", "валюта не указана"]}
    missing = client.post("/api/offers/404404/apply")
    assert (missing.status_code, missing.json()) == (404, {"detail": ["предложения №404404 нет"]})


def test_unknown_handle_offer_needs_supplier(client, session, clock):
    load(session, DATA)
    campaign = launch(client, preset="inactive_new")
    send(session, clock)
    session.add(
        InboxMessage(
            chat_id="c3",
            external_id="test:kestrel_team",
            sender="@kestrel_team",
            received_at=after(5),
            text="Пишу от Kestrel Foods EU: контейнеры пищевые 500 мл — 0.30 EUR/шт, партия от 1000 шт",
        )
    )
    receive(session, clock, closed(5))

    report = client.get(f"/api/campaigns/{campaign['id']}/report").json()

    assert [supplier["replies"] for supplier in report["suppliers"]] == [[], [], []]
    [reply] = report["unassigned"]
    assert (reply["mark"], reply["supplier"], reply["supplier_hint"]) == (
        "непонятно, от какой компании",
        None,
        {"id": "s4", "name": "Kestrel Foods EU"},
    )
    assert reply["candidates"] == [{"id": "s3", "name": "Kestrel Foods"}, {"id": "s4", "name": "Kestrel Foods EU"}]
    [offer] = reply["offers"]
    assert (offer["supplier_id"], offer["review"]) == (None, ["поставщик не выбран"])

    stranger = client.put(f"/api/offers/{offer['id']}/supplier", json={"supplier_id": "s1"})
    assert stranger.status_code == 422
    assert stranger.json() == {"detail": ["выбрать можно только поставщика этого чата, которого спросили в рассылке"]}

    chosen = client.put(f"/api/offers/{offer['id']}/supplier", json={"supplier_id": "s4"})
    assert chosen.status_code == 200
    assert (chosen.json()["supplier_id"], chosen.json()["review"]) == ("s4", [])
    assert client.post(f"/api/offers/{offer['id']}/apply").json()["supplier_id"] == "s4"

    [message] = client.get("/api/inbox/unbound").json()
    assert (message["sender"], message["mark"], message["campaign_id"]) == (
        "@kestrel_team",
        "непонятно, от какой компании",
        campaign["id"],
    )


def test_message_after_window_is_unbound(client, session, clock):
    load(session, DATA)
    launch(client, preset="stale_price")
    send(session, clock)

    receive(session, clock, after(400))

    # Сообщение @anna_buy — наше, его в списке нет
    assert client.get("/api/inbox/unbound").json() == [
        {
            "id": ANY,
            "chat": NORDPACK,
            "sender": "@nordpack_sales",
            "received_at": ANY,
            "text": "Обновление: коробки теперь 0.39 EUR/шт",
            "attachment": None,
            "mark": "вне окна",
            "campaign_id": None,
        }
    ]
