from datetime import UTC, datetime, timedelta

from app.domain.statuses import REPLIED, SENT, SILENT, supplier_status

SENT_AT = datetime(2026, 11, 1, 10, tzinfo=UTC)


def after(hours, seconds=0):
    return SENT_AT + timedelta(hours=hours, seconds=seconds)


def test_silent_after_72_hours_without_reply():
    assert supplier_status(SENT_AT, after(72, -1), []) == SENT
    assert supplier_status(SENT_AT, after(72), []) == SILENT
    # Окно ответа закрылось, а ответа так и нет — все еще молчит
    assert supplier_status(SENT_AT, after(400), []) == SILENT


def test_reply_before_72_hours():
    # s1 NordPack ответил через 2 часа
    replies = [after(2)]

    assert supplier_status(SENT_AT, after(2), replies) == REPLIED
    assert supplier_status(SENT_AT, after(72), replies) == REPLIED


def test_late_reply_lifts_silence():
    # s4 Kestrel Foods EU отказал через 110 часов
    replies = [after(110)]

    assert supplier_status(SENT_AT, after(71), replies) == SENT
    assert supplier_status(SENT_AT, after(72), replies) == SILENT
    assert supplier_status(SENT_AT, after(110, -1), replies) == SILENT
    assert supplier_status(SENT_AT, after(110), replies) == REPLIED


def test_reply_at_moment_of_send():
    assert supplier_status(SENT_AT, SENT_AT, [SENT_AT]) == REPLIED


def test_threshold_is_parameter():
    assert supplier_status(SENT_AT, after(1), [], silence=timedelta(hours=1)) == SILENT
    assert supplier_status(SENT_AT, after(1, -1), [], silence=timedelta(hours=1)) == SENT
