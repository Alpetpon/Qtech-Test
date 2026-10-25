from pathlib import Path

from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    data_dir: Path = Path("/app/data")

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
