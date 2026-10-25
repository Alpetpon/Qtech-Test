"""Прием входящих: какие сообщения — ответы, на какую отправку и от какого поставщика."""

from collections.abc import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.domain.replies import COPY, Message, bind, continues, handle, identify


def process(session: Session) -> int:
    """Разбирает новые входящие в транзакции вызывающего; возвращает, сколько разобрано.

    Сообщения идут по времени прихода: следующее сообщение того же ника продолжает его ответ.
    """
    # Разбор по одному: два параллельных разбора склеили бы одни сообщения в разные ответы
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext("inbox"))))
    rows = session.scalars(
        select(models.InboxMessage)
        .where(models.InboxMessage.reply_id.is_(None), models.InboxMessage.mark.is_(None))
        .order_by(models.InboxMessage.received_at, models.InboxMessage.id)
    ).all()
    if not rows:
        return 0
    our_accounts = session.scalars(select(models.OurAccount.handle)).all()
    contacts = dict(session.execute(select(models.SupplierContact.handle, models.SupplierContact.supplier_id)).all())
    for row in rows:
        take(session, row, our_accounts, contacts)
        session.flush()
    return len(rows)


def take(session: Session, row: models.InboxMessage, our_accounts: Sequence[str], contacts: Mapping[str, str]) -> None:
    message = to_message(row)
    if is_copy(session, row, message):
        row.mark = COPY
        return
    sends = {
        outbox.sent_at: outbox
        for outbox in session.scalars(
            select(models.OutboxMessage).where(
                models.OutboxMessage.chat_id == row.chat_id, models.OutboxMessage.status == "sent"
            )
        )
    }
    binding = bind(message, sends.keys(), our_accounts)
    if binding.sent_at is None:
        row.mark = binding.mark
        return
    outbox = sends[binding.sent_at]
    reply = open_reply(session, outbox, row)
    if reply is None:
        author = identify(row.sender, chat_suppliers(session, row.chat_id), contacts, asked(session, outbox))
        reply = models.Reply(
            outbox_id=outbox.id,
            sender=row.sender,
            supplier_id=author.supplier_id,
            mark=author.mark,
            last_at=row.received_at,
        )
        session.add(reply)
    reply.messages.append(row)
    reply.last_at = max(reply.last_at, row.received_at)


def to_message(row: models.InboxMessage) -> Message:
    return Message(row.chat_id, row.sender, row.received_at, row.text, row.attachment)


def is_copy(session: Session, row: models.InboxMessage, message: Message) -> bool:
    """Точная копия сообщения, которое уже пришло раньше: повторная доставка, не второй ответ."""
    earlier = session.scalars(
        select(models.InboxMessage).where(
            models.InboxMessage.chat_id == row.chat_id,
            models.InboxMessage.received_at == row.received_at,
            models.InboxMessage.id < row.id,
        )
    )
    return message in [to_message(other) for other in earlier]


def open_reply(session: Session, outbox: models.OutboxMessage, row: models.InboxMessage) -> models.Reply | None:
    """Последний ответ этого ника на отправку, если пауза после него меньше 10 минут."""
    replies = session.scalars(
        select(models.Reply).where(models.Reply.outbox_id == outbox.id).order_by(models.Reply.last_at.desc())
    )
    last = next((reply for reply in replies if handle(reply.sender) == handle(row.sender)), None)
    return last if last and continues(last.last_at, row.received_at) else None


def chat_suppliers(session: Session, chat_id: str) -> list[str]:
    return [supplier.id for supplier in session.get(models.Chat, chat_id).suppliers]


def asked(session: Session, outbox: models.OutboxMessage) -> set[str]:
    """Кому из поставщиков чата ушло это сообщение рассылки."""
    return set(
        session.scalars(
            select(models.CampaignSupplier.supplier_id).where(
                models.CampaignSupplier.campaign_id == outbox.campaign_id,
                models.CampaignSupplier.chat_id == outbox.chat_id,
            )
        )
    )
