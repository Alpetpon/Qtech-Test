"""Запросы и ответы HTTP API. По этим моделям фронтенд генерирует свои типы из OpenAPI."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.seed import STATUSES
from app.services.campaigns import PRESETS

PresetId = Literal[tuple(PRESETS)]
SupplierStatus = Literal[STATUSES]


class Schema(BaseModel):
    """Ответ: поля читаются из атрибутов объектов сервисов и строк базы."""

    model_config = ConfigDict(from_attributes=True)


class Problem(BaseModel):
    """Отказ: почему ничего не сделано — для человека."""

    detail: list[str]


class Preset(Schema):
    id: PresetId
    title: str


class Options(Schema):
    """Чем выбрать поставщиков рассылки и шаблон по умолчанию."""

    presets: list[Preset]
    statuses: list[SupplierStatus]
    default_template: str


class Launch(BaseModel):
    """Рассылка: поставщиков выбирают пресетом или статусом — ровно одно из двух."""

    preset: PresetId | None = None
    supplier_status: SupplierStatus | None = None
    template: str = Field(description="Текст с подстановками {supplier} и {manager}")

    @model_validator(mode="after")
    def one_selection(self) -> Self:
        if (self.preset is None) == (self.supplier_status is None):
            raise PydanticCustomError("selection", "Выберите пресет или статус поставщиков — ровно одно из двух")
        return self


class SupplierRef(Schema):
    id: str
    name: str


class ChatRef(Schema):
    id: str
    title: str


class Totals(Schema):
    selected: int = Field(description="Сколько поставщиков выбрано, вместе с исключёнными")
    chats: int = Field(description="Сколько чатов получат сообщение: в общий чат уходит одно")
    excluded: int


class Outgoing(Schema):
    chat: ChatRef
    suppliers: list[SupplierRef]
    text: str | None = Field(description="Сообщение, как оно уйдёт; пусто — шаблон для чата не заполнить")


class Excluded(Schema):
    supplier: SupplierRef
    reasons: list[str]


class Preview(Schema):
    totals: Totals
    outgoing: list[Outgoing]
    excluded: list[Excluded]
    problems: list[str] = Field(description="Почему запуск заблокирован; пусто — можно запускать")


class Queue(Schema):
    """Сообщения рассылки по статусам очереди: по одному на чат."""

    pending: int = 0
    sending: int = 0
    sent: int = 0
    unknown: int = Field(0, description="Отправка прервалась, неизвестно, ушло ли: повторно не уходит, проверьте чат")


class Campaign(Schema):
    id: int
    preset: PresetId | None
    supplier_status: SupplierStatus | None
    template: str
    created_at: datetime
    status: Literal["sending", "paused", "sent"]
    queue: Queue


class Pause(Schema):
    reason: str
    since: datetime
    until: datetime = Field(description="Потом очередь продолжится сама; раньше — кнопкой «Продолжить»")


class Sender(Schema):
    """Общий аккаунт, с которого уходят все рассылки: пауза и лимит у него одни на всех."""

    account: str
    pause: Pause | None
    sent_last_day: int = Field(description="Отправлено за последние 24 часа")
    daily_limit: int


class CampaignStatus(Schema):
    campaign: Campaign
    sender: Sender


class Message(Schema):
    sender: str
    received_at: datetime
    text: str
    attachment: str | None


class Offer(Schema):
    id: int
    supplier_id: str | None = Field(description="Пусто — непонятно, от какой компании: поставщика выбирает человек")
    product: str | None
    price: Decimal | None
    currency: str | None
    moq: int | None = Field(description="Минимальная партия")
    lead_time_days: int | None
    regions: list[str]
    quote: str = Field(description="Сообщения поставщика, которые разбирала модель")
    review: list[str] = Field(description="Что мешает применить; пусто — можно применять")
    edited_at: datetime | None = Field(description="Когда человек правил поля или выбирал поставщика")
    applied_at: datetime | None
    supplement_at: datetime | None = Field(
        description="Пришло дополнение после правки или применения; его разбор — отдельное предложение"
    )


class Reply(Schema):
    id: int
    chat: ChatRef
    sender: str
    messages: list[Message]
    type: Literal["offer", "refusal", "other"] | None = Field(description="Пусто — ответ ещё не разобран моделью")
    notes: list[str] = Field(description="Пометки модели: «цена во вложении: …»")
    review: list[str] = Field(description="Проверить вручную: что из ответа модели не прошло проверку")
    mark: str | None = Field(description="Почему ответ не засчитан поставщику")
    supplier: SupplierRef | None
    supplier_hint: SupplierRef | None = Field(description="Кто ответил, по мнению модели, если ника нет в справочнике")
    candidates: list[SupplierRef] = Field(description="Из кого выбрать поставщика предложений, если он непонятен")
    offers: list[Offer]


class ReportSupplier(Schema):
    id: str
    name: str
    status: Literal["queued", "excluded", "sent", "unknown", "silent", "replied", "offer", "refusal", "other"]
    chat: ChatRef | None
    reasons: list[str] = Field(description="Почему исключён из рассылки")
    price_updated_at: datetime | None
    replies: list[Reply]


class Report(Schema):
    campaign: Campaign
    suppliers: list[ReportSupplier]
    unassigned: list[Reply] = Field(description="Ответы в чатах рассылки, не засчитанные ни одному её поставщику")


class OfferFields(BaseModel):
    """Поля предложения целиком, как их поправил человек. Проверяются при «Применить»."""

    product: str | None
    price: Decimal | None
    currency: str | None
    moq: int | None
    lead_time_days: int | None
    regions: list[str]


class SupplierChoice(BaseModel):
    supplier_id: str


class Price(Schema):
    """Запись в прайсе поставщика из применённого предложения."""

    id: int
    supplier_id: str
    offer_id: int
    product: str
    price: Decimal
    currency: str
    moq: int | None
    lead_time_days: int | None
    regions: list[str]
    applied_at: datetime


class Unbound(Schema):
    id: int
    chat: ChatRef
    sender: str
    received_at: datetime
    text: str
    attachment: str | None
    mark: str = Field(description="Почему не засчитано: вне окна, в чат не отправляли, непонятно от кого…")
    campaign_id: int | None


class Clock(Schema):
    """Часы задания: текущее время — общее для API и воркера."""

    now: datetime


class Advance(BaseModel):
    """Перемотка часов вперёд, чтобы в демо увидеть ответы поставщиков и «молчит» без ожидания."""

    # Больше года за раз демо не нужно, а огромный сдвиг вывел бы время сервиса за пределы дат и остановил бы часы
    by: timedelta = Field(
        gt=timedelta(0),
        le=timedelta(days=365),
        description="ISO 8601: PT1H — час, P1D — день; число — секунды. Только вперёд, не больше года за раз",
    )
