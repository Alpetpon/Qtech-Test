from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, Field
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    data_dir: Path = Path("/app/data")
    clock_start: AwareDatetime = datetime(2026, 11, 1, 10, tzinfo=UTC)
    # Пауза всех рассылок после RATE_LIMIT, потом очередь продолжается сама, env RATE_LIMIT_PAUSE=00:15:00
    rate_limit_pause: timedelta = timedelta(minutes=15)
    # Как часто заглушка отправителя отвечает RATE_LIMIT, от 0 до 1, env RATE_LIMIT_CHANCE
    rate_limit_chance: float = Field(default=0.05, ge=0, le=1)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            database=self.postgres_db,
        )


settings = Settings()
