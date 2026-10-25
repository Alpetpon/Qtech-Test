"""Отправка очереди с общего аккаунта, по одному сообщению, с паузой 1–3 минуты и не больше 100 за 24 часа.

На RATE_LIMIT аккаунт встаёт на паузу, а с ним и все рассылки: отправитель один.
"""

import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app import models
from app.clock import Clock
from app.config import settings
from app.messenger.sender import RateLimited, Sender

log = logging.getLogger(__name__)

ACCOUNT = "@company_shared"
LIMIT = 100
LIMIT_WINDOW = timedelta(hours=24)
PAUSE_SECONDS = (60, 180)
LOCK = func.hashtext(f"sender {ACCOUNT}")


def send_next(session: Session, clock: Clock, sender: Sender, rng: random.Random) -> models.OutboxMessage | None:
    """Шаг очереди, отправляет следующее сообщение, если уже можно. None значит рано, пусто, отправляет другой или RATE_LIMIT.

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

    try:
        sender.send(chat_id, text)
    except RateLimited:
        # RATE_LIMIT — точный ответ «не отправлено», поэтому сообщение можно вернуть в очередь:
        # со своим id оно уйдёт первым после паузы
        paused_at = clock.now()
        paused_until = paused_at + settings.rate_limit_pause
        message.status = "pending"
        state = session.get(models.SenderState, ACCOUNT) or models.SenderState(account=ACCOUNT)
        state.paused_at, state.paused_until, state.pause_reason = paused_at, paused_until, "RATE_LIMIT"
        session.add(state)
        session.commit()
        log.warning("RATE_LIMIT на чате %s: все рассылки на паузе до %s", chat_id, paused_until)
        return None

    sent_at = clock.now()
    session.get(models.Chat, chat_id).last_outbound_at = sent_at
    next_send_at = settle(session, message, "sent", sent_at, rng)
    session.commit()
    log.info("Рассылка №%s: отправлено в чат %s, следующее — не раньше %s", campaign_id, chat_id, next_send_at)
    return message


def recover(session: Session, clock: Clock, rng: random.Random) -> None:
    """При старте воркера: строки sending оставил процесс, упавший посреди отправки.

    Ушло ли такое сообщение, неизвестно. Ставим unknown и больше не отправляем: лучше одно потерянное
    сообщение, которое человек проверит, чем дубль в чате поставщика.
    """
    # Ждём, пока другой воркер закончит шаг: под блокировкой аккаунта sending может быть только брошенной
    session.execute(select(func.pg_advisory_xact_lock(LOCK)))
    now = clock.now()
    messages = session.scalars(
        select(models.OutboxMessage).where(models.OutboxMessage.status == "sending").order_by(models.OutboxMessage.id)
    ).all()
    for message in messages:
        settle(session, message, "unknown", now, rng)
        log.warning(
            "Рассылка №%s: неизвестно, ушло ли сообщение в чат %s, — повторно не отправляем, проверьте чат",
            message.campaign_id,
            message.chat_id,
        )
    session.commit()


def settle(session: Session, message: models.OutboxMessage, status: str, at: datetime, rng: random.Random) -> datetime:
    """Итог отправки: sent — ушло, unknown — могло уйти. Для снимка, паузы, лимита и 14 дней они одинаковы.

    last_outbound_at чата здесь не трогаем: у unknown неизвестно, писали ли. Возвращает конец паузы.
    """
    campaign_id, chat_id = message.campaign_id, message.chat_id
    message.status, message.sent_at = status, at
    session.execute(
        update(models.CampaignSupplier)
        .where(models.CampaignSupplier.campaign_id == campaign_id, models.CampaignSupplier.chat_id == chat_id)
        .values(status=status)
    )
    state = session.get(models.SenderState, ACCOUNT) or models.SenderState(account=ACCOUNT)
    state.next_send_at = at + timedelta(seconds=rng.randint(*PAUSE_SECONDS))
    session.add(state)
    unsent = exists().where(
        models.OutboxMessage.campaign_id == campaign_id, models.OutboxMessage.status.in_(("pending", "sending"))
    )
    if not session.scalar(select(unsent)):
        session.get(models.Campaign, campaign_id).status = "sent"
    return state.next_send_at


def ready_at(session: Session, now: datetime) -> datetime:
    """Когда аккаунту можно отправить следующее сообщение: не раньше now, конца пауз и места в лимите."""
    moments = [now]
    state = session.get(models.SenderState, ACCOUNT)
    if state is not None and state.next_send_at is not None:
        moments.append(state.next_send_at)
    if state is not None and state.paused_until is not None:
        moments.append(state.paused_until)
    # Окно скользящее, место освобождается, когда сотое с конца сообщение старше 24 часов
    hundredth = session.scalar(
        select(models.OutboxMessage.sent_at)
        .where(models.OutboxMessage.status.in_(("sent", "unknown")))
        .order_by(models.OutboxMessage.sent_at.desc())
        .offset(LIMIT - 1)
        .limit(1)
    )
    if hundredth is not None:
        moments.append(hundredth + LIMIT_WINDOW)
    return max(moments)


def current_pause(session: Session, now: datetime) -> models.SenderState | None:
    """Идущая пауза аккаунта: причина в pause_reason, сроки в paused_at и paused_until. None — паузы нет."""
    state = session.get(models.SenderState, ACCOUNT)
    if state is None or state.paused_until is None or state.paused_until <= now:
        return None
    return state


def resume(session: Session) -> None:
    """Ручное «Продолжить»: пауза снимается сразу, не дожидаясь срока. Транзакция — вызывающего."""
    state = session.get(models.SenderState, ACCOUNT)
    if state is not None:
        state.paused_at = state.paused_until = state.pause_reason = None


def campaign_status(session: Session, now: datetime, campaign: models.Campaign) -> str:
    """Статус рассылки для показа: неотправленная стоит, пока аккаунт на паузе."""
    if campaign.status == "sending" and current_pause(session, now) is not None:
        return "paused"
    return campaign.status
