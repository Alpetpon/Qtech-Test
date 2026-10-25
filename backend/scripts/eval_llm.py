"""Точность разбора ответов поставщиков на размеченных ответах.

Разметка — eval/labeled_replies.json, записанные ответы модели — eval/recordings/,
итог — eval/results.md. Папки eval/ в образе нет, её монтируем. Сервис app — потому что у tests нет сети.

По записям, без сети:
    docker compose run --rm --no-deps --build -v ./eval:/app/eval app python -m scripts.eval_llm
Спросить настоящую модель и перезаписать записи (нужен LLM_API_KEY в .env):
    docker compose run --rm --no-deps --build -v ./eval:/app/eval app python -m scripts.eval_llm --record
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.config import settings
from app.eval_metrics import FIELDS, ReplyScore, score_reply, show, summarize
from app.llm import Reply, analyze
from app.llm.client import ApiClient, MissingRecording, RecordingClient, ReplayClient
from app.llm.record import RECORDING

TYPES = ("offer", "refusal", "other")
FIELD_NAMES = {
    "product": "товар",
    "price": "цена",
    "currency": "валюта",
    "moq": "мин. партия",
    "lead_time_days": "срок, дней",
    "regions": "регионы",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", action="store_true", help="спросить настоящую модель и перезаписать записи")
    parser.add_argument("--eval-dir", type=Path, default=Path("eval"))
    args = parser.parse_args()

    labeled = json.loads((args.eval_dir / "labeled_replies.json").read_text())
    recordings = args.eval_dir / "recordings"
    if args.record:
        recordings.mkdir(exist_ok=True)
        client = RecordingClient(ApiClient.from_settings(), recordings)
    else:
        client = ReplayClient(recordings)

    scores = []
    for reply in labeled:
        try:
            analysis = analyze(to_reply(reply), client, settings.llm_model)
        except MissingRecording:
            raise SystemExit(
                f"Нет записанного ответа модели на «{reply['id']}»: ответы ещё не записаны "
                "или с тех пор менялись промпт, модель или текст. Запишите их с --record"
            ) from None
        scores.append(score_reply(reply["expected"], asdict(analysis)))

    if args.record:
        # Записи на старый промпт или модель не копятся
        for path in recordings.iterdir():
            if RECORDING.fullmatch(path.name) and path not in client.written:
                path.unlink()
    (args.eval_dir / "results.md").write_text(render(labeled, scores, settings.llm_model))
    print(f"Тип ответа: {summarize(scores).types}. Итог — {args.eval_dir / 'results.md'}")


def to_reply(reply: dict) -> Reply:
    """Как в сервисе: сообщения одного ответа — через перевод строки, вложения — отдельно."""
    messages = reply["messages"]
    return Reply(
        "\n".join(message["text"] for message in messages),
        tuple(message["attachment"] for message in messages if "attachment" in message),
    )


def render(labeled: list[dict], scores: list[ReplyScore], model: str) -> str:
    summary = summarize(scores)
    loose = [
        f"- {reply['id']}, {FIELD_NAMES[c.field]}: разметка «{show(c.expected)}», модель «{show(c.actual)}»"
        for reply, score in zip(labeled, scores)
        for c in score.comparisons
        if c.ok and c.field in ("product", "regions") and c.filled and c.expected != c.actual
    ]
    lines = [
        "# Точность разбора ответов поставщиков",
        "",
        f"Модель `{model}`, промпт `prompts/reply_analysis.md`.",
        f"{summary.replies} размеченных ответов — `eval/labeled_replies.json`, записанные ответы модели — `eval/recordings/`.",
        "Пересчёт по записям без сети: `python -m scripts.eval_llm` (команда целиком — в `backend/scripts/eval_llm.py`).",
        "",
        "## Итог",
        "",
        "| Что считаем | Верно |",
        "|---|---|",
        f"| Тип ответа | {summary.types} |",
        f"| Тип и все поля всех позиций | {summary.all_fields} |",
        f"| Пометки модели — есть там, где нужны, и нет, где не нужны | {summary.notes} |",
        f"| Проверка ничего не отклонила (у верного ответа так и должно быть) | {summary.review} |",
        "",
        "## Тип ответа",
        "",
        "Строки — разметка, столбцы — модель.",
        "",
        "| | " + " | ".join(TYPES) + " |",
        "|---|" + "---|" * len(TYPES),
        *(
            f"| **{expected}** | " + " | ".join(str(summary.confusion[expected, actual]) for actual in TYPES) + " |"
            for expected in TYPES
        ),
        "",
        "## Поля позиций",
        "",
        "Позиции разметки и модели сопоставляем так, чтобы совпало больше всего полей.",
        "Пропущенную позицию сравниваем с пустой, лишнюю — тоже. Пустое поле верно, только если и в разметке пусто:",
        "последний столбец показывает, что модель не выдумала значение.",
        "",
        "| Поле | Всего | Значение есть в разметке | Значения нет в разметке |",
        "|---|---|---|---|",
        *(
            f"| {FIELD_NAMES[f]} | {summary.fields[f]} | {summary.filled[f]} | {summary.empty[f]} |"
            for f in FIELDS
        ),
        "",
        "Как сравниваем: цена — как число (0.40 = 0.4); валюта — без учёта регистра; партия и срок — точно;",
        "регионы — набором, без учёта порядка и регистра, «ЕС» = «EU», «МО» = «Московская область»;",
        "товар — все слова из разметки есть у модели, словоформа не важна (коробки = коробка),",
        "числа и размеры — точно, «х» и «×» в размерах = «x».",
        "",
        "Засчитано по этим правилам, хотя дословно не совпало:",
        "",
        *(loose or ["- таких нет"]),
        "",
        "## Разбор ошибок",
        "",
    ]
    wrong = [(reply, score) for reply, score in zip(labeled, scores) if score.errors]
    if not wrong:
        lines += ["Ошибок нет.", ""]
    for reply, score in wrong:
        lines += [
            f"### {reply['id']}",
            "",
            "\n>\n".join(quote(message) for message in reply["messages"]),
            "",
            *(f"- {error}" for error in score.errors),
            "",
            f"Разметка: {reply['why']}.",
            "",
        ]
    return "\n".join(lines)


def quote(message: dict) -> str:
    attachment = f" *(вложение: {message['attachment']})*" if "attachment" in message else ""
    return f"> {message['text']}{attachment}"


if __name__ == "__main__":
    main()
