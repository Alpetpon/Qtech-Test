"""Схема OpenAPI бэкенда в файл, из неё фронт генерирует типы: npm run gen:api.

Запуск из backend/: uv run python -m scripts.export_openapi <файл>
Сервер и база не нужны, приложение только импортируется.
"""

import json
import os
import sys
from pathlib import Path

# Settings требует переменных базы. Движок SQLAlchemy подключается
# только при первом запросе, поэтому для импорта хватает заглушек
for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST"):
    os.environ.setdefault(name, "unused")

from app.main import app

Path(sys.argv[1]).write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
