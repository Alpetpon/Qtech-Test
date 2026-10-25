"""Предложения в отчёте: человек выбирает поставщика, правит поля и применяет предложение к прайсу.

Применённое предложение заморожено: ни править, ни применить второй раз.
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import models
from app.clock import Clock
from app.domain.offers import Fields, problems
from app.services.inbox import asked


class Refused(Exception):
    """С предложением ничего не сделано; reasons — почему, для человека."""

    def __init__(self, *reasons: str) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class NotFound(Refused):
    def __init__(self, offer_id: int) -> None:
        super().__init__(f"предложения №{offer_id} нет")


class AlreadyApplied(Refused):
    def __init__(self) -> None:
        super().__init__("предложение уже применено")


def choose_supplier(session: Session, clock: Clock, offer_id: int, supplier_id: str) -> models.Offer:
    """Поставщик предложения, если ника нет в справочнике, — в транзакции вызывающего.

    Выбирать можно из тех же, из кого подсказывает модель: поставщиков чата, которых спросили в рассылке.
    """
    offer = editable(session, offer_id)
    reply = session.get(models.Reply, offer.reply_id)
    if reply.supplier_id is not None:
        raise Refused("поставщик известен по нику, выбирать не нужно")
    if supplier_id not in asked(session, session.get(models.OutboxMessage, reply.outbox_id)):
        raise Refused("выбрать можно только поставщика этого чата, которого спросили в рассылке")
    offer.supplier_id = supplier_id
    # Выбор — тоже правка: дополнение не заменит предложение молча, а покажет «пришло дополнение»
    offer.edited_at = clock.now()
    offer.review = problems(offer.supplier_id, fields(offer))
    return offer


def edit(session: Session, clock: Clock, offer_id: int, changed: Fields) -> models.Offer:
    """Поля, как их поправил человек, — в транзакции вызывающего. Проверка — при «Применить»."""
    offer = editable(session, offer_id)
    offer.product = changed.product
    offer.price = changed.price
    offer.currency = changed.currency
    offer.moq = changed.moq
    offer.lead_time_days = changed.lead_time_days
    offer.regions = list(changed.regions)
    offer.edited_at = clock.now()
    offer.review = problems(offer.supplier_id, changed)
    return offer


def apply(session: Session, clock: Clock, offer_id: int) -> models.Price:
    """Запись в прайсе поставщика и дата обновления прайса — в транзакции вызывающего.

    Второй раз применить не даст база: отметка «применено» ставится, только если её ещё нет.
    Второе «Применить» (двойной клик, две вкладки, повтор запроса) ждёт первое и получает AlreadyApplied.
    """
    now = clock.now()
    # Применить нельзя — откатывается и отметка «применено»
    with session.begin_nested():
        offer = session.scalar(
            update(models.Offer)
            .where(models.Offer.id == offer_id, models.Offer.applied_at.is_(None))
            .values(applied_at=now)
            .returning(models.Offer)
            # Поля — как в базе на момент отметки: строка теперь наша, поменять их уже никто не успеет
            .execution_options(populate_existing=True)
        )
        if offer is None:
            if session.get(models.Offer, offer_id) is None:
                raise NotFound(offer_id)
            raise AlreadyApplied
        if reasons := problems(offer.supplier_id, fields(offer)):
            raise Refused(*reasons)
        price = models.Price(
            supplier_id=offer.supplier_id,
            offer_id=offer.id,
            product=offer.product,
            price=offer.price,
            currency=offer.currency,
            moq=offer.moq,
            lead_time_days=offer.lead_time_days,
            regions=offer.regions,
            applied_at=now,
        )
        session.add(price)
        session.get(models.Supplier, offer.supplier_id).price_updated_at = now
    return price


def editable(session: Session, offer_id: int) -> models.Offer:
    """Предложение под блокировкой до конца транзакции: «Применить» дождётся правки, правка — «Применить»."""
    # populate_existing: отметку «применено» читаем из базы, даже если предложение уже загружено в сессию
    offer = session.get(models.Offer, offer_id, with_for_update=True, populate_existing=True)
    if offer is None:
        raise NotFound(offer_id)
    if offer.applied_at is not None:
        raise AlreadyApplied
    return offer


def fields(offer: models.Offer) -> Fields:
    return Fields(offer.product, offer.price, offer.currency, offer.moq, offer.lead_time_days, tuple(offer.regions))
