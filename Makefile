.PHONY: install db migrate seed run test lint typecheck rag-evals rag-evals-live evals check

install:
	uv sync

db:
	docker compose up -d db redis

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

# RAG evals (spec 08). Offline: fake embeddings + extractive LLM, no key needed.
rag-evals:
	uv run python -m app.rag.evals.run --offline

# Full mode: real embeddings, LLM and judges (needs OPENAI_API_KEY).
rag-evals-live:
	uv run python -m app.rag.evals.run

# Agent evals are added in spec 13.
evals: rag-evals

check: lint typecheck test evals
