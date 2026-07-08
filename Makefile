.PHONY: setup migrate demo ingest test lint fmt eval api openapi

api:
	cd backend && uv run uvicorn private_rag_apps.api.main:app --reload

setup:
	cd backend && uv sync

migrate:
	cd backend && uv run alembic upgrade head

ingest:
	cd backend && uv run python -m private_rag_apps.cli.main ingest

demo: migrate ingest

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run mypy .

fmt:
	cd backend && uv run ruff format .

eval:
	cd backend && uv run python -m private_rag_apps.evals

openapi:
	cd backend && uv run python -c "import json; from private_rag_apps.api.main import app; json.dump(app.openapi(), open('openapi.json', 'w'), ensure_ascii=False, indent=2)"
