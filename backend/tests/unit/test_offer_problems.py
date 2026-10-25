from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.offers import Fields, problems

NORDPACK = Fields("Коробки 40x30x30", Decimal("0.42"), "EUR", 5000, 10, ("EU",))


def test_complete_offer_can_be_applied():
    assert problems("s1", NORDPACK) == []


def test_moq_lead_time_and_regions_are_optional():
    assert problems("s1", Fields("Коробки 40x30x30", Decimal("0.42"), "EUR")) == []
    assert problems("s1", replace(NORDPACK, moq=0, lead_time_days=0)) == []


def test_kestrel_without_price_cannot_be_applied():
    assert problems("s3", Fields("пищевые контейнеры", None, None)) == ["цена не указана", "валюта не указана"]


def test_all_reasons_at_once():
    assert problems(None, Fields(None, None, None, -1, -1)) == [
        "поставщик не выбран",
        "товар не указан",
        "цена не указана",
        "валюта не указана",
        "партия -1 — меньше нуля",
        "срок -1 — меньше нуля",
    ]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"product": "  "}, "товар не указан"),
        ({"price": Decimal("0")}, "цена 0 — не больше нуля"),
        ({"price": Decimal("-0.42")}, "цена -0.42 — не больше нуля"),
        ({"currency": ""}, "валюта не указана"),
        ({"currency": "eur"}, "валюта «eur» не из списка"),
        ({"currency": "ЕВРО"}, "валюта «ЕВРО» не из списка"),
        ({"moq": -5000}, "партия -5000 — меньше нуля"),
        ({"lead_time_days": -10}, "срок -10 — меньше нуля"),
    ],
)
def test_wrong_field_is_named(changes, reason):
    assert problems("s1", replace(NORDPACK, **changes)) == [reason]
