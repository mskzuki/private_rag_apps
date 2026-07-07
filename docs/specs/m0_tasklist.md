# M0 Walking Skeleton タスクリスト

`docs/specs/m0_walking_skeleton.md` に基づくタスク一覧です。
各機能タスクには**対応するテストを同時に**含めます（AGENTS §8）。LLM・埋め込み・rerank 呼び出しはテストでモックします。

- `[x]` **M0-1 インフラ設定**
  - `[x]` `docker-compose.yml` (Postgres + pgvector + pg_bigm) の作成
  - `[x]` `.env.example` の作成
  - `[x]` `core/config.py` (pydantic-settings) の実装
  - *完了条件*: `docker compose up` で DB が起動し、設定が読み込めること。

- `[ ]` **M0-2 データベース・マイグレーション**
  - `[ ]` Alembic の初期化
  - `[ ]` 初期マイグレーション `0001_init` の作成
    - 拡張機能: `pgcrypto`, `vector`, `pg_bigm`（拡張の作成のみ。**全文検索用の GIN 索引 `chunks_content_bigm` は作らない → M1 の `0002` 担当**）
    - テーブル: `sources`, `chunks`, `ingest_runs`
    - 索引: `chunks.embedding` の **HNSW**（`vector_cosine_ops`, m=16, ef_construction=64）、および補助索引（`chunks.source_id`, `sources` の部分索引 `deleted_at IS NULL`, `ingest_runs.started_at`）
    - ※ `conversations` / `messages` は M0 では作らない（単発Q&A。M2 で追加）
  - *完了条件*: `make migrate` が成功し、DB 上にスキーマと HNSW 索引が構築されること。
  - *テスト*: マイグレーションの upgrade/downgrade が通ること。

- `[ ]` **M0-3 コーパス取り込み (Ingestion)**
  - `[ ]` `seed/corpus/` ディレクトリの作成と設計文書（requirements/architecture/db_design/AGENTS）の配置
  - `[ ]` `.md` ローダーの実装（タイトルは先頭 H1、なければファイル名）
  - `[ ]` 見出し境界チャンカーの実装（目安 512 トークン / オーバーラップ約 15%。M0 は文字数ベースの概算で可）
  - `[ ]` **`content_hash` の算出と保存**（`sources.content_hash` は NOT NULL。M0 は毎回フルロードで、**スキップ最適化はしない**が値は必ず格納する）
  - `[ ]` Voyage による埋め込み（voyage-4-lite, **`input_type="document"`**, バッチ）
  - `[ ]` パス単位での冪等な upsert 処理（同一 path の既存 chunks を削除 → 再挿入）
  - `[ ]` `ingest_runs` への実行ログ記録（`trigger`, `stats`: added/updated/skipped/failed_files）
  - `[ ]` CLI `make ingest CORPUS=path/` / `make demo`（demo は `seed/corpus/` を対象）
  - *完了条件*: `make demo` でデータが DB に格納され、成功ログが記録されること（S1 成立）。
  - *テスト*: チャンカー（見出し分割・サイズ調整）、content_hash 算出、upsert 冪等性の単体テスト。

- `[ ]` **M0-4 検索 (Retrieval)**
  - `[ ]` クエリの Voyage 埋め込み（voyage-4-lite, **`input_type="query"`** ← 取り込みと非対称）
  - `[ ]` `pgvector` によるベクトル検索（`embedding <=>`, top-5, `sources.deleted_at IS NULL` で除外）
  - *完了条件*: 任意のクエリで関連チャンクが取得できること（統合テスト）。
  - *テスト*: 検索 SQL の統合テスト（テスト用 DB に投入 → 期待チャンクが top-5 に入る）。

- `[ ]` **M0-5 生成 (Generation)**
  - `[ ]` プロンプトテンプレートの作成（`prompts/` に配置。system は「①コンテキストのみ ②`[n]` 出典 ③無ければ見つからない ④日本語」）
  - `[ ]` Claude API（非ストリーム）の呼び出し（モデル ID は設定化）
  - `[ ]` 回答への `[n]` 引用整形と `citations` 配列の組み立て（title/path/heading/chunk_id）
  - `[ ]` **不知応答はプロンプト駆動を主経路とする** — M0 のベクトル検索は無関係クエリでも top-5 を返すため「0件」にはならない。「見つからない」は system 指示③に基づきモデルが判断する。**0件チェックはインデックスが空の場合の保険**として実装（主経路ではない）
  - *完了条件*: 検索結果を用いた回答生成ができ、コーパス外の質問には「見つからない」と返せること（S2, S3 成立）。
  - *テスト*: 引用整形（本文 `[n]` と citations の対応）、空インデックス時のフォールバックの単体テスト（LLM はモック）。

- `[ ]` **M0-6 API 実装**
  - `[ ]` `GET /health` エンドポイント
  - `[ ]` `POST /api/chat`（JSON 応答）の実装と retrieval/generation の配線
  - *完了条件*: curl から API を叩き、正しい応答が返ること（S2, S3 を API 経由で確認）。
  - *テスト*: エンドポイントの正常系・コーパス外応答（retrieval/generation はモックまたはテスト DB）。

- `[ ]` **M0-7 可観測性 (Observability)**
  - `[ ]` Langfuse クライアントの設定
  - `[ ]` チャットの3スパン計装: `embed_query` → `retrieve` → `generate`
  - `[ ]` **取り込み時の埋め込み呼び出しのトレース**（コスト可視化。スペック §4.6）
  - `[ ]` 各 AI 呼び出しのトークン・コスト・レイテンシの記録
  - *完了条件*: チャット API 実行後、Langfuse にトレースが残ること（S4 成立）。

- `[ ]` **M0-8 評価 (Eval)**
  - `[ ]` ゴールデンデータセット `evals/golden/m0.yaml`（10問）の作成 — **スペック §8 付録のドラフト（質問＋期待ソース）を起こす**（ゼロから作らない）
  - `[ ]` Recall@5 ハーネスの実装（top-5 に期待ソース由来チャンクが1つ以上入った割合）
  - `[ ]` CLI `make eval` ターゲットの追加（Recall@5 を出力 + `evals/results/` に記録）
  - *完了条件*: `make eval` が実行でき、Recall@5 のベースラインが算出・記録されること（S5 成立）。

- `[ ]` **M0-9 仕上げ**
  - `[ ]` `make lint`（ruff + mypy）/ `make test` の整備と CI 準備
  - `[ ]` README.md にクイックスタート雛形（`docker compose up` → `make setup` → `make migrate` → `make demo`）を追記
  - *完了条件*: `make lint` および `make test` が通ること。
  - ※ 機能テストは M0-3〜M0-6 に分散済み。ここは lint/CI/README に限定する。

---

## Gemini 版からの主な修正点

1. **M0-5**: 不知応答をプロンプト駆動に修正（ベクトル検索は0件にならないため）。0件チェックは空インデックス時の保険に格下げ。
2. **M0-3**: `content_hash`（NOT NULL 列）の算出・保存を明記。
3. **M0-2**: pg_bigm の GIN 索引を M0 から除外し M1 に分担（拡張作成のみ M0）。`conversations`/`messages` を作らないことも明記。
4. **M0-3/M0-4**: Voyage の `input_type` を document / query で使い分けと明記。
5. **テスト分散**: 機能テストを各タスクに配置（M0-9 一括をやめる）。
6. **M0-7**: 取り込み時埋め込みのトレースを追加。
7. **命名**: 参照先を `m0_walking_skeleton.md` に修正（綴り・大文字）。本ファイルも `m0_task.md`。
8. **M0-8**: スペック付録のゴールデン10問ドラフトを参照。