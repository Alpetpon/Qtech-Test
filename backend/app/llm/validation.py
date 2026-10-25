import json
import re
from collections.abc import Iterable
from decimal import Decimal

from pydantic import ValidationError

from app.llm.schema import CURRENCIES, Analysis, Answer, AnswerItem, Item, Reply, ReplyType

# Размеры вида 40x30x30: их числа не бывают ни ценой, ни партией
DIMENSIONS = re.compile(r"\d+(?:[.,]\d+)?(?:\s*[xXхХ×*]\s*\d+(?:[.,]\d+)?)+")
# Число с разрядами («1 250,50», «1,250.50», «20 000») или простое («0.42», «0,71»);
# за ним — необязательный множитель тысяч («50k», «20 тыс.»)
NUMBER = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?(?!\d)|\d+(?:[.,]\d+)?)"
    r"(\s*(?:k|к|тыс)(?![a-zа-яё]))?",
    re.IGNORECASE,
)
SPACES = str.maketrans({" ": " ", " ": " ", " ": " "})
FENCE = re.compile(r"\s*```(?:json)?\s*(.*?)\s*```\s*", re.DOTALL)
TYPE_NAMES = {ReplyType.REFUSAL: "отказа", ReplyType.OTHER: "ответа «другое»"}


class AnswerError(ValueError):
    """Ответ модели — не JSON по схеме."""


def parse_answer(raw: str) -> Answer:
    if fenced := FENCE.fullmatch(raw):
        raw = fenced.group(1)
    try:
        return Answer.model_validate(json.loads(raw, parse_float=Decimal))
    except json.JSONDecodeError as error:
        raise AnswerError(f"не JSON: {error}") from error
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, problem['loc'])) or 'ответ'}: {problem['msg']}"
            for problem in error.errors()
        )
        raise AnswerError(f"не по схеме: {problems}") from error


def check(answer: Answer, reply: Reply) -> Analysis:
    """Проверяет ответ модели по тексту поставщика: что не прошло — поле пустое и причина в review."""
    review: list[str] = []
    found = numbers_in(reply.text)
    items = [item for raw in answer.items if (item := check_item(raw, found, review)) != Item()]
    if items and answer.type is not ReplyType.OFFER:
        review.append(f"у {TYPE_NAMES[answer.type]} заполнены поля предложения — поля очищены")
        items = []
    if not items and answer.type is ReplyType.OFFER:
        review.append("в предложении нет ни одной позиции")
    hint = answer.supplier_hint if answer.supplier_hint in reply.candidates else None
    return Analysis(answer.type, items, unique(answer.notes), unique(review), hint)


def check_item(raw: AnswerItem, found: set[Decimal], review: list[str]) -> Item:
    price = raw.price
    if price is not None and price <= 0:
        review.append(f"цена {price} — не больше нуля")
        price = None
    elif price is not None and price not in found:
        review.append(f"цена {price} не найдена в тексте")
        price = None

    currency = raw.currency.strip().upper() if raw.currency else None
    if currency and currency not in CURRENCIES:
        review.append(f"валюта «{raw.currency}» не из списка")
        currency = None
    if price is not None and not currency:
        review.append(f"у цены {price} нет валюты")

    moq = whole(raw.moq, "партия", review)
    if moq is not None and moq not in found:
        review.append(f"партия {moq} не найдена в тексте")
        moq = None

    return Item(
        product=(raw.product.strip() or None) if raw.product else None,
        price=price,
        currency=currency or None,
        moq=moq,
        lead_time_days=whole(raw.lead_time_days, "срок", review),
        regions=unique(raw.regions),
    )


def whole(value: Decimal | None, name: str, review: list[str]) -> int | None:
    if value is None:
        return None
    if value <= 0 or value != value.to_integral_value():
        review.append(f"{name} {value} — не целое число больше нуля")
        return None
    return int(value)


def numbers_in(text: str) -> set[Decimal]:
    """Числа из текста во всех прочтениях: «1,250» — и 1.25, и 1250; «50k» — и 50, и 50000."""
    text = DIMENSIONS.sub(" ; ", text.translate(SPACES))
    found = set()
    for match in NUMBER.finditer(text):
        token, thousands = match.groups()
        values = readings(token)
        found |= values
        if thousands:
            found |= {value * 1000 for value in values}
    return found


def readings(token: str) -> set[Decimal]:
    if " " in token:  # «1 250,50»: пробел делит разряды, запятая или точка — дробную часть
        return {Decimal(token.replace(" ", "").replace(",", "."))}
    separators = [char for char in token if char in ".,"]
    if not separators:
        return {Decimal(token)}
    if len(set(separators)) == 2:  # «1,250.50», «1.250,50»: последний знак — дробная часть
        integer, fraction = token.rsplit(separators[-1], 1)
        return {Decimal(integer.replace(",", "").replace(".", "") + "." + fraction)}
    if len(separators) > 1:  # «1,250,000»
        return {Decimal(token.replace(separators[0], ""))}
    integer, fraction = token.split(separators[0])
    values = {Decimal(f"{integer}.{fraction}")}
    if len(fraction) == 3 and len(integer) <= 3 and not integer.startswith("0"):  # «1,250»
        values.add(Decimal(integer + fraction))
    return values


def unique(values: Iterable[str]) -> list[str]:
    """Без пустых строк и повторов, порядок сохраняется."""
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
