"""Часы сервиса: время, указанное в задании используем, вместо системного времени."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine, Row, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import ClockState


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущий момент, всегда aware и в UTC."""
        ...


class DemoClock(Clock, Protocol):
    """Часы, которые в демо перематывают вперёд."""

    def advance(self, delta: timedelta) -> None: ...


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


def real_time() -> datetime:
    # Настенное, а не монотонное: момент первого запуска хранится в базе и сравнивается
    # между процессами и после перезагрузки машины
    return datetime.now(UTC)


class SharedClock:
    """Часы приложения, общие для API и воркера: текущее время = старт + реальное время с первого запуска + перемотка.

    Состояние — строка clock в базе. Её создаёт первый процесс, которому понадобилось время, остальные
    читают её же: время у процессов совпадает и после перезапуска идёт дальше, а не с начала.
    Строка читается на каждый now(): она одна и чтение дешёвое, зато перемотку другой процесс видит сразу.
    """

    def __init__(self, engine: Engine, start: datetime, real: Callable[[], datetime] = real_time) -> None:
        self._engine = engine
        self._start = to_utc(start)
        self._real = real

    def now(self) -> datetime:
        start, real_start, shift = self._state()
        return to_utc(start + (self._real() - real_start) + shift)

    def advance(self, delta: timedelta) -> None:
        """Перемотка вперёд. Назад нельзя: окна 14 дней, паузы и «молчит» уже отсчитаны от прошедшего времени."""
        if delta <= timedelta(0):
            raise ValueError(f"Часы идут только вперёд, а перемотка на {delta}")
        # Перемотка до первого now(): строку надо создать, иначе прибавлять не к чему
        self._state()
        with self._engine.begin() as connection:
            # Прибавляет база: две перемотки сразу не затрут одна другую
            connection.execute(update(ClockState).values(shift=ClockState.shift + delta))

    def _state(self) -> Row[datetime, datetime, timedelta]:
        query = select(ClockState.start, ClockState.real_start, ClockState.shift)
        with self._engine.begin() as connection:
            state = connection.execute(query).one_or_none()
            if state is None:
                # API и воркер стартуют вместе: строку вставит один, второй дождётся его транзакции и прочитает её же
                connection.execute(
                    insert(ClockState)
                    .values(id=1, start=self._start, real_start=self._real(), shift=timedelta(0))
                    .on_conflict_do_nothing()
                )
                state = connection.execute(query).one()
        return state


def to_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"Время без часового пояса: {moment.isoformat()}")
    return moment.astimezone(UTC)
