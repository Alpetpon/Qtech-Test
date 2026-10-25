"""Разбор ответов моделью: тип ответа, предложения и статус поставщика.

В модель уходит закрытый ответ (10 минут тишины) вместе с прежними ответами того же поставщика
на эту отправку: позднее дополнение разбирается заново с тем, что поставщик написал раньше.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app import models
from app.clock import Clock
from app.domain.offers import Fields, problems
from app.domain.replies import QUIET, UNKNOWN_SUPPLIER, handle
from app.domain.statuses import OFFER, OTHER, REFUSAL
from app.llm import Analysis, Item, LLMClient, LLMError, Reply, ReplyType, analyze
from app.services.inbox import asked

log = logging.getLogger(__name__)

STATUSES = {ReplyType.OFFER: OFFER, ReplyType.REFUSAL: REFUSAL, ReplyType.OTHER: OTHER}


@dataclass(frozen=True)
class Pending:
    """Закрытый неразобранный ответ и то, что о нём уйдёт в модель."""

    reply_id: int
    last_at: datetime
    reply: Reply
    # Поставщики общего чата по названию: модель подсказывает название, сохраняем поставщика
    candidates: dict[str, str]


@dataclass(frozen=True)
class Result:
    analyzed: int
    failed: int


def run(session: Session, clock: Clock, client: LLMClient, model: str) -> Result:
    """Разбирает закрытые ответы; транзакциями управляет сам. Модель недоступна — ответ ждёт следующего вызова."""
    pending = closed(session, clock.now())
    # Модель отвечает секундами: транзакция не должна висеть, пока она думает
    session.commit()
    analyzed = failed = 0
    for item in pending:
        try:
            analysis = analyze(item.reply, client, model)
        except LLMError as error:
            log.warning("Ответ №%s не разобран, повторим позже: %s", item.reply_id, error)
            failed += 1
            continue
        analyzed += save(session, item, analysis, clock.now())
        session.commit()
    return Result(analyzed, failed)


def closed(session: Session, now: datetime) -> list[Pending]:
    """Закрытые неразобранные ответы по времени: дополнение — после ответа, который оно дополняет."""
    replies = session.scalars(
        select(models.Reply)
        .where(
            models.Reply.analyzed_at.is_(None),
            models.Reply.last_at <= now - QUIET,
            # «Не по рассылке» в модель не идёт: эту компанию не спрашивали
            or_(models.Reply.mark.is_(None), models.Reply.mark == UNKNOWN_SUPPLIER),
        )
        .order_by(models.Reply.last_at, models.Reply.id)
    ).all()
    return [prepare(session, reply) for reply in replies]


def prepare(session: Session, reply: models.Reply) -> Pending:
    earlier = [other for other in thread(session, reply) if other.last_at <= reply.last_at]
    messages = sorted(
        (message for other in earlier for message in other.messages), key=lambda m: (m.received_at, m.id)
    )
    candidates = {}
    if reply.supplier_id is None:
        outbox = session.get(models.OutboxMessage, reply.outbox_id)
        suppliers = session.scalars(
            select(models.Supplier).where(models.Supplier.id.in_(asked(session, outbox))).order_by(models.Supplier.id)
        )
        candidates = {supplier.name: supplier.id for supplier in suppliers}
    text = "\n".join(message.text for message in messages)
    attachments = tuple(message.attachment for message in messages if message.attachment)
    return Pending(reply.id, reply.last_at, Reply(text, attachments, tuple(candidates)), candidates)


def thread(session: Session, reply: models.Reply) -> list[models.Reply]:
    """Ответы того же автора на ту же отправку, по времени, включая этот."""
    replies = session.scalars(
        select(models.Reply)
        .where(models.Reply.outbox_id == reply.outbox_id)
        .order_by(models.Reply.last_at, models.Reply.id)
    )
    return [other for other in replies if author(other) == author(reply)]


def author(reply: models.Reply) -> tuple[str | None, str | None]:
    """Поставщик ответа, а если он непонятен — ник: два ответа незнакомого ника — один автор."""
    return reply.supplier_id, None if reply.supplier_id else handle(reply.sender)


def save(session: Session, item: Pending, analysis: Analysis, now: datetime) -> bool:
    """Разбор, предложения и статус поставщика — одной транзакцией вызывающего.

    False — ответ разобрали без нас или к нему добавилось сообщение, пока думала модель.
    """
    reply = session.get(models.Reply, item.reply_id, with_for_update=True)
    if reply.analyzed_at is not None or reply.last_at != item.last_at:
        return False
    reply.type = analysis.type.value
    reply.notes = analysis.notes
    reply.review = analysis.review
    reply.supplier_hint = item.candidates.get(analysis.supplier_hint)
    reply.analyzed_at = now

    replies = thread(session, reply)
    if any(other.last_at > reply.last_at and other.analyzed_at for other in replies):
        # Более поздний ответ уже разобран вместе с этим: предложения и статус — по нему
        return True
    supersede(session, [other.id for other in replies if other is not reply], reply.last_at)
    for position in analysis.items:
        session.add(offer(reply, position, item.reply.text))
    if reply.mark is None:
        outbox = session.get(models.OutboxMessage, reply.outbox_id)
        session.execute(
            update(models.CampaignSupplier)
            .where(
                models.CampaignSupplier.campaign_id == outbox.campaign_id,
                models.CampaignSupplier.supplier_id == reply.supplier_id,
            )
            .values(status=STATUSES[analysis.type])
        )
    return True


def supersede(session: Session, reply_ids: list[int], supplement_at: datetime) -> None:
    """Прежние предложения автора: нетронутые заменяет новый разбор, поправленные и применённые остаются."""
    for previous in session.scalars(select(models.Offer).where(models.Offer.reply_id.in_(reply_ids))):
        if previous.edited_at or previous.applied_at:
            previous.supplement_at = supplement_at
        else:
            session.delete(previous)


def offer(reply: models.Reply, item: Item, quote: str) -> models.Offer:
    return models.Offer(
        reply_id=reply.id,
        supplier_id=reply.supplier_id,
        product=item.product,
        price=item.price,
        currency=item.currency,
        moq=item.moq,
        lead_time_days=item.lead_time_days,
        regions=item.regions,
        quote=quote,
        review=problems(
            reply.supplier_id,
            Fields(item.product, item.price, item.currency, item.moq, item.lead_time_days, tuple(item.regions)),
        ),
    )
