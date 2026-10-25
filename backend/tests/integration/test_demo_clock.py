"""Демо-часы: текущее время в базе общее для API и воркера и перематывается только вперёд.

Данные здесь коммитятся по-настоящему: двое часов — как в API и в воркере — читают одну строку
на своих соединениях. Реальное время подставное, тесты от настоящего не зависят.
"""

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_clock
from app.clock import SharedClock
from app.config import settings
from app.db import engine
from app.main import app
from app.messenger import inbox_replay
from app.messenger.sender import StubSender
from app.models import Base
from app.seed import load
from app.worker import receive, tick

DATA = settings.data_dir
START = datetime(2026, 11, 1, 10, tzinfo=UTC)
MSK = timezone(timedelta(hours=3))
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


class RealTime:
    """Подставной источник реального времени: идёт, только когда его подвинули."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 24, 15, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture(autouse=True)
def clean(schema):
    """После теста база снова пустая: остальные тесты работают в откатываемой транзакции и ждут её такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def real():
    return RealTime()


@pytest.fixture
def app_clock(real):
    return SharedClock(engine, START, real)


@pytest.fixture
def worker_clock(real):
    return SharedClock(engine, START, real)


@pytest.fixture
def api(app_clock):
    """API на настоящих сессиях: что сделали запросы, видит воркер."""
    app.dependency_overrides[get_clock] = lambda: app_clock
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def moment(value):
    return datetime.fromisoformat(value)


def test_clocks_show_same_time(real, app_clock, worker_clock):
    assert app_clock.now() == worker_clock.now() == START

    real.value += timedelta(minutes=5, seconds=30.5)

    assert app_clock.now() == worker_clock.now() == START + timedelta(minutes=5, seconds=30.5)
    assert app_clock.now().tzinfo is UTC


def test_restart_continues_from_base(real, app_clock):
    app_clock.now()
    real.value += timedelta(hours=2)

    # Перезапущенный процесс идёт от строки в базе, а не с начала и не от нового CLOCK_START
    restarted = SharedClock(engine, datetime(2026, 12, 1, tzinfo=UTC), real)

    assert restarted.now() == START + timedelta(hours=2)


def test_start_in_other_zone_is_utc(real):
    clock = SharedClock(engine, datetime(2026, 11, 1, 13, tzinfo=MSK), real)

    assert clock.now() == START
    assert clock.now().tzinfo is UTC


def test_start_without_zone_is_rejected(real):
    with pytest.raises(ValueError):
        SharedClock(engine, datetime(2026, 11, 1, 10), real)


def test_advance_is_seen_by_both(real, app_clock, worker_clock):
    app_clock.advance(timedelta(days=1))

    assert worker_clock.now() == app_clock.now() == START + timedelta(days=1)

    worker_clock.advance(timedelta(hours=1))
    real.value += timedelta(seconds=10)

    assert app_clock.now() == worker_clock.now() == START + timedelta(days=1, hours=1, seconds=10)


def test_advance_before_first_now(app_clock, worker_clock):
    app_clock.advance(timedelta(hours=3))

    assert worker_clock.now() == START + timedelta(hours=3)


def test_advance_backwards_is_rejected(app_clock):
    app_clock.advance(timedelta(hours=1))

    for delta in (timedelta(hours=-1), timedelta(0)):
        with pytest.raises(ValueError):
            app_clock.advance(delta)

    assert app_clock.now() == START + timedelta(hours=1)


def test_api_now_and_advance(api, real):
    assert moment(api.get("/api/clock").json()["now"]) == START

    day = api.post("/api/clock/advance", json={"by": "P1D"})

    assert day.status_code == 200
    assert moment(day.json()["now"]) == START + timedelta(days=1)
    real.value += timedelta(seconds=3)
    assert moment(api.get("/api/clock").json()["now"]) == START + timedelta(days=1, seconds=3)

    hour = api.post("/api/clock/advance", json={"by": "PT1H"}).json()
    seconds = api.post("/api/clock/advance", json={"by": 90}).json()

    assert moment(hour["now"]) == START + timedelta(days=1, hours=1, seconds=3)
    assert moment(seconds["now"]) == START + timedelta(days=1, hours=1, seconds=93)


def test_api_rejects_backwards_and_huge(api):
    for by in ("-PT1H", 0, "P366D"):
        response = api.post("/api/clock/advance", json={"by": by})

        assert response.status_code == 422
        [reason] = response.json()["detail"]
        assert reason.startswith("by: ")

    assert moment(api.get("/api/clock").json()["now"]) == START


def test_worker_sees_advance_from_api(api, worker_clock):
    """Рассылка в c3 на данных задания: Kestrel Foods отвечает через 30 часов, Kestrel Foods EU — через 110."""
    with Session(engine) as session:
        load(session, DATA)
        session.commit()
    campaign = api.post("/api/campaigns", json={"preset": "inactive_new", "template": TEMPLATE}).json()
    sender = StubSender()
    tick(worker_clock, sender, random.Random(0))
    assert [chat_id for chat_id, _ in sender.sent] == ["c3"]
    scenario = inbox_replay.load(DATA)

    def statuses():
        suppliers = api.get(f"/api/campaigns/{campaign['id']}/report").json()["suppliers"]
        return {supplier["id"]: supplier["status"] for supplier in suppliers}

    receive(worker_clock, scenario)
    assert statuses() == {"s3": "sent", "s4": "sent", "s6": "excluded"}

    # 72 часа: ответ с t = 30 пришёл, второй поставщик молчит
    api.post("/api/clock/advance", json={"by": "PT72H"})
    receive(worker_clock, scenario)
    assert statuses() == {"s3": "replied", "s4": "silent", "s6": "excluded"}

    # t = 110: поздний ответ снимает «молчит»
    api.post("/api/clock/advance", json={"by": "PT38H"})
    receive(worker_clock, scenario)
    assert statuses() == {"s3": "replied", "s4": "replied", "s6": "excluded"}
