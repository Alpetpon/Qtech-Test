from datetime import datetime

from sqlalchemy import ARRAY, CheckConstraint, Column, DateTime, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    status: Mapped[str]
    manager: Mapped[str | None]
    price_updated_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]


chat_suppliers = Table(
    "chat_suppliers",
    Base.metadata,
    Column("chat_id", ForeignKey("chats.id"), primary_key=True),
    Column("supplier_id", ForeignKey("suppliers.id"), primary_key=True),
)


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    enabled: Mapped[bool]
    member: Mapped[bool]
    last_outbound_at: Mapped[datetime | None]

    suppliers: Mapped[list[Supplier]] = relationship(secondary=chat_suppliers)


class OurAccount(Base):
    __tablename__ = "our_accounts"

    handle: Mapped[str] = mapped_column(primary_key=True)


class SupplierContact(Base):
    __tablename__ = "supplier_contacts"

    handle: Mapped[str] = mapped_column(primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))


class Campaign(Base):
    """Рассылка. Поставщиков выбирают пресетом или фильтром по статусу — заполнено ровно одно."""

    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("(preset IS NULL) <> (supplier_status IS NULL)", name="campaigns_one_selection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    preset: Mapped[str | None]
    supplier_status: Mapped[str | None]
    template: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]

    suppliers: Mapped[list["CampaignSupplier"]] = relationship()
    messages: Mapped[list["OutboxMessage"]] = relationship(order_by="OutboxMessage.id")


class CampaignSupplier(Base):
    """Снимок на момент запуска: в какой чат ушло сообщение поставщику или почему он исключён."""

    __tablename__ = "campaign_suppliers"
    __table_args__ = (
        CheckConstraint("(chat_id IS NULL) <> (cardinality(reasons) = 0)", name="campaign_suppliers_chat_or_reasons"),
    )

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), primary_key=True)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id"))
    reasons: Mapped[list[str]] = mapped_column(ARRAY(String))
    status: Mapped[str]


class OutboxMessage(Base):
    """Сообщение в очереди: одно на чат рассылки, текст уже с подстановками.

    pending → sending → sent; unknown — отправка прервалась и неизвестно, ушло ли сообщение.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("campaign_id", "chat_id"),
        CheckConstraint("status IN ('pending', 'sending', 'sent', 'unknown')", name="outbox_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    text: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    sent_at: Mapped[datetime | None]


class SenderState(Base):
    """Аккаунт, с которого уходит очередь, и когда ему можно отправить следующее сообщение.

    Пауза: с paused_at до paused_until по причине pause_reason, идёт, пока paused_until позже текущего времени.
    """

    __tablename__ = "sender_state"

    account: Mapped[str] = mapped_column(primary_key=True)
    next_send_at: Mapped[datetime | None]
    paused_at: Mapped[datetime | None]
    paused_until: Mapped[datetime | None]
    pause_reason: Mapped[str | None]


class InboxMessage(Base):
    """Входящее сообщение, как пришло из мессенджера.

    Разобранное — либо часть ответа (reply_id), либо с пометкой, почему не ответ (mark).
    """

    __tablename__ = "inbox"
    __table_args__ = (
        UniqueConstraint("chat_id", "external_id"),
        CheckConstraint("mark IS NULL OR reply_id IS NULL", name="inbox_reply_or_mark"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    # id сообщения в мессенджере: повторная выдача того же сообщения не добавит строку
    external_id: Mapped[str]
    sender: Mapped[str]
    received_at: Mapped[datetime]
    text: Mapped[str]
    attachment: Mapped[str | None]
    reply_id: Mapped[int | None] = mapped_column(ForeignKey("replies.id"))
    mark: Mapped[str | None]


class Reply(Base):
    """Ответ на сообщение рассылки: сообщения одного ника подряд с паузами меньше 10 минут.

    Поставщик пустой — непонятно, от какой компании: ответ остаётся на чате отправки.
    """

    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    outbox_id: Mapped[int] = mapped_column(ForeignKey("outbox.id"))
    sender: Mapped[str]
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    mark: Mapped[str | None]
    # Последнее сообщение: через 10 минут тишины после него ответ закрыт
    last_at: Mapped[datetime]

    messages: Mapped[list[InboxMessage]] = relationship(order_by=[InboxMessage.received_at, InboxMessage.id])
