"""Точность разбора ответов поставщиков: результат модели против разметки.

Разметка и результат — словари одного вида: type (offer / refusal / other),
items — позиции с полями FIELDS, notes — пометки модели по тексту.
У результата ещё review — что из ответа модели не приняла проверка;
в разметке его нет: у верного ответа он пустой.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from itertools import permutations

FIELDS = ("product", "price", "currency", "moq", "lead_time_days", "regions")

# Одно и то же место пишут по-разному
REGION_ALIASES = {
    "ес": "eu",
    "евросоюз": "eu",
    "европа": "eu",
    "мо": "московская область",
    "подмосковье": "московская область",
    "worldwide": "весь мир",
    "по всему миру": "весь мир",
}
# «Коробки» и «коробка» — одно слово: сравниваем начало
WORD_STEM = 5


@dataclass(frozen=True)
class Ratio:
    correct: int
    total: int

    def __str__(self) -> str:
        return f"{self.correct}/{self.total} ({self.correct / self.total:.0%})" if self.total else "—"


@dataclass(frozen=True)
class Comparison:
    field: str
    expected: object
    actual: object
    ok: bool

    @property
    def filled(self) -> bool:
        """В разметке есть значение. Если нет — проверяем, что модель его не выдумала."""
        return not is_empty(self.expected)


@dataclass(frozen=True)
class ReplyScore:
    expected_type: str
    actual_type: str
    comparisons: tuple[Comparison, ...]
    notes_ok: bool
    review_ok: bool
    errors: tuple[str, ...]

    @property
    def type_ok(self) -> bool:
        return self.expected_type == self.actual_type

    @property
    def fields_ok(self) -> bool:
        return self.type_ok and all(c.ok for c in self.comparisons)


@dataclass(frozen=True)
class Summary:
    replies: int
    types: Ratio
    all_fields: Ratio
    notes: Ratio
    review: Ratio
    confusion: Counter[tuple[str, str]]
    fields: dict[str, Ratio]
    filled: dict[str, Ratio]
    empty: dict[str, Ratio]


def is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str | list | tuple) and not value)


def same(field: str, expected: object, actual: object) -> bool:
    if is_empty(expected) or is_empty(actual):
        return is_empty(expected) and is_empty(actual)
    match field:
        case "product":
            return product_words(expected) <= product_words(actual)
        case "price":
            return Decimal(str(expected)) == Decimal(str(actual))
        case "currency":
            return expected.upper() == actual.upper()
        case "regions":
            return region_names(expected) == region_names(actual)
        case _:
            return expected == actual


def product_words(text: str) -> set[str]:
    """Слова названия без словоформ; числа и размеры — точно, «х» и «×» в размерах = «x»."""
    text = re.sub(r"(?<=\d)\s*[x×х*]\s*(?=\d)", "x", text.casefold().replace("ё", "е"))
    words = re.findall(r"\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)*|[^\W\d_]+", text)
    return {word.replace(",", ".") if word[0].isdigit() else word[:WORD_STEM] for word in words}


def region_names(regions: Iterable[str]) -> set[str]:
    names = {region.casefold().replace("ё", "е").strip() for region in regions}
    return {REGION_ALIASES.get(name, name) for name in names}


def pair_items(expected: list[dict], actual: list[dict]) -> list[tuple[dict, dict]]:
    """Сопоставляет позиции разметки и модели так, чтобы совпало больше всего полей.

    Недостающие позиции — пустые словари: пропущенную позицию сравниваем с пустой,
    лишнюю — тоже. Позиций в ответе мало, поэтому перебираем все варианты;
    при равенстве остаётся порядок из ответа.
    """
    size = max(len(expected), len(actual))
    expected = expected + [{}] * (size - len(expected))
    actual = actual + [{}] * (size - len(actual))

    def matched(order: tuple[dict, ...]) -> int:
        return sum(same(f, e.get(f), a.get(f)) for e, a in zip(expected, order) for f in FIELDS)

    return list(zip(expected, max(permutations(actual), key=matched)))


def score_reply(expected: dict, actual: dict) -> ReplyScore:
    comparisons = []
    errors = []
    if expected["type"] != actual["type"]:
        errors.append(f"тип: ожидали {expected['type']}, модель — {actual['type']}")
    for exp_item, act_item in pair_items(expected["items"], actual["items"]):
        for field in FIELDS:
            exp_value, act_value = exp_item.get(field), act_item.get(field)
            ok = same(field, exp_value, act_value)
            comparisons.append(Comparison(field, exp_value, act_value, ok))
            if not ok and exp_item and act_item:
                errors.append(f"«{exp_item['product']}», {field}: ожидали {show(exp_value)}, модель — {show(act_value)}")
        if not act_item:
            errors.append(f"пропущена позиция «{exp_item['product']}»")
        if not exp_item:
            errors.append(f"лишняя позиция «{act_item.get('product')}»")
    notes_ok = bool(expected["notes"]) == bool(actual["notes"])
    if not notes_ok:
        if expected["notes"]:
            errors.append(f"нет пометки, ожидали: {'; '.join(expected['notes'])}")
        else:
            errors.append(f"лишняя пометка: {'; '.join(actual['notes'])}")
    errors += (f"проверка: {reason}" for reason in actual["review"])
    return ReplyScore(
        expected["type"], actual["type"], tuple(comparisons), notes_ok, not actual["review"], tuple(errors)
    )


def summarize(scores: list[ReplyScore]) -> Summary:
    by_field = {f: [c for s in scores for c in s.comparisons if c.field == f] for f in FIELDS}
    return Summary(
        replies=len(scores),
        types=ratio(s.type_ok for s in scores),
        all_fields=ratio(s.fields_ok for s in scores),
        notes=ratio(s.notes_ok for s in scores),
        review=ratio(s.review_ok for s in scores),
        confusion=Counter((s.expected_type, s.actual_type) for s in scores),
        fields={f: ratio(c.ok for c in cs) for f, cs in by_field.items()},
        filled={f: ratio(c.ok for c in cs if c.filled) for f, cs in by_field.items()},
        empty={f: ratio(c.ok for c in cs if not c.filled) for f, cs in by_field.items()},
    )


def ratio(oks: Iterable[bool]) -> Ratio:
    oks = list(oks)
    return Ratio(sum(oks), len(oks))


def show(value: object) -> str:
    if is_empty(value):
        return "пусто"
    if isinstance(value, list | tuple):
        return ", ".join(value)
    return str(value)
