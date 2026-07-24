.PHONY: setup migrate demo ingest ingest-gdrive worker test lint fmt eval eval-no-cache eval-routing eval-all api web openapi

api:
	-docker compose stop api
	docker compose up --build --no-deps api

web:
	cd frontend && pnpm dev

# API経由トリガ（POST /api/ingest/gdrive）のARQジョブを処理するworkerをapiと同じくコンテナで起動する
# （m9_google_drive_ingestion.md §4.8 v0.6。CLI経由取り込みはこのworkerに依存しない）
worker:
	docker compose up --build ingest_worker

# 初期セットアップ: uv sync + pnpm install + .env生成 + DB起動（AGENTS.md §4/§5）
setup:
	cd backend && uv sync
	cd frontend && pnpm install
	[ -f backend/.env ] || cp backend/.env.example backend/.env
	docker compose up -d db

# backend関連ターゲットはdocker compose run --rm --build経由でapiサービスのイメージ上で実行する
# （make api/make workerと同じくホストのuv/Python直接実行をしない方針に統一。docs/decisions.md参照）
migrate:
	docker compose run --rm --build api uv run alembic upgrade head

# 任意のコーパス（.envのCORPUS_DIR）を取り込む。個別に文書を追加/更新した時に使う
# FORCE_DELETE=1 で削除安全弁をバイパスできる
ingest:
	docker compose run --rm --build api uv run python -m private_rag_apps.cli.main ingest --trigger cli

# クリーン環境からのオンボーディング用。migrate → ingest(trigger=demo, seedコーパス固定) をまとめて叩ける1コマンド
# .envのCORPUS_DIRが個人の文書ディレクトリに差し替えられていても、デモは常にseed/corpusを使う
demo: migrate
	docker compose run --rm --build -e CORPUS_DIR=seed/corpus api uv run python -m private_rag_apps.cli.main ingest --trigger demo

# Google Driveフォルダ（.envのDRIVE_FOLDER_ID）を取り込む。CLI経由は呼び出しプロセス内で完結するため
# Redis・make workerの起動は不要（API経由トリガのみARQ/Redisを使う。m9_google_drive_ingestion.md §4.8）
# FORCE_DELETE=1 で削除安全弁をバイパスできる
ingest-gdrive:
	docker compose run --rm --build api uv run python -m private_rag_apps.cli.main ingest-gdrive --trigger cli

# rag_dev(開発/デモ用DB)を巻き込まないよう、テストは常に別DB rag_test に対して実行する（docs/architecture.md §9）
# DB_NAMEのみ上書きすればよい（DB_HOSTはdocker-compose.yml側のapiサービス定義でdbに固定済み）
test:
	docker compose run --rm --build -e DB_NAME=rag_test api uv run pytest

# lint/fmtはDB/Redisに繋がないため--no-depsで無駄な依存サービス起動を避ける
lint:
	docker compose run --rm --build --no-deps api sh -c "uv run ruff check . && uv run mypy ."
	cd frontend && pnpm lint && pnpm fmt:check

fmt:
	docker compose run --rm --build --no-deps api uv run ruff format .
	cd frontend && pnpm fmt

# 評価データセットでEvalハーネスを実行。合否ではなくスコア回帰を監視する（テストとは別物）
eval:
	docker compose run --rm --build api uv run python -m private_rag_apps.evals $(ARGS)

# Voyage APIを呼んで評価用の検索結果キャッシュを更新する。
eval-no-cache:
	docker compose run --rm --build api uv run python -m private_rag_apps.evals --no-cache

# M7: routing evalデータセットでrewrite→retrieve→gradeを評価する（generateは実行しない。高速）
eval-routing:
	docker compose run --rm --build api uv run python -m private_rag_apps.evals.routing

# M7: eval と eval-routing の両方を実行する
eval-all: eval eval-routing

# FastAPIアプリからOpenAPIスキーマを生成する。API起動せず定義から直接出力
openapi:
	cd backend && uv run python -c "import json; from private_rag_apps.api.main import app; json.dump(app.openapi(), open('openapi.json', 'w'), ensure_ascii=False, indent=2)"
