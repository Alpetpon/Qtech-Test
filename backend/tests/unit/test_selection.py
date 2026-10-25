from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from app.domain.entities import Supplier
from app.domain.selection import Selected, by_status, days_since, inactive_new, stale_price

SUPPLIER = Supplier(id="x", name="X", status="active", manager="Анна", price_updated_at=None, last_activity_at=None)


def ids(selected: list[Selected]) -> list[str]:
    return [item.supplier.id for item in selected]


def reasons(selected: list[Selected]) -> dict[str, str]:
    return {item.supplier.id: item.reason for item in selected}


def test_presets_on_task_data(task_data, now):
    suppliers, _ = task_data
    assert reasons(stale_price(suppliers, now)) == {
        "s1": "прайс не обновлялся 114 дн.",
        "s2": "прайс не обновлялся 123 дн.",
        "s5": "прайс не обновлялся 184 дн.",
    }
    assert reasons(inactive_new(suppliers, now)) == {
        "s3": "нет активности 61 дн.",
        "s4": "нет активности 78 дн.",
        "s6": "нет активности 52 дн.",
    }


def test_stale_price_on_full_data(full_data, now):
    suppliers, _ = full_data
    selected = stale_price(suppliers, now)
    assert ids(selected) == ["s1", "s2", "s5", "s7", "s8", "s10", "s13", "s14", "s15", "s18", "s20", "s21"]
    assert reasons(selected)["s8"] == "прайса нет"


def test_inactive_new_on_full_data(full_data, now):
    suppliers, _ = full_data
    assert ids(inactive_new(suppliers, now)) == ["s3", "s4", "s6", "s12", "s17"]


def test_price_exactly_60_days_is_not_stale(full_data, now):
    """Pinewood: 02.09 → 01.11 10:00 — по точному времени больше 60 суток, по календарю ровно 60."""
    suppliers, _ = full_data
    selected = reasons(stale_price(suppliers, now))
    assert "s9" not in selected
    assert selected["s10"] == "прайс не обновлялся 61 дн."


def test_activity_exactly_30_days_is_not_stale(full_data, now):
    suppliers, _ = full_data
    selected = reasons(inactive_new(suppliers, now))
    assert "s11" not in selected
    assert selected["s12"] == "нет активности 31 дн."


def test_days_are_counted_in_utc(now):
    """02.09 01:00 по Москве — это 01.09 по UTC: 61 день, а не 60."""
    moscow = timezone(timedelta(hours=3))
    price = datetime(2026, 9, 2, 1, tzinfo=moscow)
    assert days_since(price, now) == 61
    assert days_since(price, now.astimezone(moscow)) == 61


def test_future_dates_are_fresh(now):
    future = datetime(2026, 11, 3, tzinfo=UTC)
    assert stale_price([replace(SUPPLIER, price_updated_at=future)], now) == []
    assert inactive_new([replace(SUPPLIER, status="new", last_activity_at=future)], now) == []


def test_new_without_activity_is_selected(now):
    supplier = replace(SUPPLIER, status="new", last_activity_at=None)
    assert inactive_new([supplier], now) == [Selected(supplier, "активности не было")]


def test_thresholds_are_parameters(task_data, now):
    suppliers, _ = task_data
    assert ids(stale_price(suppliers, now, max_age_days=120)) == ["s2", "s5"]
    assert ids(inactive_new(suppliers, now, max_age_days=60)) == ["s3", "s4"]


def test_manual_status_filter(full_data):
    suppliers, _ = full_data
    selected = by_status(suppliers, "new")
    assert ids(selected) == ["s3", "s4", "s6", "s11", "s12", "s16", "s17", "s19"]
    assert {item.reason for item in selected} == {"статус new"}
    assert len(by_status(suppliers, "active")) == 13
