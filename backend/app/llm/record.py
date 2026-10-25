"""Запись ответов модели для тестов: python -m app.llm.record <папка с cases.json>.

Из Docker, с LLM_API_KEY в .env:
docker compose run --rm --no-deps --build -v ./backend/tests:/app/tests app \
    python -m app.llm.record tests/fixtures/llm

Каждый случай из cases.json уходит в модель, ответ (и повтор, если ответ был не по схеме)
сохраняется в `<хеш запроса>.json`. Записи с другими хешами удаляются: после правки
промпта, схемы или модели старые ответы не копятся.
"""

import json
import re
import sys
from pathlib import Path

from app.config import settings
from app.llm import Reply, analyze
from app.llm.client import ApiClient, RecordingClient

RECORDING = re.compile(r"[0-9a-f]{64}\.json")


def load_cases(directory: Path) -> dict[str, Reply]:
    """Случаи из cases.json по имени: текст ответа, вложения, поставщики чата."""
    cases = json.loads((directory / "cases.json").read_text(encoding="utf-8"))
    return {
        case["name"]: Reply(
            case["text"], tuple(case.get("attachments", ())), tuple(case.get("candidates", ()))
        )
        for case in cases
    }


def main(directory: Path) -> None:
    client = RecordingClient(ApiClient.from_settings(), directory)
    for name, reply in load_cases(directory).items():
        analysis = analyze(reply, client, settings.llm_model)
        print(f"{name}: {analysis.type}, позиций {len(analysis.items)}, проверить: {analysis.review}")
    for path in directory.iterdir():
        if RECORDING.fullmatch(path.name) and path not in client.written:
            path.unlink()
            print(f"удалена старая запись {path.name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
