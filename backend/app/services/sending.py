"""Отправка очереди с общего аккаунта, по одному сообщению, с паузой 1–3 минуты и не больше 100 за 24 часа."""

import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app import models
from app.clock import Clock
from app.messenger.sender import Sender

log = logging.getLogger(__name__)

ACCOUNT = "@company_shared"
LIMIT = 100
LIMIT_WINDOW = timedelta(hours=24)
PAUSE_SECONDS = (60, 180)
LOCK = func.hashtext(f"sender {ACCOUNT}")


def send_next(session: Session, clock: Clock, sender: Sender, rng: random.Random) -> models.OutboxMessage | None:
    """Шаг очереди, отправляет следующее сообщение, если уже можно. None значит рано, пусто или отправляет другой.

    Транзакциями управляет сам шаг. Сессия весь шаг должна держать одно соединение, на нем
    живет блокировка аккаунта. Если шаг упал, соединение закрывается, так снимается и блокировка.
    """
    # Блокировка сессионная, а не на транзакцию: такая снялась бы сразу после сохранения статуса sending,
    # еще до вызова отправителя, и второй воркер отправил бы свое сообщение без паузы
    if not session.scalar(select(func.pg_try_advisory_lock(LOCK))):
        return None
    message = send_locked(session, clock, sender, rng)
    session.scalar(select(func.pg_advisory_unlock(LOCK)))
    return message


def send_locked(session: Session, clock: Clock, sender: Sender, rng: random.Random) -> models.OutboxMessage | None:
    now = clock.now()
    if ready_at(session, now) > now:
        return None
    message = session.scalars(
        select(models.OutboxMessage)
        .where(models.OutboxMessage.status == "pending")
        .order_by(models.OutboxMessage.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if message is None:
        return None
    message.status = "sending"
    campaign_id, chat_id, text = message.campaign_id, message.chat_id, message.text
    # Фиксируем до вызова отправителя, если процесс упадет посреди отправки, строка останется sending и в отправку она больше не попадает
    session.commit()

    sender.send(chat_id, text)

    sent_at = clock.now()
    message.status, message.sent_at = "sent", sent_at
    session.get(models.Chat, chat_id).last_outbound_at = sent_at
    session.execute(
        update(models.CampaignSupplier)
        .where(models.CampaignSupplier.campaign_id == campaign_id, models.CampaignSupplier.chat_id == chat_id)
        .values(status="sent")
    )
    next_send_at = sent_at + timedelta(seconds=rng.randint(*PAUSE_SECONDS))
    state = session.get(models.SenderState, ACCOUNT) or models.SenderState(account=ACCOUNT)
    state.next_send_at = next_send_at
    session.add(state)
    unsent = exists().where(models.OutboxMessage.campaign_id == campaign_id, models.OutboxMessage.status != "sent")
    if not session.scalar(select(unsent)):
        session.get(models.Campaign, campaign_id).status = "sent"
    session.commit()
    log.info("Рассылка №%s: отправлено в чат %s, следующее — не раньше %s", campaign_id, chat_id, next_send_at)
    return message


def ready_at(session: Session, now: datetime) -> datetime:
    """Когда аккаунту можно отправить следующее сообщение: не раньше now, конца паузы и места в лимите."""
    moments = [now]
    state = session.get(models.SenderState, ACCOUNT)
    if state is not None and state.next_send_at is not None:
        moments.append(state.next_send_at)
    # Окно скользящее, место освобождается, когда сотое с конца сообщение старше 24 часов
    hundredth = session.scalar(
        select(models.OutboxMessage.sent_at)
        .where(models.OutboxMessage.status == "sent")
        .order_by(models.OutboxMessage.sent_at.desc())
        .offset(LIMIT - 1)
        .limit(1)
    )
    if hundredth is not None:
        moments.append(hundredth + LIMIT_WINDOW)
    return max(moments)
