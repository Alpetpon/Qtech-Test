from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.clock import ManualClock
from app.config import settings
from app.domain.replies import QUIET
from app.llm import LLMError
from app.llm.client import ReplayClient
from app.messenger import inbox_replay
from app.models import CampaignSupplier, InboxMessage, Offer, Reply, SupplierContact
from app.seed import load
from app.services.analysis import Result, run
from app.services.campaigns import create
from app.services.inbox import process
from app.services.statuses import refresh

DATA = settings.data_dir
EXTRA = DATA / "extra"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "llm"
NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)

TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


class Spy:
    """Записанные ответы модели и тексты, которые ушли в модель."""

    def __init__(self):
        self.inner = ReplayClient(FIXTURES)
        self.texts = []

    def complete(self, request):
        self.texts.append(request.messages[-1]["content"])
        return self.inner.complete(request)


class Down:
    def complete(self, request):
        raise LLMError("Модель недоступна: таймаут")


class Garbage:
    def complete(self, request):
        return "Извините, не могу разобрать этот ответ"


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


def closed(t):
    """Момент, когда ответ с последним сообщением в t закрыт: 10 минут тишины."""
    return after(t) + QUIET


def step(session, clock, scenario, moment, client=None) -> Result:
    """Шаг воркера на момент moment: входящие, статусы, затем разбор закрытых ответов."""
    clock.set(moment)
    inbox_replay.deliver(session, clock, scenario)
    process(session)
    refresh(session, clock)
    return run(session, clock, client or ReplayClient(FIXTURES), settings.llm_model)


def write(session, chat_id, sender, received_at, text):
    """Сообщение не из сценария — как пришло бы из настоящего мессенджера."""
    session.add(
        InboxMessage(
            chat_id=chat_id, external_id=f"test:{received_at}", sender=sender, received_at=received_at, text=text
        )
    )
    session.flush()


def statuses(session):
    return {row.supplier_id: row.status for row in session.scalars(select(CampaignSupplier))}


def offers(session, supplier_id):
    return session.scalars(select(Offer).where(Offer.supplier_id == supplier_id).order_by(Offer.id)).all()


def replies_of(session, sender):
    return session.scalars(select(Reply).where(Reply.sender == sender).order_by(Reply.last_at)).all()


def test_two_messages_are_one_offer(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")  # c1 NordPack
    scenario = inbox_replay.load(DATA)

    assert step(session, clock, scenario, closed("2.03")) == Result(analyzed=1, failed=0)

    [offer] = offers(session, "s1")
    assert (offer.product, offer.price, offer.currency) == ("Коробки 40x30x30", Decimal("0.42"), "EUR")
    assert (offer.moq, offer.lead_time_days, offer.regions) == (5000, 10, ["EU"])
    assert offer.quote == (
        "Добрый день! Коробки 40x30x30 — 0.42 EUR/шт, минимальная партия 5000 шт\n"
        "Срок поставки 10 дней, доставляем по EU"
    )
    assert offer.review == []
    assert (offer.applied_at, offer.edited_at, offer.supplement_at) == (None, None, None)
    assert statuses(session)["s1"] == "offer"


def test_open_reply_is_not_sent_to_model(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)
    spy = Spy()

    # Второе сообщение NordPack пришло меньше 10 минут назад: ответ может продолжиться
    assert step(session, clock, scenario, closed("2.03") - timedelta(seconds=1), spy) == Result(0, 0)
    assert spy.texts == []
    [reply] = replies_of(session, "@nordpack_sales")
    assert (reply.type, reply.analyzed_at) == (None, None)
    assert statuses(session)["s1"] == "replied"

    step(session, clock, scenario, closed("2.03"), spy)
    assert len(spy.texts) == 1
    assert reply.analyzed_at == closed("2.03")


def test_offer_without_price_needs_review(session, clock):
    load(session, DATA)
    launch(session, clock, "inactive_new")  # c3: Kestrel Foods и Kestrel Foods EU
    scenario = inbox_replay.load(DATA)

    step(session, clock, scenario, closed(30))

    [reply] = replies_of(session, "@kestrel_sales")
    assert reply.type == "offer"
    assert reply.notes == ["цена во вложении: price_2026.pdf", "прежний товар больше не производят"]
    [offer] = offers(session, "s3")
    assert (offer.product, offer.price, offer.currency) == ("пищевые контейнеры", None, None)
    assert "цена не указана" in offer.review
    assert (statuses(session)["s3"], statuses(session)["s4"]) == ("offer", "sent")


def test_refusal_after_silence(session, clock):
    load(session, DATA)
    launch(session, clock, "inactive_new")
    scenario = inbox_replay.load(DATA)

    step(session, clock, scenario, after(72))
    assert statuses(session)["s4"] == "silent"
    step(session, clock, scenario, closed(110))

    [reply] = replies_of(session, "@kestrel_eu")
    assert reply.type == "refusal"
    assert offers(session, "s4") == []
    assert statuses(session) == {"s3": "offer", "s4": "refusal", "s6": "excluded"}


def test_unknown_handle_gets_hint_and_changes_no_status(session, clock):
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

    [reply] = replies_of(session, "@kestrel_team")
    assert (reply.type, reply.supplier_id, reply.supplier_hint) == ("offer", None, "s4")
    # Предложение остаётся на чате: поставщика выберет человек, подсказка — только подсказка
    [offer] = offers(session, None)
    assert (offer.reply_id, offer.price) == (reply.id, Decimal("0.30"))
    assert statuses(session) == {"s3": "sent", "s4": "sent", "s6": "excluded"}


def test_company_not_asked_is_not_sent_to_model(session, clock):
    load(session, DATA, EXTRA)
    # В c14 Delta Group пресет выбрал только Delta Tools (s15)
    launch(session, clock, "stale_price")
    session.add(SupplierContact(handle="@delta_logistics", supplier_id="s16"))
    write(session, "c14", "@delta_logistics", after(3), "Пришлём прайс завтра")
    spy = Spy()

    step(session, clock, inbox_replay.load(DATA, EXTRA), closed(3), spy)

    assert [text for text in spy.texts if "Пришлём прайс завтра" in text] == []
    [reply] = replies_of(session, "@delta_logistics")
    assert (reply.mark, reply.type) == ("не по рассылке", None)


def test_message_after_reply_window_is_not_sent_to_model(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)
    step(session, clock, scenario, closed("2.03"))
    spy = Spy()

    # «Обновление: коробки теперь 0.39 EUR/шт» пришло через 400 ч — позже окна 14 дней
    step(session, clock, scenario, closed(400), spy)

    assert spy.texts == []
    [offer] = offers(session, "s1")
    assert offer.price == Decimal("0.42")


def test_garbage_from_model_is_other_for_review(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)

    assert step(session, clock, scenario, closed("2.03"), Garbage()) == Result(1, 0)

    [reply] = replies_of(session, "@nordpack_sales")
    assert reply.type == "other"
    assert reply.review[0].startswith("ответ модели не разобран")
    assert offers(session, "s1") == []
    assert statuses(session)["s1"] == "other"


def test_model_down_reply_waits_for_next_step(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)

    assert step(session, clock, scenario, closed("2.03"), Down()) == Result(0, 1)
    [reply] = replies_of(session, "@nordpack_sales")
    assert (reply.type, reply.analyzed_at) == (None, None)
    assert statuses(session)["s1"] == "replied"

    assert step(session, clock, scenario, closed("2.03") + timedelta(minutes=1)) == Result(1, 0)
    assert reply.type == "offer"
    assert statuses(session)["s1"] == "offer"


def test_supplement_is_analyzed_with_earlier_messages(session, clock):
    load(session, DATA, EXTRA)
    launch(session, clock, "inactive_new")  # в том числе c15 Orion Cups
    scenario = inbox_replay.load(DATA, EXTRA)

    step(session, clock, scenario, closed(8))
    assert statuses(session)["s17"] == "other"  # «пришлём прайс до конца недели»
    assert offers(session, "s17") == []

    spy = Spy()
    step(session, clock, scenario, closed(50), spy)

    first, supplement = replies_of(session, "@orion_sales")
    assert (first.type, supplement.type) == ("other", "offer")
    [offer] = offers(session, "s17")
    assert (offer.reply_id, offer.product, offer.price, offer.moq) == (
        supplement.id,
        "Стаканы бумажные 250 мл",
        Decimal("0.05"),
        20000,
    )
    assert offer.quote.startswith("Получили запрос, пришлём прайс до конца недели\n")
    assert statuses(session)["s17"] == "offer"


def test_supplement_replaces_untouched_offer(session, clock):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)
    step(session, clock, scenario, closed("2.03"))

    write(session, "c1", "@nordpack_sales", after(5), "Обновление: коробки теперь 0.39 EUR/шт")
    step(session, clock, scenario, closed(5))

    [offer] = offers(session, "s1")
    assert (offer.price, offer.moq, offer.supplement_at) == (Decimal("0.39"), 5000, None)
    assert statuses(session)["s1"] == "offer"


@pytest.mark.parametrize("touched", ["edited_at", "applied_at"])
def test_supplement_keeps_edited_or_applied_offer(session, clock, touched):
    load(session, DATA)
    launch(session, clock, "stale_price")
    scenario = inbox_replay.load(DATA)
    step(session, clock, scenario, closed("2.03"))
    [first] = offers(session, "s1")
    setattr(first, touched, clock.now())
    session.flush()

    write(session, "c1", "@nordpack_sales", after(5), "Обновление: коробки теперь 0.39 EUR/шт")
    step(session, clock, scenario, closed(5))

    kept, new = offers(session, "s1")
    assert (kept.id, kept.price, kept.supplement_at) == (first.id, Decimal("0.42"), after(5))
    assert (new.price, new.supplement_at) == (Decimal("0.39"), None)


def test_every_reply_in_data_is_recorded(session, clock):
    """Без ключа воркер разбирает по записям: на данных задания и расширения запись есть у каждого ответа."""
    load(session, DATA, EXTRA)
    launch(session, clock, "stale_price")
    launch(session, clock, "inactive_new")
    scenario = inbox_replay.load(DATA, EXTRA)

    result = step(session, clock, scenario, closed("335.9"))

    assert result.failed == 0
    assert statuses(session) == {
        # «Активные, прайс устарел»
        "s1": "offer",
        "s8": "offer",
        "s10": "offer",
        "s13": "offer",
        "s15": "offer",
        "s20": "offer",
        "s21": "silent",  # Fjord ответил на границе окна — не засчитан
        "s2": "excluded",
        "s5": "excluded",
        "s7": "excluded",
        "s14": "excluded",
        "s18": "excluded",
        # «Новые без движения»
        "s3": "offer",
        "s4": "refusal",
        "s12": "other",  # «цена по запросу»
        "s17": "offer",
        "s6": "excluded",
    }
