FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --system --create-home appuser
COPY --from=builder --chown=appuser:appuser /app /app
USER appuser
EXPOSE 8000
# Migrations are run explicitly: docker compose run --rm api alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
