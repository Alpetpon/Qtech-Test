"""Статус поставщика в рассылке: отправлено → молчит → ответил → предложение, отказ или другое.

Время отправки, текущее время и ответы приходят параметрами.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

SILENCE = timedelta(hours=72)

SENT = "sent"
SILENT = "silent"
REPLIED = "replied"
# Ответ разобран моделью: тип последнего разбора
OFFER = "offer"
REFUSAL = "refusal"
OTHER = "other"


def supplier_status(sent_at: datetime, now: datetime, replies: Iterable[datetime], silence: timedelta = SILENCE) -> str:
    """sent_at — наша отправка в чат поставщика, replies — когда пришли его засчитанные ответы на нее.

    Засчитанный ответ — в окне 14 дней, от этого поставщика и не «не по рассылке»: это уже решили
    replies.bind и replies.identify. Поэтому поздний ответ, после 72 часов, тоже снимает статус «молчит».
    """
    if any(received_at <= now for received_at in replies):
        return REPLIED
    if now - sent_at >= silence:
        return SILENT
    return SENT
