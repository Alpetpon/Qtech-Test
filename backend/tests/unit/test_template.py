from dataclasses import replace

import pytest

from app.domain.entities import Supplier
from app.domain.template import check, render

TEMPLATE = "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс.\nС уважением, {manager}"


def supplier(id, name, manager):
    return Supplier(id, name, "active", manager, price_updated_at=None, last_activity_at=None)


# Как в data/ и data/extra/; статус и даты шаблону не нужны
NORDPACK = supplier("s1", "NordPack", "Анна")
ORBIS = supplier("s2", "Orbis Trade", None)
KESTREL = supplier("s3", "Kestrel Foods", "Игорь")
KESTREL_EU = supplier("s4", "Kestrel Foods EU", "Игорь")
POLAR = supplier("s19", "Polar Film", "  ")


def test_nordpack():
    assert check(TEMPLATE, [NORDPACK]) == []
    assert render(TEMPLATE, [NORDPACK]) == (
        "Здравствуйте, NordPack! Пришлите, пожалуйста, актуальный прайс.\nС уважением, Анна"
    )


def test_shared_chat_gets_all_names_and_one_manager():
    assert check(TEMPLATE, [KESTREL, KESTREL_EU]) == []
    assert render(TEMPLATE, [KESTREL, KESTREL_EU]) == (
        "Здравствуйте, Kestrel Foods, Kestrel Foods EU! Пришлите, пожалуйста, актуальный прайс.\n"
        "С уважением, Игорь"
    )


@pytest.mark.parametrize("blank", [ORBIS, POLAR, replace(NORDPACK, manager="")], ids=["null", "spaces", "empty"])
def test_blank_manager_blocks(blank):
    reason = f"У {blank.name} пустой менеджер"

    assert check(TEMPLATE, [blank]) == [reason]
    with pytest.raises(ValueError, match=reason):
        render(TEMPLATE, [blank])


def test_blank_supplier_name_blocks():
    assert check(TEMPLATE, [KESTREL, replace(KESTREL_EU, name=" ")]) == ["У поставщика s4 пустое название"]


def test_different_managers_in_one_chat_block():
    assert check(TEMPLATE, [KESTREL, replace(KESTREL_EU, manager="Анна")]) == [
        "У поставщиков одного чата разные менеджеры: Kestrel Foods — Игорь, Kestrel Foods EU — Анна. "
        "В {manager} подставляется один"
    ]


@pytest.mark.parametrize("typo", ["{suplier}", "{Supplier}", "{ supplier }", "{поставщик}"])
def test_unknown_substitution_blocks(typo):
    template = TEMPLATE.replace("{supplier}", typo)

    assert check(template, [NORDPACK]) == [
        "Неизвестная подстановка " + typo + ": есть только {supplier} и {manager}"
    ]


@pytest.mark.parametrize(("typo", "brace"), [("{supplier", "{"), ("supplier}", "}")])
def test_stray_brace_blocks(typo, brace):
    template = TEMPLATE.replace("{supplier}", typo)

    assert check(template, [NORDPACK]) == [
        "Лишняя скобка «" + brace + "»: фигурные скобки — только для {supplier} и {manager}"
    ]


@pytest.mark.parametrize("template", ["", "  \n "])
def test_blank_template_blocks(template):
    assert check(template, [NORDPACK]) == ["Шаблон пустой"]


def test_all_reasons_are_shown():
    template = "Здравствуйте, {suplier}! С уважением, {manager}. {suplier}"

    assert check(template, [ORBIS]) == [
        "Неизвестная подстановка {suplier}: есть только {supplier} и {manager}",
        "У Orbis Trade пустой менеджер",
    ]


def test_blank_value_of_unused_substitution_does_not_block():
    assert render("Здравствуйте, {supplier}!", [ORBIS]) == "Здравствуйте, Orbis Trade!"
