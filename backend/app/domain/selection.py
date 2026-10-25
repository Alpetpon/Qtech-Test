from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.entities import Supplier

PRICE_MAX_AGE_DAYS = 60
ACTIVITY_MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class Selected:
    supplier: Supplier
    reason: str


def by_status(suppliers: Iterable[Supplier], status: str) -> list[Selected]:
    return [Selected(supplier, f"статус {status}") for supplier in suppliers if supplier.status == status]


def stale_price(
    suppliers: Iterable[Supplier], now: datetime, max_age_days: int = PRICE_MAX_AGE_DAYS
) -> list[Selected]:
    """Пресет «Активные, прайс устарел»: active, прайс старше max_age_days дней или прайса нет."""
    selected = []
    for supplier in suppliers:
        if supplier.status != "active":
            continue
        if supplier.price_updated_at is None:
            selected.append(Selected(supplier, "прайса нет"))
        elif (age := days_since(supplier.price_updated_at, now)) > max_age_days:
            selected.append(Selected(supplier, f"прайс не обновлялся {age} дн."))
    return selected


def inactive_new(
    suppliers: Iterable[Supplier], now: datetime, max_age_days: int = ACTIVITY_MAX_AGE_DAYS
) -> list[Selected]:
    """Пресет «Новые без движения»: new, активность старше max_age_days дней или её не было."""
    selected = []
    for supplier in suppliers:
        if supplier.status != "new":
            continue
        if supplier.last_activity_at is None:
            selected.append(Selected(supplier, "активности не было"))
        elif (age := days_since(supplier.last_activity_at, now)) > max_age_days:
            selected.append(Selected(supplier, f"нет активности {age} дн."))
    return selected


def days_since(moment: datetime, now: datetime) -> int:
    """Календарные дни по UTC, время суток не учитывается: с 02.09 до 01.11 10:00 — 60 дней."""
    return (now.astimezone(UTC).date() - moment.astimezone(UTC).date()).days
