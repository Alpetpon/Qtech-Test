import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import settings
from app.models import Chat, OurAccount, Supplier, SupplierContact, chat_suppliers
from app.seed import load, parse_time

DATA = settings.data_dir
EXTRA = DATA / "extra"

FULL = {"suppliers": 21, "chats": 18, "links": 20, "accounts": 3, "contacts": 12}


def counts(session):
    tables = {
        "suppliers": Supplier,
        "chats": Chat,
        "links": chat_suppliers,
        "accounts": OurAccount,
        "contacts": SupplierContact,
    }
    return {name: session.scalar(select(func.count()).select_from(table)) for name, table in tables.items()}


def linked(session, chat_id):
    return {supplier.id for supplier in session.get(Chat, chat_id).suppliers}


def test_task_data(session):
    load(session, DATA)

    assert counts(session) == {"suppliers": 6, "chats": 4, "links": 5, "accounts": 3, "contacts": 3}
    assert linked(session, "c3") == {"s3", "s4"}
    assert session.get(Supplier, "s1").price_updated_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert session.get(Supplier, "s2").manager is None
    assert session.get(Supplier, "s3").price_updated_at is None
    assert session.get(Chat, "c5").last_outbound_at is None


def test_full_data_skips_bad_records_with_warning(session, tmp_path, caplog):
    bad_suppliers = [
        {"id": "s50", "name": "Old Corp", "status": "archived", "manager": "Анна",
         "price_updated_at": None, "last_activity_at": None},
        {"id": "s51", "name": "Broken Date", "status": "active", "manager": "Анна",
         "price_updated_at": "2026-13-01", "last_activity_at": None},
        {"id": "s52", "name": "No Manager Field", "status": "active",
         "price_updated_at": None, "last_activity_at": None},
    ]
    (tmp_path / "suppliers.json").write_text(json.dumps(bad_suppliers))
    (tmp_path / "supplier_contacts.json").write_text(json.dumps([{"handle": "@old_corp", "supplier_id": "s50"}]))

    load(session, DATA, EXTRA, tmp_path)

    assert counts(session) == FULL
    assert linked(session, "c15") == {"s17"}
    warnings = caplog.messages
    assert len(warnings) == 5
    assert "s50" in warnings[0] and "archived" in warnings[0]
    assert "s51" in warnings[1] and "неверное значение" in warnings[1]
    assert "s52" in warnings[2] and "нет поля 'manager'" in warnings[2]
    assert "c15" in warnings[3] and "s99" in warnings[3]
    assert "@old_corp" in warnings[4] and "s50" in warnings[4]


def test_reload_adds_nothing_and_keeps_changes(session):
    load(session, DATA, EXTRA)
    sent = datetime(2026, 11, 1, 10, 5, tzinfo=UTC)
    session.get(Chat, "c1").last_outbound_at = sent
    session.get(Supplier, "s1").price_updated_at = sent
    session.flush()

    load(session, DATA, EXTRA)
    session.expire_all()

    assert counts(session) == FULL
    assert session.get(Chat, "c1").last_outbound_at == sent
    assert session.get(Supplier, "s1").price_updated_at == sent


def test_date_without_time_is_utc_midnight():
    assert parse_time("2026-07-10") == datetime(2026, 7, 10, tzinfo=UTC)
