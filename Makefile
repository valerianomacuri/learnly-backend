.PHONY: install db up down migrate seed run test cov lint typecheck rag-evals rag-evals-live agent-evals agent-evals-live evals demo check

install:
	uv sync

# Only the data stores, for running the API from source.
db:
	docker compose up -d db redis

# The whole stack (db, redis, init job, api, worker, frontend) with seed data.
up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run seed

run:
	uv run uvicorn app.main:app --reload --app-dir src

test:
	uv run pytest

# Domain coverage gate used by CI (spec 13).
cov:
	uv run pytest --cov=app.domain --cov-report=term-missing --cov-fail-under=85

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

# Agent evals (spec 13). --fake: scripted LLM + fake embeddings, no key needed.
agent-evals:
	uv run python -m app.agent.evals.run --fake

# Real model and embeddings (needs OPENAI_API_KEY).
agent-evals-live:
	uv run python -m app.agent.evals.run

evals: rag-evals agent-evals

# 3-minute demo: bring everything up, then follow docs/demo.md.
demo: up
	@echo "App:     http://localhost:5173   API docs: http://localhost:8000/docs"
	@echo "Guide:   docs/demo.md   (users and password: docker compose logs init)"

check: lint typecheck test evals
