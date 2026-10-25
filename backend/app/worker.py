import logging
import random
import signal
import threading
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.clock import AppClock, Clock
from app.config import settings
from app.db import engine
from app.domain.replies import Incoming
from app.messenger import inbox_replay
from app.messenger.sender import Sender, StubSender
from app.services import inbox, statuses
from app.services.sending import recover, send_next

log = logging.getLogger("app.worker")

TICK_SECONDS = 1


def run(stop: threading.Event) -> None:
    clock = AppClock(settings.clock_start)
    rng = random.Random()
    sender = StubSender(lambda attempt: rng.random() < settings.rate_limit_chance)
    # Входящие заглушки мессенджера — из тех же папок, что и справочники
    scenario = inbox_replay.load(settings.data_dir, settings.data_dir / "extra")
    start(clock, rng)
    while not stop.is_set():
        try:
            tick(clock, sender, rng)
        except Exception:
            log.exception("Шаг отправки упал, повторим на следующем тике")
        try:
            receive(clock, scenario)
        except Exception:
            log.exception("Шаг входящих упал, повторим на следующем тике")
        stop.wait(TICK_SECONDS)


def start(clock: Clock, rng: random.Random) -> None:
    """До первого тика: отправки, прерванные падением прошлого процесса, помечаем unknown."""
    with Session(engine) as session:
        recover(session, clock, rng)


def tick(clock: Clock, sender: Sender, rng: random.Random) -> None:
    """Весь шаг идет через одно соединение с базой. Блокировка аккаунта привязана к соединению,
    а в шаге несколько транзакций, если бы после каждой сессия брала из пула другое
    соединение, снимать блокировку в конце шага было бы не с чего"""
    with engine.connect() as connection:
        try:
            with Session(connection) as session:
                send_next(session, clock, sender, rng)
        except Exception:
            # Иначе соединение вернется в пул вместе с блокировкой, если шаг не успел ее снять
            connection.invalidate()
            raise


def receive(clock: Clock, scenario: Sequence[Incoming]) -> None:
    """Входящие, ответы и статусы — одной транзакцией: статусы считаются по ответам, разобранным на этом же шаге."""
    with Session(engine) as session, session.begin():
        delivered = inbox_replay.deliver(session, clock, scenario)
        inbox.process(session)
        changed = statuses.refresh(session, clock)
    if delivered or changed:
        log.info("Пришло входящих: %s, изменилось статусов поставщиков: %s", delivered, changed)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    stop = threading.Event()
    # Сигнал поднимает флаг, начатая отправка доходит до отметки sent, новая не начинается
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    log.info("Воркер отправки запущен, часы идут от %s", settings.clock_start.isoformat())
    run(stop)
    log.info("Воркер отправки остановлен")


if __name__ == "__main__":
    main()
