"""Предложение поставщика: можно ли применить его к прайсу."""

from dataclasses import dataclass
from decimal import Decimal

# Валюты ISO 4217, в которых принимаем цену: и от модели, и в правке человека
CURRENCIES = frozenset({"RUB", "USD", "EUR", "CNY", "GBP", "CHF", "JPY", "PLN", "TRY", "AED", "KZT", "BYN"})


@dataclass(frozen=True)
class Fields:
    """Поля предложения, которые уходят в прайс."""

    product: str | None
    price: Decimal | None
    currency: str | None
    moq: int | None = None
    lead_time_days: int | None = None
    regions: tuple[str, ...] = ()


def problems(supplier_id: str | None, fields: Fields) -> list[str]:
    """Почему предложение нельзя применить; пусто — можно. Партия, срок и регионы необязательны."""
    reasons = []
    if supplier_id is None:
        reasons.append("поставщик не выбран")
    if not (fields.product and fields.product.strip()):
        reasons.append("товар не указан")
    if fields.price is None:
        reasons.append("цена не указана")
    elif fields.price <= 0:
        reasons.append(f"цена {fields.price} — не больше нуля")
    if not fields.currency:
        reasons.append("валюта не указана")
    elif fields.currency not in CURRENCIES:
        reasons.append(f"валюта «{fields.currency}» не из списка")
    for name, value in (("партия", fields.moq), ("срок", fields.lead_time_days)):
        if value is not None and value < 0:
            reasons.append(f"{name} {value} — меньше нуля")
    return reasons
