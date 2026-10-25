import pytest

from app.domain.preview import Totals, build_preview
from app.domain.selection import inactive_new, stale_price


def run(preset, data, now):
    suppliers, chats = data
    return build_preview([item.supplier for item in preset(suppliers, now)], chats, now)


def sent(preview):
    return [item.chat.id for item in preview.outgoing]


@pytest.mark.parametrize(("preset", "chats_sent"), [(stale_price, ["c1"]), (inactive_new, ["c3"])], ids=["active", "new"])
def test_task_data(task_data, now, preset, chats_sent):
    assert sent(run(preset, task_data, now)) == chats_sent


@pytest.mark.parametrize(
    ("preset", "chats_sent", "excluded", "totals"),
    [
        (
            stale_price,
            ["c1", "c7", "c9", "c12", "c14", "c18", "c19"],
            ["s2", "s5", "s7", "s14", "s18"],
            Totals(selected=12, chats=7, excluded=5),
        ),
        (inactive_new, ["c3", "c11", "c15"], ["s6"], Totals(selected=5, chats=3, excluded=1)),
    ],
    ids=["active", "new"],
)
def test_full_data_matches_readme(full_data, now, preset, chats_sent, excluded, totals):
    preview = run(preset, full_data, now)

    assert sent(preview) == chats_sent
    assert [item.supplier.id for item in preview.excluded] == excluded
    assert preview.totals == totals
