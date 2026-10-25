from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.entities import Chat, Supplier

RESEND_AFTER = timedelta(days=14)


@dataclass(frozen=True)
class Outgoing:
    """Одно сообщение в чат на всех выбранных поставщиков этого чата."""

    chat: Chat
    suppliers: tuple[Supplier, ...]


@dataclass(frozen=True)
class Excluded:
    supplier: Supplier
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Totals:
    selected: int
    chats: int
    excluded: int


@dataclass(frozen=True)
class Preview:
    outgoing: tuple[Outgoing, ...]
    excluded: tuple[Excluded, ...]

    @property
    def totals(self) -> Totals:
        sent = sum(len(item.suppliers) for item in self.outgoing)
        return Totals(selected=sent + len(self.excluded), chats=len(self.outgoing), excluded=len(self.excluded))


def build_preview(
    selected: list[Supplier],
    chats: list[Chat],
    now: datetime,
    resend_after: timedelta = RESEND_AFTER,
) -> Preview:
    """Куда уйдет рассылка выбранным поставщикам и кто исключен со всеми причинами сразу."""
    chats_of: defaultdict[str, list[Chat]] = defaultdict(list)
    for chat in chats:
        for supplier_id in chat.supplier_ids:
            chats_of[supplier_id].append(chat)

    recipients: defaultdict[str, list[Supplier]] = defaultdict(list)
    excluded = []
    for supplier in selected:
        found = chats_of.get(supplier.id, [])
        if not found:
            reasons = ["нет чата"]
        elif len(found) > 1:
            titles = ", ".join(chat.title for chat in found)
            reasons = [f"несколько чатов ({titles}), нужно выбрать вручную"]
        else:
            reasons = chat_problems(found[0], now, resend_after)
        if reasons:
            excluded.append(Excluded(supplier, tuple(reasons)))
        else:
            recipients[found[0].id].append(supplier)

    outgoing = tuple(Outgoing(chat, tuple(recipients[chat.id])) for chat in chats if chat.id in recipients)
    return Preview(outgoing, tuple(excluded))


def chat_problems(chat: Chat, now: datetime, resend_after: timedelta) -> list[str]:
    """Почему в чат сейчас нельзя писать. Писали ровно resend_after назад уже можно."""
    problems = []
    if not chat.member:
        problems.append("аккаунт не состоит в чате")
    if not chat.enabled:
        problems.append("чат выключен")
    sent = chat.last_outbound_at  # None — в чат ещё не писали
    if sent is not None and sent > now:
        problems.append(f"дата последней отправки в будущем ({sent:%d.%m}) — проверьте данные")
    elif sent is not None and now - sent < resend_after:
        problems.append(f"писали {ago(now - sent)}")
    return problems


def ago(delta: timedelta) -> str:
    days = delta.days
    if days == 0:
        return "меньше суток назад"
    if days % 10 == 1 and days % 100 != 11:
        word = "день"
    elif 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14:
        word = "дня"
    else:
        word = "дней"
    return f"{days} {word} назад"
