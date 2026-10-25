"""«Применить»: предложение попадает в прайс поставщика один раз, применённое заморожено."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import ManualClock
from app.config import settings
from app.db import engine
from app.domain.replies import QUIET
from app.domain.selection import stale_price
from app.llm.client import ReplayClient
from app.messenger import inbox_replay
from app.models import Base, InboxMessage, Offer, Price, Supplier
from app.seed import load
from app.services import campaigns
from app.services.analysis import run
from app.services.inbox import process
from app.services.offers import AlreadyApplied, Refused, apply, choose_supplier, edit, fields
from app.services.statuses import refresh

DATA = settings.data_dir
FIXTURES = Path(__file__).parents[1] / "fixtures" / "llm"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


@pytest.fixture
def clock():
    return ManualClock(NOW)


@pytest.fixture
def clean(schema):
    """После теста база снова пустая: остальные тесты работают в откатываемой транзакции и ждут её такой."""
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def launch(session, clock, preset):
    """Рассылка, которую очередь уже отправила: воркера в тестах нет, отметки ставим, как ставит он."""
    campaign = campaigns.create(session, clock, TEMPLATE, preset=preset)
    for message in campaign.messages:
        message.status, message.sent_at = "sent", clock.now()
    for row in campaign.suppliers:
        if row.status == "queued":
            row.status = "sent"
    session.flush()


def after(t):
    return NOW + timedelta(seconds=int(Decimal(str(t)) * 3600))


def closed(t):
    """Момент, когда ответ с последним сообщением в t закрыт: 10 минут тишины."""
    return after(t) + QUIET


def step(session, clock, scenario, moment):
    """Шаг воркера на момент moment: входящие, статусы, затем разбор закрытых ответов."""
    clock.set(moment)
    inbox_replay.deliver(session, clock, scenario)
    process(session)
    refresh(session, clock)
    run(session, clock, ReplayClient(FIXTURES), settings.llm_model)


def write(session, chat_id, sender, received_at, text):
    """Сообщение не из сценария — как пришло бы из настоящего мессенджера."""
    session.add(
        InboxMessage(
            chat_id=chat_id, external_id=f"test:{received_at}", sender=sender, received_at=received_at, text=text
        )
    )
    session.flush()


def nordpack(session, clock):
    """Предложение NordPack из данных ТЗ: 0.42 EUR, партия 5000, 10 дней, EU."""
    load(session, DATA)
    launch(session, clock, "stale_price")  # c1 NordPack
    step(session, clock, inbox_replay.load(DATA), closed("2.03"))
    [offer] = offers(session, "s1")
    return offer


def offers(session, supplier_id):
    return session.scalars(select(Offer).where(Offer.supplier_id == supplier_id).order_by(Offer.id)).all()


def prices(session):
    return session.scalars(select(Price).order_by(Price.id)).all()


def stale(session, clock):
    """Кого сейчас выбрал бы пресет «Активные, прайс устарел»."""
    suppliers, _ = campaigns.load(session)
    return [item.supplier.id for item in stale_price(suppliers, clock.now())]


def test_edited_offer_updates_supplier_price(session, clock):
    offer = nordpack(session, clock)
    assert "s1" in stale(session, clock)

    clock.advance(timedelta(hours=1))
    edit(session, clock, offer.id, replace(fields(offer), price=Decimal("0.40")))
    assert (offer.price, offer.edited_at, offer.applied_at) == (Decimal("0.40"), clock.now(), None)

    clock.advance(timedelta(minutes=5))
    apply(session, clock, offer.id)

    [price] = prices(session)
    assert (price.supplier_id, price.offer_id, price.applied_at) == ("s1", offer.id, clock.now())
    assert (price.product, price.price, price.currency) == ("Коробки 40x30x30", Decimal("0.40"), "EUR")
    assert (price.moq, price.lead_time_days, price.regions) == (5000, 10, ["EU"])
    assert offer.applied_at == clock.now()
    assert session.get(Supplier, "s1").price_updated_at == clock.now()
    assert "s1" not in stale(session, clock)


def test_second_apply_is_refused(session, clock):
    offer = nordpack(session, clock)
    apply(session, clock, offer.id)
    applied_at = clock.now()

    clock.advance(timedelta(minutes=1))
    with pytest.raises(AlreadyApplied, match="предложение уже применено"):
        apply(session, clock, offer.id)

    assert len(prices(session)) == 1
    assert offer.applied_at == session.get(Supplier, "s1").price_updated_at == applied_at


def test_two_applies_at_once_only_one_succeeds(clean, clock):
    with Session(engine) as session:
        offer_id = nordpack(session, clock).id
        session.commit()

    def apply_and_commit(session):
        apply(session, clock, offer_id)
        session.commit()

    with Session(engine) as first, Session(engine) as second, ThreadPoolExecutor(1) as pool:
        apply(first, clock, offer_id)
        # Первое «Применить» ещё не закоммичено: второе в другом подключении ждёт его, а не проскакивает
        later = pool.submit(apply_and_commit, second)
        with pytest.raises(TimeoutError):
            later.result(timeout=0.5)
        first.commit()
        with pytest.raises(AlreadyApplied):
            later.result(timeout=5)

    with Session(engine) as session:
        assert [price.offer_id for price in prices(session)] == [offer_id]


def test_price_table_takes_one_record_per_offer(session, clock):
    offer = nordpack(session, clock)
    price = apply(session, clock, offer.id)

    # Вторая защита: даже в обход отметки «применено» вторую запись из того же предложения база не примет
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Price(
                supplier_id="s1",
                offer_id=offer.id,
                product=price.product,
                price=price.price,
                currency=price.currency,
                regions=[],
                applied_at=clock.now(),
            )
        )
    assert len(prices(session)) == 1


def test_offer_without_price_waits_for_human(session, clock):
    load(session, DATA)
    launch(session, clock, "inactive_new")  # c3: Kestrel Foods и Kestrel Foods EU
    step(session, clock, inbox_replay.load(DATA), closed(30))
    [offer] = offers(session, "s3")
    priced_at = session.get(Supplier, "s3").price_updated_at
    assert offer.review == ["цена не указана", "валюта не указана"]

    with pytest.raises(Refused) as refused:
        apply(session, clock, offer.id)

    assert refused.value.reasons == ("цена не указана", "валюта не указана")
    assert (offer.applied_at, prices(session)) == (None, [])
    assert session.get(Supplier, "s3").price_updated_at == priced_at

    # Цену из price_2026.pdf вписал человек
    edit(session, clock, offer.id, replace(fields(offer), price=Decimal("0.18"), currency="EUR"))
    assert offer.review == []
    apply(session, clock, offer.id)

    [price] = prices(session)
    assert (price.supplier_id, price.product, price.price, price.currency) == (
        "s3",
        "пищевые контейнеры",
        Decimal("0.18"),
        "EUR",
    )
    assert session.get(Supplier, "s3").price_updated_at == clock.now()


def test_unknown_handle_needs_supplier_from_chat(session, clock):
    load(session, DATA)
    launch(session, clock, "inactive_new")
    write(
        session,
        "c3",
        "@kestrel_team",
        after(5),
        "Пишу от Kestrel Foods EU: контейнеры пищевые 500 мл — 0.30 EUR/шт, партия от 1000 шт",
    )
    step(session, clock, [], closed(5))
    [offer] = offers(session, None)
    assert offer.review == ["поставщик не выбран"]

    with pytest.raises(Refused) as refused:
        apply(session, clock, offer.id)
    assert refused.value.reasons == ("поставщик не выбран",)

    # NordPack не из этого чата
    with pytest.raises(Refused, match="только поставщика этого чата"):
        choose_supplier(session, clock, offer.id, "s1")
    assert offer.supplier_id is None

    clock.advance(timedelta(hours=1))
    choose_supplier(session, clock, offer.id, "s4")  # модель подсказала Kestrel Foods EU, выбрал человек
    assert (offer.supplier_id, offer.edited_at, offer.review) == ("s4", clock.now(), [])
    apply(session, clock, offer.id)

    [price] = prices(session)
    assert (price.supplier_id, price.price) == ("s4", Decimal("0.30"))
    assert session.get(Supplier, "s4").price_updated_at == clock.now()


def test_known_handle_supplier_is_not_chosen(session, clock):
    offer = nordpack(session, clock)

    with pytest.raises(Refused, match="поставщик известен по нику"):
        choose_supplier(session, clock, offer.id, "s1")
    assert offer.edited_at is None


def test_applied_offer_cannot_be_edited(session, clock):
    offer = nordpack(session, clock)
    apply(session, clock, offer.id)

    with pytest.raises(AlreadyApplied):
        edit(session, clock, offer.id, replace(fields(offer), price=Decimal("0.01")))

    session.refresh(offer)
    assert (offer.price, offer.edited_at) == (Decimal("0.42"), None)
    assert [price.price for price in prices(session)] == [Decimal("0.42")]


def test_supplement_after_apply_is_applied_separately(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)
    step(session, clock, scenario, closed("2.03"))
    [first] = offers(session, "s1")
    apply(session, clock, first.id)

    write(session, "c1", "@nordpack_sales", after(5), "Обновление: коробки теперь 0.39 EUR/шт")
    step(session, clock, scenario, closed(5))

    kept, supplement = offers(session, "s1")
    assert (kept.id, kept.price, kept.supplement_at) == (first.id, Decimal("0.42"), after(5))
    assert (supplement.price, supplement.applied_at) == (Decimal("0.39"), None)

    apply(session, clock, supplement.id)

    assert [(price.offer_id, price.price) for price in prices(session)] == [
        (first.id, Decimal("0.42")),
        (supplement.id, Decimal("0.39")),
    ]
    assert session.get(Supplier, "s1").price_updated_at == closed(5)
    with pytest.raises(AlreadyApplied):
        apply(session, clock, first.id)
