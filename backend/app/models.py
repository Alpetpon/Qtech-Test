from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Table
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
