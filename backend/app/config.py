from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, Field, SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    data_dir: Path = Path("/app/data")
    clock_start: AwareDatetime = datetime(2026, 11, 1, 10, tzinfo=UTC)
    rate_limit_pause: timedelta = timedelta(minutes=15)
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

    # LLM: OpenAI-совместимый API, по умолчанию OpenRouter. Без ключа приложение и тесты работают
    llm_api_key: SecretStr | None = None
    llm_model: str = "anthropic/claude-sonnet-5.5"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    prompts_dir: Path = Path("/app/prompts")


settings = Settings()
