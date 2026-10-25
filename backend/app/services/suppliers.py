"""Справочник поставщиков: менеджера, которого нет в данных, человек вписывает в превью перед запуском.

Правка живёт только в базе: загрузка данных при старте существующих поставщиков не меняет.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.domain.template import blank


def set_manager(session: Session, supplier_id: str, manager: str) -> models.Supplier | None:
    """Менеджер поставщика — в транзакции вызывающего. None — такого поставщика нет."""
    supplier = session.get(models.Supplier, supplier_id)
    if supplier is not None:
        supplier.manager = manager
    return supplier


def known_managers(session: Session) -> list[str]:
    """Подсказки имён: непустые менеджеры поставщиков без повторов, по алфавиту."""
    managers = session.scalars(select(models.Supplier.manager))
    return sorted({manager.strip() for manager in managers if not blank(manager)})
