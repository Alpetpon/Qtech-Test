from fastapi import FastAPI
from sqlalchemy import text

from app.api import errors, routes
from app.db import engine

app = FastAPI(title="Qtech")
app.include_router(routes.router)
errors.install(app)


@app.get("/api/health")
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
