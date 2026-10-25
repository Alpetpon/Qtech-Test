"""Шаблон сообщения: подстановки {supplier} и {manager} для одного чата.

Поставщики здесь — выбранные поставщики чата, которому сообщение реально уходит:
исключённые сюда не попадают и запуск не блокируют.
"""

import re
from collections.abc import Sequence

from app.domain.entities import Supplier

SUPPLIER = "{supplier}"
MANAGER = "{manager}"

# Подстановка или одиночная скобка. Фигурные скобки в шаблоне — только для подстановок,
# поэтому «{suplier}», «{ supplier }» и «{supplier» не уйдут поставщику обычным текстом
TOKEN = re.compile(r"\{[^{}]*\}|[{}]")


def check(template: str, suppliers: Sequence[Supplier]) -> list[str]:
    """Причины, по которым запуск блокируется; пустой список — можно отправлять.

    Значения проверяем только у подстановок, которые есть в шаблоне.
    """
    if blank(template):
        return ["Шаблон пустой"]

    problems = []
    used = set()
    for token in TOKEN.findall(template):
        if token in (SUPPLIER, MANAGER):
            used.add(token)
        elif token in ("{", "}"):
            problems.append(f"Лишняя скобка «{token}»: фигурные скобки — только для {SUPPLIER} и {MANAGER}")
        else:
            problems.append(f"Неизвестная подстановка {token}: есть только {SUPPLIER} и {MANAGER}")

    if SUPPLIER in used:
        problems += [f"У поставщика {s.id} пустое название" for s in suppliers if blank(s.name)]
    if MANAGER in used:
        problems += [f"У {label(s)} пустой менеджер" for s in suppliers if blank(s.manager)]
        if len(managers(suppliers)) > 1:
            pairs = ", ".join(f"{label(s)} — {s.manager.strip()}" for s in suppliers if not blank(s.manager))
            problems.append(
                f"У поставщиков одного чата разные менеджеры: {pairs}. В {MANAGER} подставляется один"
            )

    return list(dict.fromkeys(problems))


def render(template: str, suppliers: Sequence[Supplier]) -> str:
    """Текст сообщения для чата. Если запуск заблокирован, падает с причинами из check."""
    if problems := check(template, suppliers):
        raise ValueError("; ".join(problems))
    values = {
        SUPPLIER: ", ".join(s.name.strip() for s in suppliers),
        MANAGER: ", ".join(managers(suppliers)),
    }
    # Одним проходом: «{manager}» внутри названия поставщика второй раз не подставится
    return TOKEN.sub(lambda match: values[match.group()], template)


def blank(value: str | None) -> bool:
    """Пустое значение — None, пустая строка или одни пробелы."""
    return value is None or not value.strip()


def managers(suppliers: Sequence[Supplier]) -> list[str]:
    """Менеджеры поставщиков чата без пустых и повторов, в порядке поставщиков."""
    return list(dict.fromkeys(s.manager.strip() for s in suppliers if not blank(s.manager)))


def label(supplier: Supplier) -> str:
    return supplier.name.strip() or supplier.id
