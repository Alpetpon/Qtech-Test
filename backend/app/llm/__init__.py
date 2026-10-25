import logging

from app.llm.client import LLMClient, LLMError
from app.llm.prompt import build_request, retry_request
from app.llm.schema import Analysis, Item, Reply, ReplyType
from app.llm.validation import AnswerError, check, parse_answer

__all__ = ["Analysis", "Item", "LLMClient", "LLMError", "Reply", "ReplyType", "analyze"]

log = logging.getLogger(__name__)

# Сколько раз спрашиваем модель, если она вернула не JSON по схеме
ATTEMPTS = 2


def analyze(reply: Reply, client: LLMClient, model: str) -> Analysis:
    """Тип ответа и поля предложения; ответ модели проверяется по тексту поставщика.

    Ответ не по схеме — повтор с описанием ошибки; не вышло и со второго раза —
    `other` и «проверить вручную». Недоступность модели — LLMError, решает вызывающий.
    """
    request = build_request(reply, model)
    for attempt in range(1, ATTEMPTS + 1):
        raw = client.complete(request)
        try:
            return check(parse_answer(raw), reply)
        except AnswerError as error:
            log.warning("Ответ модели %s, попытка %d из %d: %s", model, attempt, ATTEMPTS, error)
            problem = str(error)
            request = retry_request(request, raw, problem)
    return Analysis(ReplyType.OTHER, review=[f"ответ модели не разобран: {problem}"])
