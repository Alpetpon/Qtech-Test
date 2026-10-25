FROM node:24-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./

FROM base AS runtime
RUN uv sync --locked --no-dev
COPY --from=frontend /frontend/dist ./frontend/
COPY backend/ ./
COPY data/ ./data/
COPY prompts/ ./prompts/

FROM base AS test
RUN uv sync --locked
COPY backend/ ./
COPY data/ ./data/
COPY prompts/ ./prompts/
