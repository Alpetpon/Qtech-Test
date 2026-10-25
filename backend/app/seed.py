import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Base, Chat, OurAccount, Supplier, SupplierContact, chat_suppliers

log = logging.getLogger("app.seed")  # не __name__: при python -m app.seed он равен __main__

STATUSES = ("active", "new")


def load(session: Session, *dirs: Path) -> None:
    """Добавляет из папок с данными записи, которых ещё нет в базе.

    Существующие записи не меняет: после загрузки их правят рассылка (last_outbound_at)
    и «Применить» (прайс), и перезапуск сервиса не должен откатывать эти правки.
    """
    for row in read(dirs, "suppliers.json"):
        with skip_broken(session, "suppliers.json", row):
            if row["status"] not in STATUSES:
                log.warning(
                    "Поставщик %s «%s» пропущен: статус %r, а загружаем только active и new",
                    row["id"], row["name"], row["status"],
                )
                continue
            add(
                session,
                Supplier,
                id=row["id"],
                name=row["name"],
                status=row["status"],
                manager=row["manager"],
                price_updated_at=parse_time(row["price_updated_at"]),
                last_activity_at=parse_time(row["last_activity_at"]),
            )

    supplier_ids = set(session.scalars(select(Supplier.id)))

    for row in read(dirs, "chats.json"):
        with skip_broken(session, "chats.json", row):
            add(
                session,
                Chat,
                id=row["id"],
                title=row["title"],
                enabled=row["enabled"],
                member=row["member"],
                last_outbound_at=parse_time(row["last_outbound_at"]),
            )
            for supplier_id in row["supplier_ids"]:
                if supplier_id not in supplier_ids:
                    log.warning(
                        "Чат %s «%s»: поставщика %s нет в данных, связь пропущена, чат загружен",
                        row["id"], row["title"], supplier_id,
                    )
                    continue
                add(session, chat_suppliers, chat_id=row["id"], supplier_id=supplier_id)

    for handle in read(dirs, "our_accounts.json"):
        add(session, OurAccount, handle=handle)

    for row in read(dirs, "supplier_contacts.json"):
        with skip_broken(session, "supplier_contacts.json", row):
            if row["supplier_id"] not in supplier_ids:
                log.warning("Контакт %s пропущен: поставщика %s нет в данных", row["handle"], row["supplier_id"])
                continue
            add(session, SupplierContact, handle=row["handle"], supplier_id=row["supplier_id"])


def read(dirs: tuple[Path, ...], name: str) -> Iterator[Any]:
    for directory in dirs:
        path = directory / name
        if path.exists():
            yield from json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def skip_broken(session: Session, name: str, row: Any) -> Iterator[None]:
    """Запись без нужного поля или с неверной датой пропускается целиком, остальные загружаются."""
    try:
        with session.begin_nested():
            yield
    except KeyError as error:
        log.warning("%s: запись пропущена, нет поля %s: %s", name, error, row)
    except (TypeError, ValueError) as error:
        log.warning("%s: запись пропущена, неверное значение (%s): %s", name, error, row)


def add(session: Session, table: type[Base] | Table, **values: Any) -> None:
    session.execute(insert(table).values(**values).on_conflict_do_nothing())


def parse_time(value: str | None) -> datetime | None:
    """Дата без времени — полночь UTC; время без пояса — тоже UTC."""
    if value is None:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    dirs = (settings.data_dir, settings.data_dir / "extra")
    with SessionLocal.begin() as session:
        load(session, *dirs)
    log.info("Входные данные загружены из %s", ", ".join(map(str, dirs)))


if __name__ == "__main__":
    main()
