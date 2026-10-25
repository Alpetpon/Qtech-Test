from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, WithJsonSchema


class ReplyType(StrEnum):
    OFFER = "offer"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass(frozen=True)
class Reply:
    """Ответ поставщика, как он уходит в модель."""

    # Сообщения поставщика одного ответа, склеенные через перевод строки
    text: str
    attachments: tuple[str, ...] = ()
    # Поставщики общего чата, если отправитель не опознан: модель подсказывает, кто из них ответил
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    product: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    regions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Analysis:
    """Разбор ответа после проверки: поля, которые проверку не прошли, уже пустые."""

    type: ReplyType
    items: list[Item] = field(default_factory=list)
    # Пометки модели по тексту: «цена во вложении: price_2026.pdf»
    notes: list[str] = field(default_factory=list)
    # Причины «проверить вручную»: что из ответа модели проверка не приняла
    review: list[str] = field(default_factory=list)
    supplier_hint: str | None = None

    @property
    def needs_review(self) -> bool:
        return bool(self.review)


# Ответ модели. Числа — Decimal без float; что партия и срок целые, проверяем сами,
# чтобы дробное значение очищало одно поле, а не браковало весь ответ
Number = Annotated[Decimal, WithJsonSchema({"type": "number"})]
Integer = Annotated[Decimal, WithJsonSchema({"type": "integer"})]


class AnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str | None
    price: Number | None
    currency: str | None
    moq: Integer | None
    lead_time_days: Integer | None
    regions: list[str]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ReplyType
    items: list[AnswerItem]
    notes: list[str]
    supplier_hint: str | None


# JSON-схема для structured outputs: все поля обязательны, лишних нет
ANSWER_SCHEMA = Answer.model_json_schema()
