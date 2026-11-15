.PHONY: install db migrate seed run test lint typecheck evals check

install:
	uv sync

db:
	docker compose up -d db

migrate:
	uv run alembic upgrade head

seed:
	uv run seed

run:
	uv run uvicorn app.main:app --reload --app-dir src

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

evals:
	@echo "evals: placeholder (filled in specs 08/13)"

check: lint typecheck test evals
