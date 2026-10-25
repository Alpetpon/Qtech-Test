"""Рассылка из базы: превью перед запуском и постановка сообщений в очередь."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.clock import Clock
from app.domain.entities import Chat, Supplier
from app.domain.preview import RESEND_AFTER, Excluded, Preview, build_preview
from app.domain.selection import Selected, by_status, inactive_new, stale_price
from app.domain.template import check, render

PRESETS = {"stale_price": stale_price, "inactive_new": inactive_new}


@dataclass(frozen=True)
class Draft:
    """Что уйдет, если запустить сейчас. problems — почему запускать нельзя; пусто — можно."""

    preview: Preview
    problems: tuple[str, ...]


class Blocked(Exception):
    """Запуск заблокирован, рассылка не создана."""

    def __init__(self, draft: Draft) -> None:
        super().__init__("; ".join(draft.problems))
        self.draft = draft


def preview(
    session: Session, clock: Clock, template: str, *, preset: str | None = None, status: str | None = None
) -> Draft:
    return make_draft(session, clock.now(), template, preset, status)


def create(
    session: Session, clock: Clock, template: str, *, preset: str | None = None, status: str | None = None
) -> models.Campaign:
    """Рассылка, снимок поставщиков и очередь в транзакции вызывающего: появятся вместе или никак.

    Превью и шаблон считаются здесь заново: из запроса берем только шаблон и способ отбора.
    """
    # Запуски по одному, вторая транзакция не видит незакоммиченную очередь первой,
    # и двойной клик кнопки «Запустить» поставил бы чат в очередь дважды
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext("campaigns"))))
    now = clock.now()
    draft = make_draft(session, now, template, preset, status)
    if draft.problems:
        raise Blocked(draft)

    campaign = models.Campaign(
        preset=preset, supplier_status=status, template=template, status="sending", created_at=now
    )
    for item in draft.preview.outgoing:
        campaign.messages.append(models.OutboxMessage(chat_id=item.chat.id, text=render(template, item.suppliers)))
        campaign.suppliers += [
            models.CampaignSupplier(supplier_id=supplier.id, chat_id=item.chat.id, reasons=[], status="queued")
            for supplier in item.suppliers
        ]
    campaign.suppliers += [
        models.CampaignSupplier(supplier_id=item.supplier.id, reasons=list(item.reasons), status="excluded")
        for item in draft.preview.excluded
    ]
    session.add(campaign)
    session.flush()
    return campaign


def make_draft(session: Session, now: datetime, template: str, preset: str | None, status: str | None) -> Draft:
    suppliers, chats = load(session)
    selected = [item.supplier for item in choose(suppliers, now, preset, status)]
    result = exclude_busy(build_preview(selected, chats, now), busy_chats(session, now))
    # Шаблон проверяем только у тех, кому уходит сообщение
    problems = [problem for item in result.outgoing for problem in check(template, item.suppliers)]
    if not result.outgoing:
        problems.append("Отправлять некому: в рассылке ни одного чата")
    return Draft(result, tuple(dict.fromkeys(problems)))


def choose(suppliers: list[Supplier], now: datetime, preset: str | None, status: str | None) -> list[Selected]:
    if (preset is None) == (status is None):
        raise ValueError("Поставщиков выбирают пресетом или статусом — нужно ровно одно из двух")
    if status is not None:
        return by_status(suppliers, status)
    if preset not in PRESETS:
        raise ValueError(f"Неизвестный пресет {preset!r}")
    return PRESETS[preset](suppliers, now)


def load(session: Session) -> tuple[list[Supplier], list[Chat]]:
    """Справочники как доменные сущности, по алфавиту: в этом порядке идут превью и очередь."""
    suppliers = [
        Supplier(
            id=row.id,
            name=row.name,
            status=row.status,
            manager=row.manager,
            price_updated_at=row.price_updated_at,
            last_activity_at=row.last_activity_at,
        )
        for row in session.scalars(select(models.Supplier).order_by(models.Supplier.name, models.Supplier.id))
    ]
    rows = session.scalars(
        select(models.Chat).options(selectinload(models.Chat.suppliers)).order_by(models.Chat.title, models.Chat.id)
    )
    chats = [
        Chat(
            id=row.id,
            title=row.title,
            supplier_ids=tuple(supplier.id for supplier in row.suppliers),
            enabled=row.enabled,
            member=row.member,
            last_outbound_at=row.last_outbound_at,
        )
        for row in rows
    ]
    return suppliers, chats


def busy_chats(session: Session, now: datetime) -> dict[str, str]:
    """Чаты, где сообщение другой рассылки еще не ушло или ушло (unknown значит могло уйти) меньше 14 дней назад.

    Ушедшее сообщение видно и по last_outbound_at чата, но очередь держит чат и без него.
    """
    messages = session.scalars(
        select(models.OutboxMessage).where(
            or_(
                models.OutboxMessage.status.in_(("pending", "sending")),
                models.OutboxMessage.sent_at > now - RESEND_AFTER,
            )
        )
    )
    return {message.chat_id: busy_reason(message) for message in messages}


def busy_reason(message: models.OutboxMessage) -> str:
    number = message.campaign_id
    if message.status == "sent":
        return f"уже писали в рассылке №{number}"
    if message.status == "unknown":
        return f"неизвестно, ушло ли сообщение рассылки №{number}, — проверьте чат"
    return f"уже в очереди рассылки №{number}"


def exclude_busy(preview: Preview, busy: dict[str, str]) -> Preview:
    """Занятый чат исключается со всеми своими поставщиками."""
    outgoing = tuple(item for item in preview.outgoing if item.chat.id not in busy)
    excluded = tuple(
        Excluded(supplier, (busy[item.chat.id],))
        for item in preview.outgoing
        if item.chat.id in busy
        for supplier in item.suppliers
    )
    return Preview(outgoing, preview.excluded + excluded)
