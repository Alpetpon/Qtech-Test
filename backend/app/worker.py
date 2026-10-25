import logging
import random
import signal
import threading
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.clock import Clock, SharedClock
from app.config import settings
from app.db import engine
from app.domain.replies import Incoming
from app.llm import LLMClient, LLMError
from app.llm.client import ApiClient, ReplayClient
from app.messenger import inbox_replay
from app.messenger.sender import Sender, StubSender
from app.services import analysis, inbox, statuses
from app.services.sending import recover, send_next

log = logging.getLogger("app.worker")

TICK_SECONDS = 1
# Модель недоступна — следующий круг разбора через минуту, а не на каждом тике
ANALYSIS_RETRY_SECONDS = 60


def run(stop: threading.Event) -> None:
    clock = SharedClock(engine, settings.clock_start)
    rng = random.Random()
    sender = StubSender(lambda attempt: rng.random() < settings.rate_limit_chance)
    # Входящие заглушки мессенджера — из тех же папок, что и справочники
    scenario = inbox_replay.load(settings.data_dir, settings.data_dir / "extra")
    client = llm_client()
    start(clock, rng)
    log.info("Сейчас по часам задания: %s", clock.now().isoformat())
    # Модель отвечает секундами: разбор в своём потоке, чтобы отправка и приём её не ждали. Поток-демон:
    # разбор сохраняется одной транзакцией, и прерванный остановкой ответ разберётся после перезапуска
    threading.Thread(target=analyze_loop, args=(stop, clock, client), name="analysis", daemon=True).start()
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


def llm_client() -> LLMClient:
    """Модель по API; без ключа — записанные ответы, чтобы демо на данных задания шло и без него."""
    try:
        return ApiClient.from_settings()
    except LLMError:
        log.warning(
            "LLM_API_KEY не задан: ответы разбираются по записанным ответам модели из %s. "
            "Ответ, для которого записи нет, ждёт разбора — положите ключ в .env",
            settings.llm_recordings_dir,
        )
        return ReplayClient(settings.llm_recordings_dir)


def analyze_loop(stop: threading.Event, clock: Clock, client: LLMClient) -> None:
    while not stop.is_set():
        try:
            failed = analyze(clock, client)
        except Exception:
            log.exception("Шаг разбора ответов упал, повторим позже")
            failed = True
        stop.wait(ANALYSIS_RETRY_SECONDS if failed else TICK_SECONDS)


def analyze(clock: Clock, client: LLMClient) -> bool:
    """Разбирает закрытые ответы; True — модель ответила не на все, повторим позже."""
    with Session(engine) as session:
        result = analysis.run(session, clock, client, settings.llm_model)
    if result.analyzed:
        log.info("Разобрано ответов моделью: %s", result.analyzed)
    return result.failed > 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    stop = threading.Event()
    # Сигнал поднимает флаг, начатая отправка доходит до отметки sent, новая не начинается
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    log.info("Воркер отправки запущен")
    run(stop)
    log.info("Воркер отправки остановлен")


if __name__ == "__main__":
    main()
