.PHONY: setup migrate demo ingest test lint fmt eval api openapi

api:
	cd backend && uv run uvicorn private_rag_apps.api.main:app --reload

# 初期セットアップ: uv sync + pnpm install + .env生成 + DB起動（AGENTS.md §4/§5）
setup:
	cd backend && uv sync
	cd frontend && pnpm install
	[ -f backend/.env ] || cp backend/.env.example backend/.env
	docker compose up -d db

migrate:
	cd backend && uv run alembic upgrade head

# 任意のコーパス（.envのCORPUS_DIR）を取り込む。個別に文書を追加/更新した時に使う
# FORCE_DELETE=1 で削除安全弁をバイパスできる
ingest:
	cd backend && uv run python -m private_rag_apps.cli.main ingest --trigger cli

# クリーン環境からのオンボーディング用。migrate → ingest(trigger=demo, seedコーパス固定) をまとめて叩ける1コマンド
# .envのCORPUS_DIRが個人の文書ディレクトリに差し替えられていても、デモは常にseed/corpusを使う
demo: migrate
	cd backend && CORPUS_DIR=seed/corpus uv run python -m private_rag_apps.cli.main ingest --trigger demo

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
