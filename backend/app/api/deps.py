"""Сессия базы и часы для роутов. Тесты подменяют их через app.dependency_overrides."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.clock import AppClock, Clock
from app.config import settings
from app.db import SessionLocal

clock = AppClock(settings.clock_start)


def get_clock() -> Clock:
    return clock


def get_session() -> Iterator[Session]:
    """Сессия на запрос. Пишущий роут сам делает commit; незакоммиченное откатится при закрытии."""
    with SessionLocal() as session:
        yield session


ClockDep = Annotated[Clock, Depends(get_clock)]
SessionDep = Annotated[Session, Depends(get_session)]
