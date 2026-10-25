from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api import errors, routes
from app.config import settings
from app.db import engine

app = FastAPI(title="Qtech")
app.include_router(routes.router)
errors.install(app)


class Health(BaseModel):
    status: str
    db: str


@app.get("/api/health")
def health() -> Health:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return Health(status="ok", db="ok")


class FrontendFiles(StaticFiles):
    """Собранный фронт: путь, которого нет среди файлов, получает index.html.

    Так прямой заход на адрес роутера (/campaigns/1) открывает приложение.
    Неизвестный /api/... остаётся 404 JSON, а не страницей.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


# В тестовом образе фронта нет, там приложение работает только как API.
# Подключается последним: Mount("/") забирает всё, что не совпало с маршрутами выше
if settings.frontend_dir.is_dir():
    app.mount("/", FrontendFiles(directory=settings.frontend_dir))
