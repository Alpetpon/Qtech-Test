import logging
import random
import signal
import threading

from sqlalchemy.orm import Session

from app.clock import AppClock, Clock
from app.config import settings
from app.db import engine
from app.messenger.sender import Sender, StubSender
from app.services.sending import send_next

log = logging.getLogger("app.worker")

TICK_SECONDS = 1


def run(stop: threading.Event) -> None:
    clock = AppClock(settings.clock_start)
    sender = StubSender()
    rng = random.Random()
    while not stop.is_set():
        try:
            tick(clock, sender, rng)
        except Exception:
            log.exception("Шаг отправки упал, повторим на следующем тике")
        stop.wait(TICK_SECONDS)


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
