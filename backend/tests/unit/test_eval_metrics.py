from collections import Counter
from decimal import Decimal

from app.eval_metrics import Ratio, pair_items, same, score_reply, summarize

BOXES = {"product": "Коробки 40x30x30", "price": "0.42", "currency": "EUR", "moq": 5000, "lead_time_days": 10, "regions": ["EU"]}
TAPE = {"product": "Скотч 48 мм", "price": "0.60", "currency": "USD", "moq": 200, "lead_time_days": 5, "regions": ["Казахстан"]}
FILM = {"product": "Стрейч-плёнка 500 мм", "price": "1.85", "currency": "USD", "moq": 50, "lead_time_days": 5, "regions": ["Казахстан"]}
CONTAINERS = {"product": "Пищевые контейнеры", "price": None, "currency": None, "moq": None, "lead_time_days": None, "regions": []}
REFUSAL = {"type": "refusal", "items": [], "notes": [], "review": []}


def offer(*items: dict, notes: tuple[str, ...] = (), review: tuple[str, ...] = ()) -> dict:
    return {"type": "offer", "items": list(items), "notes": list(notes), "review": list(review)}


def test_price_is_compared_as_number():
    assert same("price", "0.40", Decimal("0.4"))
    assert same("price", "1250.50", Decimal("1250.5"))
    assert not same("price", "0.42", "0.39")


def test_empty_matches_only_empty():
    assert same("lead_time_days", None, None)
    assert same("regions", [], None)
    assert not same("price", None, "0.42")
    assert not same("price", "0.42", None)


def test_currency_ignores_case():
    assert same("currency", "EUR", "eur")
    assert not same("currency", "EUR", "USD")


def test_regions_ignore_order_case_and_spelling():
    assert same("regions", ["Польша", "Германия"], ["германия", "Польша"])
    assert same("regions", ["EU"], ["ЕС"])
    assert same("regions", ["Москва", "Московская область"], ["Москва", "МО"])
    assert same("regions", ["весь мир"], ["Worldwide"])
    assert not same("regions", ["Москва", "Московская область"], ["Москва"])


def test_product_ignores_word_forms_and_size_separators():
    assert same("product", "Коробки 40x30x30", "коробка 40×30×30 см")
    assert same("product", "Коробки 60x40x40", "Коробки 60х40х40")  # «х» — кириллица
    assert same("product", "Лист оцинкованный 1 мм", "Лист оцинкованный 1мм")
    assert same("product", "Стрейч-плёнка 500 мм", "стрейч-пленка 500 мм")


def test_product_numbers_must_match_exactly():
    assert not same("product", "Плёнка ПВД 120 мкм", "Плёнка ПВД 150 мкм")
    assert not same("product", "Коробки 40x30x30", "Коробки 30x40x30")
    assert not same("product", "Коробки 40x30x30", "Коробки")


def test_items_are_paired_by_content_not_order():
    assert pair_items([TAPE, FILM], [FILM, TAPE]) == [(TAPE, TAPE), (FILM, FILM)]


def test_missing_item_is_paired_with_empty():
    assert pair_items([TAPE, FILM], [FILM]) == [(TAPE, {}), (FILM, FILM)]


def test_exact_offer_is_fully_correct():
    score = score_reply(offer(BOXES), offer(dict(BOXES, price=Decimal("0.420"))))
    assert score.fields_ok
    assert score.notes_ok
    assert score.review_ok
    assert score.errors == ()


def test_wrong_field_is_reported():
    score = score_reply(offer(BOXES), offer(dict(BOXES, price="0.39")))
    assert [c.field for c in score.comparisons if not c.ok] == ["price"]
    assert score.errors == ("«Коробки 40x30x30», price: ожидали 0.42, модель — 0.39",)


def test_missed_item_is_wrong_only_where_label_has_value():
    """Kestrel: позицию не вернули — ошибка только в товаре, цену модель не выдумала."""
    notes = ("цена во вложении",)
    score = score_reply(offer(CONTAINERS, notes=notes), offer(notes=notes))
    assert [c.field for c in score.comparisons if not c.ok] == ["product"]
    assert score.errors == ("пропущена позиция «Пищевые контейнеры»",)


def test_invented_item_on_refusal_is_an_error():
    score = score_reply(REFUSAL, offer(BOXES))
    assert not score.type_ok
    assert not any(c.ok for c in score.comparisons)
    assert score.errors == ("тип: ожидали refusal, модель — offer", "лишняя позиция «Коробки 40x30x30»")


def test_notes_are_scored_by_presence():
    assert score_reply(offer(BOXES, notes=("цена 0.40–0.45",)), offer(BOXES, notes=("диапазон цены",))).notes_ok
    score = score_reply(offer(BOXES, notes=("цена 0.40–0.45",)), offer(BOXES))
    assert not score.notes_ok
    assert score.errors == ("нет пометки, ожидали: цена 0.40–0.45",)


def test_review_reasons_are_reported():
    """У верного ответа review пустой: любая причина — в разбор ошибок."""
    score = score_reply(offer(BOXES), offer(dict(BOXES, price=None), review=("цены 0.24 нет в тексте",)))
    assert not score.review_ok
    assert score.errors == (
        "«Коробки 40x30x30», price: ожидали 0.42, модель — пусто",
        "проверка: цены 0.24 нет в тексте",
    )


def test_summary_counts_types_and_fields():
    summary = summarize([
        score_reply(offer(BOXES), offer(BOXES)),
        score_reply(offer(BOXES), offer(dict(BOXES, price="0.39", lead_time_days=None))),
        score_reply(REFUSAL, dict(REFUSAL, type="other")),
    ])
    assert summary.replies == 3
    assert summary.types == Ratio(2, 3)
    assert summary.all_fields == Ratio(1, 3)
    assert summary.notes == Ratio(3, 3)
    assert summary.review == Ratio(3, 3)
    assert summary.confusion == Counter({("offer", "offer"): 2, ("refusal", "other"): 1})
    assert summary.fields["price"] == Ratio(1, 2)
    assert summary.filled["lead_time_days"] == Ratio(1, 2)
    assert summary.empty["product"] == Ratio(0, 0)


def test_empty_label_fields_catch_invented_values():
    delta = dict(BOXES, lead_time_days=None, regions=[])
    summary = summarize([score_reply(offer(delta), offer(dict(delta, lead_time_days=14)))])
    assert summary.empty["lead_time_days"] == Ratio(0, 1)
    assert summary.empty["regions"] == Ratio(1, 1)


def test_ratio_text():
    assert str(Ratio(14, 15)) == "14/15 (93%)"
    assert str(Ratio(0, 0)) == "—"
