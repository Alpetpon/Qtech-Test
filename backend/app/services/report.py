"""Что видит человек: рассылки с очередью, отчёт по поставщикам и предложениям, непривязанные входящие.

Только чтение: статусы пересчитывает воркер, предложения — разбор моделью и правки человека.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.domain.replies import BEFORE_SEND, NO_SEND, OUT_OF_WINDOW
from app.services.sending import ACCOUNT, LIMIT, campaign_status, current_pause, sent_last_day

# Поставщик написал, но привязать сообщение не к чему. Наши сотрудники и повторные доставки сюда не попадают
UNBOUND = (OUT_OF_WINDOW, NO_SEND, BEFORE_SEND)


@dataclass(frozen=True)
class CampaignView:
    id: int
    preset: str | None
    supplier_status: str | None
    template: str
    created_at: datetime
    # sending, paused или sent: неотправленная рассылка стоит, пока аккаунт на паузе
    status: str
    # Сообщения рассылки по статусам очереди
    queue: dict[str, int]


@dataclass(frozen=True)
class Pause:
    reason: str
    since: datetime
    until: datetime


@dataclass(frozen=True)
class SenderView:
    account: str
    pause: Pause | None
    sent_last_day: int
    daily_limit: int


@dataclass(frozen=True)
class ReplyView:
    id: int
    chat: models.Chat
    sender: str
    messages: list[models.InboxMessage]
    type: str | None
    notes: list[str]
    review: list[str]
    mark: str | None
    supplier: models.Supplier | None
    supplier_hint: models.Supplier | None
    # Из кого человек выбирает поставщика предложений, если по нику он непонятен
    candidates: list[models.Supplier]
    offers: list[models.Offer]


@dataclass(frozen=True)
class SupplierView:
    id: str
    name: str
    status: str
    chat: models.Chat | None
    reasons: list[str]
    price_updated_at: datetime | None
    replies: list[ReplyView]


@dataclass(frozen=True)
class ReportView:
    campaign: CampaignView
    suppliers: list[SupplierView]
    # Ответы в чатах рассылки, не засчитанные ни одному её поставщику: непонятно от кого или «не по рассылке»
    unassigned: list[ReplyView]


@dataclass(frozen=True)
class UnboundView:
    id: int
    chat: models.Chat
    sender: str
    received_at: datetime
    text: str
    attachment: str | None
    mark: str
    # Рассылка, в чат которой пришёл не засчитанный ответ; у сообщений вне окна и без отправки — пусто
    campaign_id: int | None


def campaigns(session: Session, now: datetime) -> list[CampaignView]:
    """Все рассылки, новые первыми."""
    rows = session.scalars(select(models.Campaign).order_by(models.Campaign.id.desc())).all()
    counts = queues(session, [row.id for row in rows])
    return [view(session, now, row, counts[row.id]) for row in rows]


def campaign(session: Session, now: datetime, campaign_id: int) -> CampaignView | None:
    row = session.get(models.Campaign, campaign_id)
    if row is None:
        return None
    return view(session, now, row, queues(session, [row.id])[row.id])


def view(session: Session, now: datetime, row: models.Campaign, queue: dict[str, int]) -> CampaignView:
    return CampaignView(
        id=row.id,
        preset=row.preset,
        supplier_status=row.supplier_status,
        template=row.template,
        created_at=row.created_at,
        status=campaign_status(session, now, row),
        queue=queue,
    )


def queues(session: Session, campaign_ids: list[int]) -> defaultdict[int, dict[str, int]]:
    counts: defaultdict[int, dict[str, int]] = defaultdict(dict)
    rows = session.execute(
        select(models.OutboxMessage.campaign_id, models.OutboxMessage.status, func.count())
        .where(models.OutboxMessage.campaign_id.in_(campaign_ids))
        .group_by(models.OutboxMessage.campaign_id, models.OutboxMessage.status)
    )
    for campaign_id, status, count in rows:
        counts[campaign_id][status] = count
    return counts


def sender(session: Session, now: datetime) -> SenderView:
    """Общий аккаунт: пауза и суточный лимит у него одни на все рассылки."""
    state = current_pause(session, now)
    pause = None if state is None else Pause(state.pause_reason, state.paused_at, state.paused_until)
    return SenderView(ACCOUNT, pause, sent_last_day(session, now), LIMIT)


def build(session: Session, now: datetime, campaign_id: int) -> ReportView | None:
    """Отчёт рассылки: поставщики по алфавиту, у каждого его засчитанные ответы с предложениями."""
    summary = campaign(session, now, campaign_id)
    if summary is None:
        return None
    rows = session.execute(
        select(models.CampaignSupplier, models.Supplier)
        .join(models.Supplier)
        .where(models.CampaignSupplier.campaign_id == campaign_id)
        .order_by(models.Supplier.name, models.Supplier.id)
    ).all()
    asked: defaultdict[str, list[models.Supplier]] = defaultdict(list)
    for row, supplier in rows:
        if row.chat_id is not None:
            asked[row.chat_id].append(supplier)

    replies = session.execute(
        select(models.Reply, models.OutboxMessage.chat_id)
        .join(models.OutboxMessage, models.Reply.outbox_id == models.OutboxMessage.id)
        .where(models.OutboxMessage.campaign_id == campaign_id)
        .options(selectinload(models.Reply.messages))
        .order_by(models.Reply.last_at, models.Reply.id)
    ).all()
    offers: defaultdict[int, list[models.Offer]] = defaultdict(list)
    for offer in session.scalars(
        select(models.Offer)
        .where(models.Offer.reply_id.in_([reply.id for reply, _ in replies]))
        .order_by(models.Offer.id)
    ):
        offers[offer.reply_id].append(offer)

    counted: defaultdict[str, list[ReplyView]] = defaultdict(list)
    unassigned = []
    for reply, chat_id in replies:
        item = ReplyView(
            id=reply.id,
            chat=session.get(models.Chat, chat_id),
            sender=reply.sender,
            messages=reply.messages,
            type=reply.type,
            notes=reply.notes,
            review=reply.review,
            mark=reply.mark,
            supplier=supplier_or_none(session, reply.supplier_id),
            supplier_hint=supplier_or_none(session, reply.supplier_hint),
            candidates=asked[chat_id] if reply.supplier_id is None else [],
            offers=offers[reply.id],
        )
        # Пометка — ответ не засчитан поставщику: identify ставит её, если компания непонятна или её не спрашивали
        if reply.mark is None:
            counted[reply.supplier_id].append(item)
        else:
            unassigned.append(item)

    suppliers = [
        SupplierView(
            id=supplier.id,
            name=supplier.name,
            status=row.status,
            chat=None if row.chat_id is None else session.get(models.Chat, row.chat_id),
            reasons=row.reasons,
            price_updated_at=supplier.price_updated_at,
            replies=counted[supplier.id],
        )
        for row, supplier in rows
    ]
    return ReportView(summary, suppliers, unassigned)


def unbound(session: Session) -> list[UnboundView]:
    """Входящие поставщиков, не засчитанные ни одной рассылке: вне окна, без отправки, непонятно от кого,
    «не по рассылке». По времени прихода."""
    items = [
        unbound_view(session, message, message.mark, None)
        for message in session.scalars(select(models.InboxMessage).where(models.InboxMessage.mark.in_(UNBOUND)))
    ]
    in_replies = session.execute(
        select(models.InboxMessage, models.Reply.mark, models.OutboxMessage.campaign_id)
        .join(models.Reply, models.InboxMessage.reply_id == models.Reply.id)
        .join(models.OutboxMessage, models.Reply.outbox_id == models.OutboxMessage.id)
        .where(models.Reply.mark.is_not(None))
    )
    items += [unbound_view(session, message, mark, campaign_id) for message, mark, campaign_id in in_replies]
    return sorted(items, key=lambda item: (item.received_at, item.id))


def unbound_view(session: Session, message: models.InboxMessage, mark: str, campaign_id: int | None) -> UnboundView:
    return UnboundView(
        id=message.id,
        chat=session.get(models.Chat, message.chat_id),
        sender=message.sender,
        received_at=message.received_at,
        text=message.text,
        attachment=message.attachment,
        mark=mark,
        campaign_id=campaign_id,
    )


def supplier_or_none(session: Session, supplier_id: str | None) -> models.Supplier | None:
    return None if supplier_id is None else session.get(models.Supplier, supplier_id)
