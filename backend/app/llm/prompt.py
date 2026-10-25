import re
from functools import cache

from app.config import settings
from app.llm.client import Request
from app.llm.schema import ANSWER_SCHEMA, Reply

PROMPT_FILE = "reply_analysis.md"
# Поставщик может написать «</reply>», а дальше — текст, похожий на наши инструкции
REPLY_TAG = re.compile(r"</?\s*reply\s*>", re.IGNORECASE)

RETRY_MESSAGE = (
    "Ответ не прошёл проверку: {error}\n"
    "Верни тот же разбор только JSON-объектом в формате из инструкции, без пояснений."
)


@cache
def system_prompt() -> str:
    return (settings.prompts_dir / PROMPT_FILE).read_text(encoding="utf-8")


def user_message(reply: Reply) -> str:
    lines = [f"Вложения: {', '.join(reply.attachments) or 'нет'}"]
    if reply.candidates:
        lines.append(f"Поставщики чата: {', '.join(reply.candidates)}")
    lines += ["<reply>", REPLY_TAG.sub("", reply.text), "</reply>"]
    return "\n".join(lines)


def build_request(reply: Reply, model: str) -> Request:
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_message(reply)},
    ]
    return Request(model, messages, ANSWER_SCHEMA)


def retry_request(request: Request, answer: str, error: str) -> Request:
    """Повтор после ответа не по схеме: модель видит свой ответ и что с ним не так."""
    messages = [
        *request.messages,
        {"role": "assistant", "content": answer},
        {"role": "user", "content": RETRY_MESSAGE.format(error=error)},
    ]
    return Request(request.model, messages, request.schema)
