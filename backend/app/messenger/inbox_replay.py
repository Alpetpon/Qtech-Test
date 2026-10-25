"""Заглушка мессенджера: проигрывает incoming_messages.json от наших отправок.

t в сценарии — часы от отправки нашей очередью в этот чат, поэтому сообщение приходит
в момент outbox.sent_at + t, на каждую отправку в чат заново. В чат, куда не отправляли,
ничего не приходит: отсчитывать не от чего.
"""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.replies import Incoming, from_row
from app.models import InboxMessage, OutboxMessage
from app.seed import read


def load(*dirs: Path) -> list[Incoming]:
    return [from_row(row) for row in read(dirs, "incoming_messages.json")]


def deliver(session: Session, clock: Clock, scenario: Sequence[Incoming]) -> int:
    """Кладёт во входящие сообщения, которые к этому моменту уже пришли; возвращает, сколько новых.

    id сообщения в мессенджере — отправка и номер записи в сценарии: повторный вызов ничего
    не дублирует, а копия записи в сценарии приходит, как пришла бы повторная доставка.
    """
    now = clock.now()
    added = 0
    sent = select(OutboxMessage).where(OutboxMessage.status == "sent").order_by(OutboxMessage.id)
    for outbox in session.scalars(sent):
        for number, incoming in enumerate(scenario):
            if incoming.chat_id != outbox.chat_id:
                continue
            message = incoming.arrive(outbox.sent_at)
            if message.received_at > now:
                continue
            inserted = session.scalar(
                insert(InboxMessage)
                .values(
                    chat_id=message.chat_id,
                    external_id=f"replay:{outbox.id}:{number}",
                    sender=message.sender,
                    received_at=message.received_at,
                    text=message.text,
                    attachment=message.attachment,
                )
                .on_conflict_do_nothing()
                .returning(InboxMessage.id)
            )
            added += inserted is not None
    return added
