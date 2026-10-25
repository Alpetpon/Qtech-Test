from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    status: str
    manager: str | None
    price_updated_at: datetime | None
    last_activity_at: datetime | None


@dataclass(frozen=True)
class Chat:
    id: str
    title: str
    supplier_ids: tuple[str, ...]
    enabled: bool
    member: bool
    last_outbound_at: datetime | None
