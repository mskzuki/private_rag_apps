.PHONY: setup migrate demo ingest ingest-gdrive test lint fmt eval eval-no-cache eval-routing eval-all api web openapi

api:
	docker compose up --build api

web:
	cd frontend && pnpm dev

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

# Google Driveフォルダ（.envのDRIVE_FOLDER_ID）を取り込む。CLI経由は呼び出しプロセス内で完結するため
# Redis・make workerの起動は不要（API経由トリガのみARQ/Redisを使う。m9_google_drive_ingestion.md §4.8）
# FORCE_DELETE=1 で削除安全弁をバイパスできる
ingest-gdrive:
	cd backend && uv run python -m private_rag_apps.cli.main ingest-gdrive --trigger cli

# rag_dev(開発/デモ用DB)を巻き込まないよう、テストは常に別DB rag_test に対して実行する（docs/architecture.md §9）
test:
	cd backend && DATABASE_URL="postgresql+psycopg://rag_user:rag_pass@localhost:5432/rag_test" uv run pytest

lint:
	cd backend && uv run ruff check . && uv run mypy .
	cd frontend && pnpm lint && pnpm fmt:check

fmt:
	cd backend && uv run ruff format .
	cd frontend && pnpm fmt

# 評価データセットでEvalハーネスを実行。合否ではなくスコア回帰を監視する（テストとは別物）
eval:
	cd backend && uv run python -m private_rag_apps.evals $(ARGS)

# Voyage APIを呼んで評価用の検索結果キャッシュを更新する。
eval-no-cache:
	cd backend && uv run python -m private_rag_apps.evals --no-cache

# M7: routing evalデータセットでrewrite→retrieve→gradeを評価する（generateは実行しない。高速）
eval-routing:
	cd backend && uv run python -m private_rag_apps.evals.routing

# M7: eval と eval-routing の両方を実行する
eval-all: eval eval-routing

# FastAPIアプリからOpenAPIスキーマを生成する。API起動せず定義から直接出力
openapi:
	cd backend && uv run python -c "import json; from private_rag_apps.api.main import app; json.dump(app.openapi(), open('openapi.json', 'w'), ensure_ascii=False, indent=2)"
