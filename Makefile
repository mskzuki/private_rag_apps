.PHONY: setup migrate demo ingest test lint fmt eval api openapi

api:
	cd backend && uv run uvicorn private_rag_apps.api.main:app --reload

setup:
	cd backend && uv sync

migrate:
	cd backend && uv run alembic upgrade head

# 任意のコーパス（.envのCORPUS_DIR）を取り込む。個別に文書を追加/更新した時に使う
ingest:
	cd backend && uv run python -m private_rag_apps.cli.main ingest

# クリーン環境からのオンボーディング用。migrate → ingest をまとめて叩ける1コマンド
demo: migrate ingest

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run mypy .
	cd frontend && pnpm lint && pnpm fmt:check

fmt:
	cd backend && uv run ruff format .
	cd frontend && pnpm fmt

# ゴールデンデータセットでEvalハーネスを実行。合否ではなくスコア回帰を監視する（テストとは別物）
eval:
	cd backend && uv run python -m private_rag_apps.evals

# FastAPIアプリからOpenAPIスキーマを生成する。API起動せず定義から直接出力
openapi:
	cd backend && uv run python -c "import json; from private_rag_apps.api.main import app; json.dump(app.openapi(), open('openapi.json', 'w'), ensure_ascii=False, indent=2)"
