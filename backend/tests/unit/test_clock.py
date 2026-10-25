from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.clock import AppClock, ManualClock
from app.config import Settings

START = datetime(2026, 11, 1, 10, tzinfo=UTC)
MSK = timezone(timedelta(hours=3))


class FakeSeconds:
    """Подставной источник реального времени, секунды идут, только когда их добавили."""

    def __init__(self) -> None:
        self.value = 5000.0

    def __call__(self) -> float:
        return self.value


def test_manual_clock_is_set_and_advanced():
    clock = ManualClock(START)
    assert clock.now() == START

    clock.advance(timedelta(days=14, minutes=1))
    assert clock.now() == datetime(2026, 11, 15, 10, 1, tzinfo=UTC)

    clock.set(datetime(2026, 12, 1, tzinfo=UTC))
    assert clock.now() == datetime(2026, 12, 1, tzinfo=UTC)


def test_app_clock_is_start_plus_elapsed():
    seconds = FakeSeconds()
    clock = AppClock(START, seconds)
    assert clock.now() == START

    seconds.value += 90.5
    assert clock.now() == START + timedelta(seconds=90.5)

    seconds.value += 3 * 24 * 3600
    assert clock.now() == START + timedelta(days=3, seconds=90.5)


def test_clocks_return_utc():
    start = datetime(2026, 11, 1, 13, tzinfo=MSK)

    for clock in (ManualClock(start), AppClock(start, FakeSeconds())):
        assert clock.now() == START
        assert clock.now().tzinfo is UTC


def test_time_without_zone_is_rejected():
    naive = datetime(2026, 11, 1, 10)

    with pytest.raises(ValueError):
        ManualClock(naive)
    with pytest.raises(ValueError):
        ManualClock(START).set(naive)
    with pytest.raises(ValueError):
        AppClock(naive, FakeSeconds())


def test_start_defaults_to_task_now(monkeypatch):
    monkeypatch.delenv("CLOCK_START", raising=False)

    assert Settings().clock_start == START


def test_start_is_overridden_from_env(monkeypatch):
    monkeypatch.setenv("CLOCK_START", "2026-11-05T08:30:00Z")

    assert Settings().clock_start == datetime(2026, 11, 5, 8, 30, tzinfo=UTC)


def test_start_without_zone_in_env_is_rejected(monkeypatch):
    monkeypatch.setenv("CLOCK_START", "2026-11-05T08:30:00")

    with pytest.raises(ValidationError):
        Settings()
