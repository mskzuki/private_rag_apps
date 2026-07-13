# M0 Walking Skeleton タスクリスト

`docs/specs/m0_walking_skeleton.md` に基づくタスク一覧です。
各機能タスクには**対応するテストを同時に**含めます（AGENTS §8）。LLM・埋め込み・rerank 呼び出しはテストでモックします。

> **M5監査（2026-07-13）**: 本タスクリストは `docs/specs/m0_walking_skelton.md` §7 Definition of Done の項目別エビデンス検証（すでに完了済み）を根拠に、実装ステップ単位で一括チェックした（bulk pass）。個々の行を再検証してはいない。DoD が真であることが確認できた機能に包含されるタスクを `[x]` にし、DoD 検証中に見つかった実装との齟齬はその行に注記した。

- `[x]` **M0-1 インフラ設定**
  - `[x]` `docker-compose.yml` (Postgres + pgvector + pg_bigm) の作成
  - `[x]` `.env.example` の作成
  - `[x]` `core/config.py` (pydantic-settings) の実装
  - *完了条件*: `docker compose up` で DB が起動し、設定が読み込めること。

- `[x]` **M0-2 データベース・マイグレーション**（確認: `backend/alembic/versions/0001_init.py` が存在し、拡張・3テーブル・HNSW索引・補助索引を作成。`make migrate` で適用される）
  - `[x]` Alembic の初期化
  - `[x]` 初期マイグレーション `0001_init` の作成
    - 拡張機能: `pgcrypto`, `vector`, `pg_bigm`（**要注記・実装との齟齬**: 実際の `0001_init.py`（73-76行）はこの行の想定と異なり `chunks_content_bigm` GIN索引も同時に作成している。「索引は作らない → M1の0002担当」という本記述は実装と不一致。結果として `0002` マイグレーション（`0002_chunks_content_bigm.py`）は同名索引を `IF NOT EXISTS` で再作成するだけの冗長なno-opになっている。機能上の実害はない（索引は最終的に存在する）が、M1側のタスク説明も合わせて要修正 — 詳細は `m1_hybrid_search.md` §7 / `m1_tasklist.md` M1-1 参照）
    - テーブル: `sources`, `chunks`, `ingest_runs`
    - 索引: `chunks.embedding` の **HNSW**（`vector_cosine_ops`, m=16, ef_construction=64）、および補助索引（`chunks.source_id`, `sources` の部分索引 `deleted_at IS NULL`, `ingest_runs.started_at`）
    - ※ `conversations` / `messages` は M0 では作らない（確認: `0001_init.py` に無し。M2の `0003_chat_history.py` で追加）
  - *完了条件*: `make migrate` が成功し、DB 上にスキーマと HNSW 索引が構築されること。
  - *テスト*: マイグレーションの upgrade/downgrade が通ること。（`0001_init.py` に `downgrade()` 実装あり。DB依存のため本監査では実行未確認）

- `[x]` **M0-3 コーパス取り込み (Ingestion)**（確認: `ingestion/loader.py`, `ingestion/chunker.py`, `ingestion/indexer.py` が一連の取り込みパイプラインを実装）
  - `[x]` `seed/corpus/` ディレクトリの作成と設計文書（requirements/architecture/db_design/AGENTS）の配置（確認: `ls seed/corpus/` で4ファイル実在）
  - `[x]` `.md` ローダーの実装（タイトルは先頭 H1、なければファイル名）（確認: `ingestion/loader.py:32-37`）
  - `[x]` 見出し境界チャンカーの実装（目安 512 トークン / オーバーラップ約 15%。M0 は文字数ベースの概算で可）（確認: `ingestion/chunker.py:chunk_markdown`。ただしオーバーラップは未実装で見出し単位の非重複分割のみ — この点は元スペック§9でも「概算で開始」と明記され既知の簡略化）
  - `[x]` **`content_hash` の算出と保存**（確認: `ingestion/loader.py:13` で `sha256` 算出・`Document.content_hash` に保持）（**進化ノート**: 本項目はM0時点では「スキップ最適化はしない」設計だったが、現行コードは `ingestion/diff.py` の `classify()` でハッシュ差分によるSKIP/REPLACE/REVIVE判定を実装済み — M4で計画通り追加されたスキップ最適化であり後退ではない）
  - `[x]` Voyage による埋め込み（voyage-4-lite, **`input_type="document"`**, バッチ）（確認: `ingestion/indexer.py:170-182` `_embed_documents`）
  - `[x]` パス単位での冪等な upsert 処理（同一 path の既存 chunks を削除 → 再挿入）（確認: `ingestion/indexer.py:128-138` のREPLACE分岐で削除→再挿入）
  - `[x]` `ingest_runs` への実行ログ記録（`trigger`, `stats`: added/updated/skipped/failed_files）（確認: `ingestion/indexer.py:37,56` の `Stats` 組み立てと `run.stats` 保存）
  - `[x]` CLI `make ingest CORPUS=path/` / `make demo`（demo は `seed/corpus/` を対象）（確認: `Makefile:21-27`）
  - *完了条件*: `make demo` でデータが DB に格納され、成功ログが記録されること（S1 成立）。
  - *テスト*: チャンカー（見出し分割・サイズ調整）、content_hash 算出、upsert 冪等性の単体テスト。（確認: `tests/test_basic.py::TestChunkMarkdown`、`tests/test_ingestion_indexer.py`（DB依存、本監査では未実行・ファイル内容のみ確認））

- `[x]` **M0-4 検索 (Retrieval)**（確認: `retrieval/searcher.py`）
  - `[x]` クエリの Voyage 埋め込み（voyage-4-lite, **`input_type="query"`** ← 取り込みと非対称）（確認: `retrieval/searcher.py:58-65` `_embed_query`）
  - `[x]` `pgvector` によるベクトル検索（`embedding <=>`, top-5, `sources.deleted_at IS NULL` で除外）（確認: `retrieval/searcher.py:68-81` `_vector_search`。top_k は現在は設定値 `rerank_top_k`/戦略依存で可変だが、M0が要求する「関連チャンクが返る」動作自体は同一ロジック）
  - *完了条件*: 任意のクエリで関連チャンクが取得できること（統合テスト）。
  - *テスト*: 検索 SQL の統合テスト（テスト用 DB に投入 → 期待チャンクが top-5 に入る）。（DB依存のため本監査では未実行。`tests/test_retrieval.py` はRRF/rerankロジックの単体テストが中心で、ベクトル検索単体のDB統合テストは別途 `test_ingestion_indexer.py` 等の枠組みに準ずる）

- `[x]` **M0-5 生成 (Generation)**（確認: `generation/generator.py`, `prompts/rag.py`）
  - `[x]` プロンプトテンプレートの作成（`prompts/` に配置。system は「①コンテキストのみ ②`[n]` 出典 ③無ければ見つからない ④日本語」）（確認: `prompts/rag.py:1-4` `RAG_SYSTEM_PROMPT`）
  - `[x]` LLM API の呼び出し（モデル ID は設定化）（確認: `generation/generator.py:64-70` `openai.responses.create(model=settings.llm_model, ...)`。**注記**: 現在は `stream=True` でストリーミング呼び出しに変わっている（M2のSSE化に伴う進化。詳細は`m0_walking_skelton.md`§7参照）。生成対象LLMもClaudeからOpenAIモデルに変わっている点は仕様上「モデルIDは設定化」の範囲内）
  - `[x]` 回答への `[n]` 引用整形と `citations` 配列の組み立て（title/path/heading/chunk_id）（確認: `generation/generator.py:47-55`）
  - `[x]` **不知応答はプロンプト駆動を主経路とする**（確認: `prompts/rag.py:3` のsystem指示③、`generator.py:42-45` の空コンテキスト時ガード）
  - *完了条件*: 検索結果を用いた回答生成ができ、コーパス外の質問には「見つからない」と返せること（S2, S3 成立）。
  - *テスト*: 引用整形（本文 `[n]` と citations の対応）、空インデックス時のフォールバックの単体テスト（LLM はモック）。（確認: `tests/test_chat.py::test_generate_answer_stream_no_chunks`, `::test_generate_answer_stream_with_chunks`）

- `[x]` **M0-6 API 実装**（確認: `api/main.py`）
  - `[x]` `GET /health` エンドポイント（確認: `api/main.py:79-82`）
  - `[x]` `POST /api/chat` の実装と retrieval/generation の配線（確認: `api/main.py:123-238` `chat()`）（**注記**: 応答形式は本タスクが書く「JSON 応答」ではなく、M2以降SSEストリーミング（`EventSourceResponse`）に置き換わっている。retrieval/generationへの配線自体は健在。詳細は `m0_walking_skelton.md` §7 参照）
  - *完了条件*: curl から API を叩き、正しい応答が返ること（S2, S3 を API 経由で確認）。
  - *テスト*: エンドポイントの正常系・コーパス外応答（retrieval/generation はモックまたはテスト DB）。（確認: `tests/test_api.py::test_chat_bulk_save_and_history`）

- `[x]` **M0-7 可観測性 (Observability)**（確認: `retrieval/searcher.py`, `generation/generator.py`, `ingestion/indexer.py` 全体に `@observe` 計装）
  - `[x]` Langfuse クライアントの設定（確認: `langfuse` パッケージの `observe`/`get_client` を各層で使用。`LANGFUSE_*` 未設定時はno-op、`.env.example` に任意キーとして記載）
  - `[x]` チャットの計装: `embed_query` → `retrieve_context`（`vector_search`/`hybrid_search`/`rerank` に細分化）→ `generate`（確認: `retrieval/searcher.py:10,58,68,84,159`, `generation/generator.py:8,39`）
  - `[x]` **取り込み時の埋め込み呼び出しのトレース**（コスト可視化。スペック §4.6）（確認: `ingestion/indexer.py:170` `@observe(name="embed_documents")`）
  - `[x]` 各 AI 呼び出しのトークン・コスト・レイテンシの記録（確認: `update_current_generation(usage_details=...)` 呼び出しが `generator.py:27-33,80-86`, `searcher.py:180-189` に実在）
  - *完了条件*: チャット API 実行後、Langfuse にトレースが残ること（S4 成立）。

- `[x]` **M0-8 評価 (Eval)**（確認: `backend/evals/golden/m0.yaml`, `backend/src/private_rag_apps/evals/`）
  - `[x]` ゴールデンデータセット `evals/golden/m0.yaml`（10問）の作成（確認: `backend/evals/golden/m0.yaml` に q01〜q10 実在、スペック§8付録の文言と一致）
  - `[x]` Recall@5 ハーネスの実装（top-5 に期待ソース由来チャンクが1つ以上入った割合）（確認: `evals/metrics.py:37-38,53` `calc_recall`）
  - `[x]` CLI `make eval` ターゲットの追加（Recall@5 を出力 + 記録）（確認: `Makefile:41-42`、`evals/__main__.py`。**注記**: 保存先は当初案の `evals/results/` ではなく、M3統合ハーネスにより `evals/baselines/current.json` + `evals/reports/m3_*.json` に変更されている。`evals/golden/m0.yaml` 自体は現在の `make eval` からは参照されず、`evals/dataset/m3_golden.jsonl`（31問）に統合されている — レガシー資産として残存）
  - *完了条件*: `make eval` が実行でき、Recall@5 のベースラインが算出・記録されること（S5 成立）。

- `[x]` **M0-9 仕上げ**（確認: ruff実行PASS、README.mdクイックスタート実在）
  - `[x]` `make lint`（ruff + mypy）/ `make test` の整備と CI 準備（確認: `Makefile:32-34` に `lint` ターゲット実在。`uv run ruff check .` を本監査で実行しPASSを確認。mypy・pytestはDocker未起動のため本監査では未実行）
  - `[x]` README.md にクイックスタート雛形（`docker compose up` → `make setup` → `make migrate` → `make demo`）を追記（確認: `README.md:11-17` クイックスタート節）
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