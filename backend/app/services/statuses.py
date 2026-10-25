"""Статусы поставщиков в рассылках: статус «молчит» через 72 часа без ответа, ответ снимает статус «молчит»."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.clock import Clock
from app.domain.statuses import SENT, SILENT, supplier_status

# Ждем ответа только от тех, кому сообщение ушло. Ответ не пропадает, поэтому «ответил» не пересчитываем;
# остальные статусы не наши: queued — ещё не ушло, excluded — не отправляли, unknown — неизвестно, ушло ли
WAITING = (SENT, SILENT)


def refresh(session: Session, clock: Clock) -> int:
    """Пересчитывает статусы поставщиков, ждущих ответа, в транзакции вызывающего; возвращает, сколько изменилось."""
    now = clock.now()
    rows = session.execute(
        select(models.CampaignSupplier, models.OutboxMessage)
        .join(
            models.OutboxMessage,
            (models.OutboxMessage.campaign_id == models.CampaignSupplier.campaign_id)
            & (models.OutboxMessage.chat_id == models.CampaignSupplier.chat_id),
        )
        .where(models.CampaignSupplier.status.in_(WAITING))
    ).all()
    if not rows:
        return 0
    replies: defaultdict[tuple[int, str], list[datetime]] = defaultdict(list)
    counted = (
        select(models.Reply.outbox_id, models.Reply.supplier_id, models.InboxMessage.received_at)
        .join(models.Reply.messages)
        # Пометка — ответ не засчитан: непонятно, от какой компании, или ее не спрашивали
        .where(models.Reply.mark.is_(None), models.Reply.outbox_id.in_([outbox.id for _, outbox in rows]))
    )
    for outbox_id, supplier_id, received_at in session.execute(counted):
        replies[outbox_id, supplier_id].append(received_at)
    changed = 0
    for row, outbox in rows:
        status = supplier_status(outbox.sent_at, now, replies[outbox.id, row.supplier_id])
        if row.status != status:
            row.status = status
            changed += 1
    return changed
