"""Часы сервиса: время, указанное в задании используем, вместо системного времени."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущий момент, всегда aware и в UTC."""
        ...


class ManualClock:
    """Часы для тестов: находятся без движения, пока их не переставят или не перемотают."""

    def __init__(self, now: datetime) -> None:
        self._now = to_utc(now)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = to_utc(now)

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class AppClock:
    """Часы приложения: начинают ход от времени, указанного в задании, идут вперед вместе с реальным временем.

    Отсчет идет от создания часов, то есть от старта процесса. Источник секунд
    по умолчанию монотонный, перевод системных часов не сдвигает время сервиса.
    """

    def __init__(self, start: datetime, seconds: Callable[[], float] = time.monotonic) -> None:
        self._start = to_utc(start)
        self._seconds = seconds
        self._origin = seconds()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._seconds() - self._origin)


def to_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"Время без часового пояса: {moment.isoformat()}")
    return moment.astimezone(UTC)
