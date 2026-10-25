"""Отказы сервисов как HTTP-ответы. Тело у любого отказа одно: {"detail": [причины для человека]}."""

from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.campaigns import Blocked
from app.services.offers import AlreadyApplied, NotFound, Refused


def install(app: FastAPI) -> None:
    # Обработчик ищется по классу исключения и его предкам: NotFound и AlreadyApplied — тоже Refused
    app.add_exception_handler(Blocked, lambda request, error: problem(422, error.draft.problems))
    app.add_exception_handler(NotFound, lambda request, error: problem(404, error.reasons))
    app.add_exception_handler(AlreadyApplied, lambda request, error: problem(409, error.reasons))
    app.add_exception_handler(Refused, lambda request, error: problem(422, error.reasons))
    app.add_exception_handler(RequestValidationError, invalid)


def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
    """Неверный запрос — в том же виде, что и отказ: фронтенду не нужен второй формат ошибок."""
    return problem(422, [describe(item) for item in error.errors()])


def describe(error: dict[str, Any]) -> str:
    # loc начинается с места в запросе: body, path или query
    field = ".".join(str(part) for part in error["loc"][1:])
    return f"{field}: {error['msg']}" if field else error["msg"]


def problem(status_code: int, reasons: Iterable[str]) -> JSONResponse:
    return JSONResponse({"detail": list(reasons)}, status_code=status_code)
