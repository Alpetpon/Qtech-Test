"""Входящие сообщения: что считается ответом на рассылку и от какого поставщика.

Время наших отправок в чат и текущее время приходят параметрами.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

REPLY_WINDOW = timedelta(days=14)
QUIET = timedelta(minutes=10)

OURS = "наш сотрудник"
NO_SEND = "в чат не отправляли"
BEFORE_SEND = "раньше отправки"
OUT_OF_WINDOW = "вне окна"
COPY = "точная копия"
UNKNOWN_SUPPLIER = "непонятно, от какой компании"
NOT_ASKED = "не по рассылке"


@dataclass(frozen=True)
class Message:
    """Сообщение, пришедшее в чат."""

    chat_id: str
    sender: str
    received_at: datetime
    text: str
    attachment: str | None = None


@dataclass(frozen=True)
class Incoming:
    """Запись incoming_messages.json — сценарий заглушки мессенджера.

    t — часы от нашей отправки в этот чат: когда отправили, данные не знают, это решает очередь.
    """

    chat_id: str
    sender: str
    t: Decimal
    text: str
    attachment: str | None = None

    def arrive(self, sent_at: datetime) -> Message:
        # timedelta не принимает Decimal: округляем до секунды, 0.5 — вверх
        seconds = int((self.t * 3600).to_integral_value(ROUND_HALF_UP))
        return Message(self.chat_id, self.sender, sent_at + timedelta(seconds=seconds), self.text, self.attachment)


def from_row(row: Mapping[str, Any]) -> Incoming:
    """Поле from становится sender: from — ключевое слово Python."""
    return Incoming(
        chat_id=row["chat_id"],
        sender=row["from"],
        # json отдаёт float, а str возвращает запись из файла: Decimal("2.03"), но Decimal(2.03) = 2.0299…
        t=Decimal(str(row["t"])),
        text=row["text"],
        attachment=row.get("attachment"),
    )


@dataclass(frozen=True)
class Binding:
    """Наша отправка в чат, на которую пришёл ответ. Не ответ — sent_at пустой, в mark причина."""

    sent_at: datetime | None
    mark: str | None = None


def bind(message: Message, sends: Collection[datetime], our_accounts: Iterable[str]) -> Binding:
    """Привязка к рассылке по чату и времени; sends — наши отправки в чат сообщения.

    Ответ засчитывается, если пришёл меньше чем через 14 дней, а новая рассылка в чат возможна
    через 14 дней и позже. Окна ответов не пересекаются, и ответ относится к последней отправке
    перед ним. Пришёл в момент отправки — уже ответ.
    """
    if is_ours(message.sender, our_accounts):
        return Binding(None, OURS)
    if not sends:
        return Binding(None, NO_SEND)
    earlier = [sent_at for sent_at in sends if sent_at <= message.received_at]
    if not earlier:
        return Binding(None, BEFORE_SEND)
    sent_at = max(earlier)
    if message.received_at - sent_at >= REPLY_WINDOW:
        return Binding(None, OUT_OF_WINDOW)
    return Binding(sent_at)


def is_ours(sender: str, our_accounts: Iterable[str]) -> bool:
    return handle(sender) in {handle(account) for account in our_accounts}


def handle(value: str) -> str:
    """Ники без учёта регистра: @Anna_Buy и @anna_buy — один аккаунт."""
    return value.casefold()


def unique(messages: Iterable[Message]) -> list[Message]:
    """Без точных копий: повторная доставка того же сообщения — не второй ответ."""
    return list(dict.fromkeys(messages))


@dataclass(frozen=True)
class Reply:
    """Ответ: сообщения одного отправителя подряд с паузами меньше 10 минут."""

    messages: tuple[Message, ...]

    def closed(self, now: datetime) -> bool:
        """После 10 минут тишины ответ закрыт и уходит в разбор; следующее сообщение — уже новый ответ."""
        return now - self.messages[-1].received_at >= QUIET


def group(replies: Iterable[Message]) -> list[Reply]:
    """Склеивает ответы, прошедшие bind: наши сообщения и не ответы склейку не разрывают.

    В общем чате у каждого отправителя склейка своя. Порядок — по времени прихода, а не по порядку в списке.
    """
    groups: list[list[Message]] = []
    current: dict[tuple[str, str], list[Message]] = {}
    for message in sorted(replies, key=lambda m: m.received_at):
        key = (message.chat_id, handle(message.sender))
        last = current.get(key)
        if last and continues(last[-1].received_at, message.received_at):
            last.append(message)
        else:
            current[key] = [message]
            groups.append(current[key])
    return [Reply(tuple(messages)) for messages in groups]


def continues(last_at: datetime, received_at: datetime) -> bool:
    """Пауза меньше 10 минут после последнего сообщения — это ещё тот же ответ."""
    return received_at - last_at < QUIET


@dataclass(frozen=True)
class Author:
    """Кто ответил. Поставщик пустой — ответ остаётся на чате, поставщика выбирает человек."""

    supplier_id: str | None
    mark: str | None = None


def identify(
    sender: str, supplier_ids: Sequence[str], contacts: Mapping[str, str], asked: Collection[str]
) -> Author:
    """Поставщик ответа: supplier_ids — поставщики чата, contacts — ник → поставщик,
    asked — кого из поставщиков чата спросили в этой рассылке.

    Один поставщик в чате — он, справочник не нужен. В общем чате — только по нику: названия
    почти одинаковые (Kestrel Foods и Kestrel Foods EU), а ошибка — цена применится не той компании.
    """
    if len(supplier_ids) == 1:
        supplier_id = supplier_ids[0]
    else:
        supplier_id = {handle(h): s for h, s in contacts.items()}.get(handle(sender))
        # Ника нет в справочнике или он другой компании, не из этого чата
        if supplier_id not in supplier_ids:
            return Author(None, UNKNOWN_SUPPLIER)
    if supplier_id not in asked:
        # Ответ на статусы не влияет: эту компанию в рассылке не спрашивали
        return Author(supplier_id, NOT_ASKED)
    return Author(supplier_id)
