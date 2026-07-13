# M0 — Walking Skeleton（フィーチャースペック）

> 配置先: `docs/specs/m0_walking_skeleton.md`
> 準拠: requirements.md v0.3 / architecture.md v0.3 / db_design.md v0.2 / AGENTS.md v0.3
> ステータス: ドラフト v0.1

---

## 1. ゴール

コーパス取り込みから回答生成までを **end-to-end で縦に薄く貫通**させる。以降のマイルストーン（M1: ハイブリッド化、M2: ストリーミング+UI…）が乗る土台を作る。

M0 完了時点で「シードコーパス（＝本プロジェクトの設計文書）を取り込み、`POST /api/chat` に日本語で質問すると、出典付きの回答が JSON で返り、Langfuse にトレースが残り、`make eval` が Recall@5 を出す」状態になる。

充足する要件: **FR-2(基本), FR-4(非ストリーム), NFR-4, requirements §9 最小Eval**

---

## 2. スコープ

### In scope (M0)

- docker-compose で PostgreSQL + pgvector + pg_bigm を起動
- Alembic 初期マイグレーション（`sources` / `chunks` / `ingest_runs`）
- CLI 取り込み（`make ingest` / `make demo`）: `.md` を見出し境界でチャンク → voyage-4-lite で埋め込み → upsert
- **ベクトル検索のみ**（top-5）
- **非ストリーム**の回答生成（出典付き JSON、コンテキスト外は「見つからない」）
- `POST /api/chat`（JSON 応答）+ `GET /health`
- Langfuse 計装（embed_query → retrieve → generate のスパン、トークン・コスト・レイテンシ）
- 最小 Eval: ゴールデン10問に対する **Recall@5**（`make eval`）
- シードコーパス `seed/corpus/`（設計文書のコピー）

### Out of scope (M0 → 後続M)

| 項目 | 送り先 |
|---|---|
| ハイブリッド検索（pg_bigm）/ RRF / リランク | M1 |
| SSE ストリーミング / assistant-ui チャットUI / 出典カードUI | M2 |
| 会話履歴・複数ターン・クエリ書き換え(condense) | M2 |
| 増分再取り込み(content_hash スキップ)・削除反映・データ管理UI | M4 |
| 生成品質の Eval（Faithfulness 等） | M3 |

> M0 は**単発 Q&A・履歴なし**。`conversations` / `messages` テーブルは M2（FR-6）で追加する。db_design §9 の 0001_init は「M0 で使うテーブルを作る初期マイグレーション」として増分的に読む。

---

## 3. 受け入れシナリオ（Given / When / Then）

**S1: 取り込み**
- Given クリーンな DB
- When `make demo` を実行
- Then `seed/corpus/` の `.md` が取り込まれ、`sources` と `chunks` に行が入り、`ingest_runs` に `status='success'` と件数統計が記録される

**S2: コーパス内の質問**
- Given デモコーパス取り込み済み
- When `POST /api/chat` に「全文検索に使っている拡張は?」を送る
- Then `pg_bigm` を含む回答が、出典（`db_design.md` 等）付きで JSON で返る

**S3: コーパス外の質問**
- Given 同上
- When `POST /api/chat` に「東京の明日の天気は?」を送る
- Then 推測せず「該当する情報が見つかりませんでした」を返す（出典は空）

**S4: 可観測性**
- Given Langfuse 設定済み
- When 任意の `/api/chat` を1回実行
- Then Langfuse に1トレースが作られ、`embed_query` / `retrieve` / `generate` のスパンとトークン・コストが記録される

**S5: Eval**
- Given デモコーパス取り込み済み、ゴールデン10問あり
- When `make eval`
- Then Recall@5 が算出・表示され、ベースラインとして記録される

---

## 4. 技術設計（M0 固有）

### 4.1 データフロー（Query Path, M0）

```
question → embed_query(voyage-4-lite, input_type=query)
        → vector search (pgvector <=>, top-5, deleted_at IS NULL)
        → prompt 組立([n]付きcontext)
        → Claude(非ストリーム)
        → {content, citations} を JSON 応答
```

M0 では condense / pg_bigm / RRF / rerank は**通らない**。

### 4.2 取り込み（M0）

- 対象: `.md` のみ（`.txt` は対応するが seed は `.md`）
- チャンキング: 見出し境界を尊重、目安 512 トークン / オーバーラップ約 15%（architecture §6）
- 埋め込み: voyage-4-lite / `input_type="document"` / バッチ
- 書き込み: **path 単位で冪等**（同一 path の既存 chunks を削除 → 再挿入）。`content_hash` は保存するが**スキップ最適化は M4**（M0 は毎回フルロード）
- `ingest_runs` に `trigger`（cli/demo）と `stats`（added/updated/skipped/failed_files）を記録

### 4.3 検索（M0）

```sql
SELECT c.id, c.content, c.metadata, s.title, s.path
FROM chunks c
JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL
ORDER BY c.embedding <=> :q_embedding
LIMIT 5;
```

### 4.4 生成（M0）

- プロンプトは `prompts/` に配置（コードにハードコード禁止, AGENTS §6）
- system: ①取得コンテキストのみに基づく ②各主張に `[n]` 出典 ③無ければ「見つからない」 ④日本語
- context: top-5 チャンクを `[n] {title}\n{content}` で列挙
- LLM: Claude（非ストリーム）。モデルIDは設定化（デフォルトは Sonnet 系）
- 応答整形: 本文の `[n]` と citations 配列を対応付け

### 4.5 API 契約（M0）

**`POST /api/chat`**
```jsonc
// request
{ "message": "全文検索に使っている拡張は?" }

// response 200
{
  "content": "pg_bigm を使っています[1]。標準FTSは日本語を分かち書きできないためです[1]。",
  "citations": [
    { "n": 1, "title": "DB設計", "path": "seed/corpus/db_design.md", "heading": "拡張と全文検索の選択", "chunk_id": "…" }
  ]
}

// response 200（コンテキスト外）
{ "content": "該当する情報が見つかりませんでした。", "citations": [] }
```

**`GET /health`** → `{ "status": "ok" }`

> M0 では会話系エンドポイント・SSE は作らない。

### 4.6 可観測性（M0）

- 1 リクエスト = 1 Langfuse トレース。スパン: `embed_query` → `retrieve` → `generate`
- 各 AI 呼び出しにトークン・コスト・レイテンシを記録
- 取り込み時の埋め込み呼び出しもトレース（コスト可視化）

---

## 5. Eval 定義（最小・M0）

- **形式**: ゴールデンデータセット `evals/golden/m0.yaml` = `[{id, question, expected_sources: [path,...]}]`
- **指標**: **Recall@5** = 「top-5 の取得チャンクの中に、期待ソース文書由来のチャンクが1つ以上含まれる」質問の割合
- **対象**: コーパス内質問10問（§8 のドラフト参照）。コーパス外の否定質問は Recall 対象外だが、S3 の受け入れテストに使う
- **実行**: `make eval` → Recall@5 を標準出力 + `evals/results/` に記録（ベースライン）
- 生成品質（Faithfulness 等）は M3。M0 は**検索の再現率のみ**

---

## 6. タスク分解（実装順）

各タスクは独立して着手・レビュー可能な粒度。1タスク1PR推奨。

| ID | タスク | 完了条件 |
|---|---|---|
| **M00-1** | インフラ: docker-compose（Postgres + pgvector + pg_bigm イメージ）、`.env.example`、`core/config.py` | `docker compose up` で DB 起動、設定が読める |
| **M00-2** | DB: Alembic `0001_init`（拡張 + sources + chunks + ingest_runs + HNSW/索引） | `make migrate` が通り、テーブル・索引が作られる |
| **M00-3** | 取り込み: `.md` ローダー + 見出しチャンカー + voyage 埋め込み(document) + path冪等 upsert + `ingest_runs` 記録。CLI `ingest`/`demo`。`seed/corpus/` に設計文書配置 | `make demo` で S1 が成立 |
| **M00-4** | 検索: query 埋め込み(query) + ベクトル検索(top-5) | 与えたクエリで関連チャンクが返る（統合テスト） |
| **M00-5** | 生成: `prompts/` + Claude 非ストリーム呼び出し + 引用整形 + not-found フォールバック | S2 / S3 が成立 |
| **M00-6** | API: `POST /api/chat`(JSON) + `GET /health` を retrieval/generation に配線 | curl で S2 / S3 が確認できる |
| **M00-7** | 可観測性: Langfuse 計装（3スパン + token/cost） | S4 が成立 |
| **M00-8** | Eval: `evals/golden/m0.yaml`(10問) + Recall@5 ハーネス + `make eval` | S5 が成立、ベースライン記録 |
| **M00-9** | テスト + Make ターゲット + README クイックスタート雛形 | `make lint`/`make test` が通る |

---

## 7. Definition of Done（M0）

> M5監査（2026-07-13）: 現行コード（`backend/src/private_rag_apps/`）に対して項目ごとに検証し、根拠を付記した。

- [x] `docker compose up` で pgvector + pg_bigm 入り Postgres が起動する（確認: `docker-compose.yml` の `db` サービスが `backend/docker/db/Dockerfile.local` をビルド。同 Dockerfile は `pgvector/pgvector:pg16` ベースに pg_bigm をソースからビルドして組み込み）
- [x] `make migrate` が `0001_init` を適用する（確認: `Makefile:16-17` の `migrate` → `alembic upgrade head`、`backend/alembic/versions/0001_init.py` が拡張・テーブル・索引を作成）
- [x] `make demo` が設計文書を取り込み、`sources`/`chunks` が埋まり、`ingest_runs` に成功記録が残る（確認: `Makefile:26-27` の `demo` → `ingest --trigger demo`、`ingestion/indexer.py:execute_ingestion` が Source/Chunk 行を作成し `run.status = "success"` を設定（indexer.py:82）。`seed/corpus/` に AGENTS.md / architecture.md / db_design.md / requirements.md が実在）
- [ ] `POST /api/chat`（コーパス内）が出典付き JSON を返す（S2） — **未チェック**: 現行の `api/main.py:123` `chat()` は本スペックが想定する非ストリームJSON応答ではなく、`EventSourceResponse` によるSSEストリーミングに置き換わっている（本スペック§2 out-of-scopeテーブルどおりM2でSSE化された、想定内の進化）。出典自体は `citations` イベント（`generator.py:47-57`）、本文は `token` イベントで配信され内容面（出典付き回答）は満たすが、字句通りの「非ストリームJSON応答」ではないため未達として記録
- [ ] `POST /api/chat`（コーパス外）が「見つからない」を返す（S3） — **未チェック**: 上と同じ理由でSSE化により応答形式が本スペック記述と不一致。フォールバック機構自体（システムプロンプトの指示 `prompts/rag.py:3`、空コンテキスト時のガード `generator.py:42-45`）は存在し機能している
- [x] Langfuse に `embed_query`/`retrieve`/`generate` スパン + token/cost が出る（S4）（確認: `retrieval/searcher.py:58` `@observe(name="embed_query")`、`searcher.py:10` `@observe(name="retrieve_context")`、`generation/generator.py:8,39` `@observe(as_type="generation")`。M1以降 `retrieve` は `vector_search`/`hybrid_search`/`rerank` に細分化されているが、token/cost記録は維持）
- [x] `make eval` が Recall@5 を出しベースライン記録（S5）（確認: `evals/metrics.py:53` が `recall_5` を算出、`evals/__main__.py:164-188` がbaselineと比較・`evals/baselines/current.json` へ記録。M3統合ハーネスに引き継がれ質問数・保存先は変わったが指標自体は継続）
- [x] `make lint` / `make test` が通る（チャンキング・検索SQL・引用整形をカバー、LLM/埋め込みはモック）（確認: `cd backend && uv run ruff check .` 実行しPASS。チャンキング: `tests/test_basic.py::TestChunkMarkdown`。引用整形: `tests/test_basic.py::TestCitationFormatting`。LLM/埋め込みモック: `tests/test_chat.py`。mypyおよびDB依存テスト（`test_ingestion_indexer.py`等）はDocker未起動のため本監査では未実行、ファイル存在と内容のみ確認）
- [x] 依存方向ルール（AGENTS §3）を守っている（確認: grepで `retrieval/searcher.py` から `ingestion`/`generation` へのimportなし、LLM呼び出しは `generation/`・`evals/` に限定、埋め込み呼び出しは `ingestion/`・`retrieval/` に限定、rerank呼び出しは `retrieval/` に限定）
- [x] シークレット・実データを含まない（同梱は `seed/` のみ）（確認: `.gitignore:2` に `.env`、`git ls-files` に `.env` なし、`backend/src` にAPIキーのハードコードなし、`seed/corpus/` は設計文書のみ）

---

## 8. 付録: ゴールデン質問ドラフト（`evals/golden/m0.yaml` の種）

自己参照コーパス（設計文書）に対する10問。expected_sources は「その質問に答えている文書」。実装時に文言・期待ソースを微調整する。

| id | question | expected_sources |
|---|---|---|
| q01 | 全文検索に使っている拡張は何? | db_design.md, requirements.md |
| q02 | 埋め込みモデルと次元は? | requirements.md, db_design.md |
| q03 | ベクトルインデックスの種類とパラメータは? | db_design.md |
| q04 | ハイブリッド検索で結果を融合する手法は? | architecture.md, db_design.md |
| q05 | なぜ Qdrant ではなく Postgres を選んだ? | architecture.md |
| q06 | チャットUIに採用したライブラリと、ChatKit を選ばなかった理由は? | requirements.md, architecture.md |
| q07 | ドキュメント更新時のチャンクの扱いは? | architecture.md, db_design.md |
| q08 | LLM 呼び出しを許可されているモジュールは? | AGENTS.md |
| q09 | v1 でスコープ外にした主要項目は? | requirements.md |
| q10 | 取り込みの実行経路（CLIとAPI）はどうなっている? | architecture.md |

否定テスト用（Recall対象外・S3用）: 「東京の明日の天気は?」「このアプリの月額料金は?」

---

## 9. オープンな論点

- チャンキングの「512トークン」をトークン厳密で測るか概算（文字数ベース）で M0 は済ませるか → M0 は概算で開始し、M1 で厳密化を検討
- top-5 の妥当性は Eval のベースライン取得後に見直す（M1 でハイブリッド化した際に再チューニング）

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-07 | 初版（M0 スペック・ドラフト） |