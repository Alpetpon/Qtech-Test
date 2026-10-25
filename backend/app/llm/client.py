import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx2
import openai

from app.config import settings

MAX_TOKENS = 4096
TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Request:
    model: str
    messages: list[dict[str, str]]
    schema: dict[str, Any]

    def key(self) -> str:
        """Хеш запроса целиком (модель, промпт, текст, схема): по нему ищется записанный ответ."""
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class LLMClient(Protocol):
    def complete(self, request: Request) -> str:
        """Сырой текст ответа модели."""
        ...


class LLMError(RuntimeError):
    """Модель недоступна: нет ключа, сеть, ошибка API."""


class MissingRecording(LLMError):
    """У клиента на записях нет ответа на этот запрос."""


class ApiClient:
    """Модель через OpenAI-совместимый API; по умолчанию OpenRouter, модель — LLM_MODEL."""

    def __init__(self, api_key: str, base_url: str, http_client: httpx2.Client | None = None):
        self._client = openai.OpenAI(
            api_key=api_key, base_url=base_url, timeout=TIMEOUT_SECONDS, http_client=http_client
        )

    @classmethod
    def from_settings(cls) -> "ApiClient":
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
        if not api_key:
            raise LLMError("LLM_API_KEY не задан: положите ключ в .env, образец — .env.example")
        return cls(api_key, settings.llm_base_url)

    def complete(self, request: Request) -> str:
        try:
            response = self._client.chat.completions.create(
                model=request.model,
                messages=request.messages,
                max_tokens=MAX_TOKENS,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "reply_analysis", "strict": True, "schema": request.schema},
                },
            )
        except openai.OpenAIError as error:
            raise LLMError(f"Модель недоступна: {error}") from error
        if not response.choices:
            raise LLMError("Модель вернула пустой ответ")
        return response.choices[0].message.content or ""


class ReplayClient:
    """Ответы модели, записанные заранее в `<хеш запроса>.json`. В сеть не ходит."""

    def __init__(self, directory: Path):
        self.directory = directory

    def complete(self, request: Request) -> str:
        path = self.directory / f"{request.key()}.json"
        if not path.exists():
            raise MissingRecording(
                f"Нет записанного ответа модели {path.name} на запрос:\n"
                f"{request.messages[-1]['content']}\n"
                f"Запись привязана к модели, промпту и тексту — после их правки ответы записывают заново.\n"
                f"Новый случай добавьте в {self.directory / 'cases.json'} и запишите ответы "
                f"(нужен LLM_API_KEY в .env):\n"
                f"docker compose run --rm --no-deps --build -v ./backend/tests:/app/tests "
                f"app python -m app.llm.record {self.directory}"
            )
        return json.loads(path.read_text(encoding="utf-8"))["response"]


class RecordingClient:
    """Спрашивает модель и сохраняет каждый ответ для ReplayClient."""

    def __init__(self, inner: LLMClient, directory: Path):
        self.inner = inner
        self.directory = directory
        self.written: set[Path] = set()

    def complete(self, request: Request) -> str:
        response = self.inner.complete(request)
        record = {
            "source": "api",
            "model": request.model,
            # Без системного промпта: он общий, его версия и так входит в хеш
            "request": request.messages[1:],
            "response": response,
        }
        path = self.directory / f"{request.key()}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.written.add(path)
        return response
