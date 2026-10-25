from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import settings
from app.domain.entities import Chat, Supplier
from app.seed import STATUSES, parse_time, read

NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)


def load_entities(*dirs: Path) -> tuple[list[Supplier], list[Chat]]:
    suppliers = [
        Supplier(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            manager=row["manager"],
            price_updated_at=parse_time(row["price_updated_at"]),
            last_activity_at=parse_time(row["last_activity_at"]),
        )
        for row in read(dirs, "suppliers.json")
        if row["status"] in STATUSES
    ]
    known = {supplier.id for supplier in suppliers}
    chats = [
        Chat(
            id=row["id"],
            title=row["title"],
            supplier_ids=tuple(i for i in row["supplier_ids"] if i in known),
            enabled=row["enabled"],
            member=row["member"],
            last_outbound_at=parse_time(row["last_outbound_at"]),
        )
        for row in read(dirs, "chats.json")
    ]
    return suppliers, chats


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def task_data() -> tuple[list[Supplier], list[Chat]]:
    return load_entities(settings.data_dir)


@pytest.fixture
def full_data() -> tuple[list[Supplier], list[Chat]]:
    return load_entities(settings.data_dir, settings.data_dir / "extra")
