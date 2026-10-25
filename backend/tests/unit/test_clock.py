from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.clock import ManualClock
from app.config import Settings

START = datetime(2026, 11, 1, 10, tzinfo=UTC)
MSK = timezone(timedelta(hours=3))


def test_manual_clock_is_set_and_advanced():
    clock = ManualClock(START)
    assert clock.now() == START

    clock.advance(timedelta(days=14, minutes=1))
    assert clock.now() == datetime(2026, 11, 15, 10, 1, tzinfo=UTC)

    clock.set(datetime(2026, 12, 1, tzinfo=UTC))
    assert clock.now() == datetime(2026, 12, 1, tzinfo=UTC)


def test_clock_returns_utc():
    clock = ManualClock(datetime(2026, 11, 1, 13, tzinfo=MSK))

    assert clock.now() == START
    assert clock.now().tzinfo is UTC


def test_time_without_zone_is_rejected():
    naive = datetime(2026, 11, 1, 10)

    with pytest.raises(ValueError):
        ManualClock(naive)
    with pytest.raises(ValueError):
        ManualClock(START).set(naive)


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
