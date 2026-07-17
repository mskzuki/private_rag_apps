# AGENTS.md

> このファイルは **AI コーディングエージェント（Claude Code 等）** がこのリポジトリで作業する際のガイドです。
> 人間向けの概要は `README.md`、要件は `docs/requirements.md`、詳細仕様は `docs/specs/` を参照してください。
> **矛盾がある場合は `docs/specs/` の個別仕様が最優先**、次に本ファイル、最後に一般的な慣習の順で従ってください。

---

## 1. プロジェクト概要

Private RAG Apps は、ローカルのプライベートドキュメントコーパス（Markdown / テキスト）を取り込み、ハイブリッド検索 + リランクで出典付き回答を返す RAG チャットアプリケーション。単一ユーザー向け。技術ショーケースとして、**品質評価（Eval）・可観測性・クリーンな境界**を重視する。

SaaS コネクタ（Notion/Slack）・OAuth・マルチユーザー・ACL は **v1 スコープ外**（requirements.md §11）。**Google Drive の限定的な取り込み（単一固定フォルダ・サービスアカウント認証・OAuth 不使用）は M9 で例外的にスコープイン**（`docs/specs/m9_google_drive_ingestion.md`）。

---

## 2. 技術スタック

- **Backend**: Python 3.13, FastAPI, uvicorn（`backend/`, パッケージ名 `private_rag_apps`）
- **Store**: PostgreSQL + pgvector + **pg_bigm**（ベクトル + 日本語全文）, Alembic
- **AI**: OpenAI GPT（生成）, Voyage voyage-4-lite / rerank-2.5（埋め込み・リランク）
- **Observability**: Langfuse
- **Frontend**: Next.js (App Router), TypeScript, **assistant-ui**（shadcn/ui ベースのチャット UI。カスタムランタイムで自前 SSE を受ける）（`frontend/`）
- **取り込み**: CLI（`make ingest` / `make demo`）+ API からの BackgroundTasks。ジョブキューは無い（**例外: M9 の Google Drive 取り込みは、API 経由トリガ（`POST /api/ingest/gdrive`）のみ ARQ/Redis を使用する。`docs/specs/m9_google_drive_ingestion.md` §3.3 参照**）
- **Package管理**: uv（Python）, pnpm（frontend）

---

## 3. ディレクトリ構成と依存方向

```
.
├── AGENTS.md
├── README.md
├── Makefile
├── docker-compose.yml
├── docs/
│   ├── requirements.md
│   ├── architecture.md
│   ├── db_design.md
│   ├── decisions.md           # 決定ログ（経緯付き）
│   ├── adr/                   # Architecture Decision Record。個別の設計判断を1件1ファイルで記録
│   └── specs/                 # スペック駆動開発。実装前にここを更新する
├── seed/                      # シードコーパス（デモモード & Eval 兼用）
├── backend/
│   ├── src/private_rag_apps/
│   │   ├── core/              # 設定・DB接続・テレメトリ（共有基盤）
│   │   ├── models/            # SQLAlchemy + Pydantic
│   │   ├── cli/               # ingest / demo コマンド（ingestion を呼ぶ薄い層）
│   │   ├── ingestion/         # コーパス読込 → 正規化 → チャンキング → 埋め込み → upsert
│   │   ├── retrieval/         # hybrid search (pgvector + pg_bigm) → RRF → rerank
│   │   ├── generation/        # クエリ書換 → プロンプト組立 → LLM呼び出し → 引用付与
│   │   ├── graph/             # LangGraph によるグラフオーケストレーション（M7〜）
│   │   ├── prompts/           # プロンプトはここに集約（コードにハードコードしない）
│   │   └── api/               # FastAPI ルート・SSE・BackgroundTasks
│   ├── tests/
│   ├── evals/                 # ゴールデンデータセットと評価ハーネス
│   └── pyproject.toml
└── frontend/                  # Next.js チャット UI (assistant-ui)
```

### 依存方向のルール（守ること）

- `graph`（M7 で新設。LangGraph によるグラフオーケストレーション層）は `generation` と `retrieval` を独立に import してよい（`generation` は `retrieval` を import しない、という制約は `graph` 経由でも維持する）。`api` は `generation`/`retrieval` を直接呼ぶ代わりに、**`graph` 経由でこれらの処理を呼ぶ**。会話履歴のロード・永続化は引き続き `api`（FastAPI ハンドラ層）が担い、`graph` には持ち込まない（`graph` はステートレスな1リクエスト=1実行の関数として扱う）。`retrieval`/`generation`/`graph` から `api` への**逆流はさせない**。（M7 T5 で rewrite ノードが `graph` 内に実装され、`api` が `generation.condense` をグラフ外から直接呼んでいた暫定例外は解消済み。`api` は生の `user_query`/`history` を `graph` に渡すのみ）
- `ingestion` はストアに書き込む側。`retrieval` はストアから読む側。両者を直接依存させない。
- `cli` は `ingestion` を呼ぶ薄い層。ロジックを持たない。
- `core` は全レイヤから参照される共有基盤。`core` から上位レイヤを import しない。
- **LLM 呼び出しは `generation/`（と `evals/`）のみ**。
- **埋め込み呼び出しは `ingestion/`（インデックス時）と `retrieval/`（クエリ時）のみ**。
- **リランク呼び出しは `retrieval/` のみ**。
- ローカル FS（コーパス）へのアクセスは `ingestion/`（取り込み時の読み込み）と `evals/`（corpus ハッシュ算出・データセットの path 実在検証）のみ。
- **Google Drive API へのアクセスは `ingestion/` のみ**（M9。ローカル FS アクセスと同じ局所化ルールを適用する。`docs/specs/m9_google_drive_ingestion.md`）。

---

## 4. セットアップ

```bash
make setup        # uv sync + pnpm install + .env 生成 + DB 起動
make migrate      # Alembic マイグレーション適用
make demo         # シードコーパス取り込み → すぐチャット可能
```

- 環境変数は `.env`（`.env.example` からコピー）。**`.env` はコミットしない**。
- 必要なキー: `OPENAI_API_KEY`, `VOYAGE_API_KEY`, `DATABASE_URL`, `CORPUS_DIR`。`LANGFUSE_*` は**任意**（未設定時は計装が no-op になり、アプリ・デモ・eval は動作する。requirements NFR-4/NFR-8）。増分再取り込み関連の `INGEST_*` 設定は任意（既定値あり。`core/config.py` 参照）。
- `docker-compose.yml` の `db` サービスは、標準の `pgvector/pgvector` イメージには含まれない **pg_bigm 拡張をソースからビルドして組み込んだイメージ**（`backend/docker/db/Dockerfile.local`）を使う。`docker compose up` だけで `CREATE EXTENSION pg_bigm` が通る状態を保証する。
- `make demo` は `.env` の `CORPUS_DIR` の値によらず、**常に `seed/corpus` を取り込み対象にする**（デモの再現性を優先）。自分の文書を試す場合は先にインデックスを初期化してから `CORPUS_DIR` を差し替えて `make ingest` する（README クイックスタート参照）。

---

## 5. コマンド（Makefile 経由で実行すること）

| コマンド | 内容 |
|---|---|
| `make setup` | 初期セットアップ（uv sync + pnpm install + `.env` 生成 + DB 起動。§4） |
| `make api` | API 起動（`uv run uvicorn private_rag_apps.api.main:app --reload`） |
| `make web` | フロント起動（`pnpm --dir frontend dev`） |
| `make ingest CORPUS=path/` | コーパス取り込み（CLI） |
| `make demo` | シードコーパスで即デモ可能な状態にする |
| `make test` | `uv run pytest` |
| `make lint` | backend: `uv run ruff check . && uv run mypy .` / frontend: `biome lint .` + `biome format .`（チェックのみ） |
| `make fmt` | backend: `uv run ruff format .` / frontend: `biome format --write .` |
| `make eval` | `uv run python -m private_rag_apps.evals`（既存e2e eval + M7複合質問の補足書式検証） |
| `make eval-routing` | `uv run python -m private_rag_apps.evals.routing`（M7 routing eval。rewrite→retrieve→gradeを評価。generateは実行しない） |
| `make eval-all` | `make eval` + `make eval-routing` |
| `make migrate` | `uv run alembic upgrade head` |

> 個別に `pip install` / `python xxx.py` を叩かず、原則 Make ターゲット・`uv run` を使うこと。

---

## 6. コーディング規約

- **フォーマット/Lint（backend）**: ruff（`make fmt` / `make lint`）。独自スタイルを持ち込まない。
- **フォーマット/Lint（frontend）**: Biome（`make fmt` / `make lint`）。ESLint / Prettier は使わない。
- **型**: mypy を通す。**public 関数には型注釈必須**。`Any` は原則使わない。
- **async**: I/O（DB・HTTP・LLM）は async で書く。同期版と混在させない。
- **設定**: 値のハードコード禁止。設定は `core/config.py`（pydantic-settings）経由。
- **プロンプト**: コード中に文字列で埋め込まない。`prompts/` に置き、バージョンを意識する。
- **命名**: レイヤ名・要件 ID（FR-x / NFR-x）と対応が付く名前を使う。
- **ファイル名**: 単語の区切りは **`_`（スネークケース）で統一**する。ハイフン（`-`）・キャメルケース・スペースは使わない。
  - 例: `db_design.md`, `m0_walking_skeleton.md`, `ingest_runs.py`
  - 例外: フレームワーク側の規約で固定されるもの（`README.md`, `AGENTS.md`, Next.js の予約ファイル名等）はそのまま使う。
  - `docs/specs/` 配下のスペックファイルも本ルールに従う（例: `m0_walking_skeleton.md`, `m1_hybrid_search.md`）。

---

## 7. RAG 特有のルール（重要）

- **生成は取得コンテキストにのみ基づく**。コンテキスト外の主張を書かせない。
- **回答には必ず出典（citation）を付ける**。出典を辿れない回答は不可。
- コンテキストに答えが無い場合は「見つからない」を返す実装にする（でっち上げない）。
- **埋め込みモデル / チャンキング戦略を変更したら、再インデックス + `make eval` が必須**。
  影響範囲（既存インデックスとの非互換）を PR に明記する。
- **プロンプトを変更したら `make eval` を実行**し、スコアの劣化がないか確認する。
- **最小 Eval は M0 から存在する**（requirements §9）。「Eval がまだ無いので計測せず変更する」は M0 完了以降は認められない。
- すべての LLM / 埋め込み / リランク呼び出しは **Langfuse トレースに記録**されるように実装する（トレース漏れを作らない）。計装は M0 の骨格段階から配線する。`LANGFUSE_*` 未設定時は no-op（動作を妨げない。requirements NFR-4）。
- **Eval CI は再現経路をたどる**（M3 以降）: DB 起動 → `make migrate` → `make ingest`（seed）→ `make eval` → committed baseline と比較。ゲートは**検索指標=ハード / 生成指標=ソフト**（詳細: `docs/specs/m3_eval_expansion.md` §7）。
- **（M7）THETA・rewrite プロンプト・grade ロジックを変更したら `make eval-routing` を必須とする**（grounded/direct の経路判定精度への影響を確認するため。合格基準は `docs/specs/m7_adaptive_routing.md` §7.2、初期THETA決定の根拠は `docs/adr/0001_m7_theta_threshold.md`）。
- **（M7）grounded / direct プロンプトを変更したら `make eval` を必須とする**（`make eval` は生成に実際に使われる grounded プロンプトを経由するため、プロンプト変更の影響がここに現れる。詳細: `docs/specs/m7_adaptive_routing.md` §7.3）。**現状の例外:** Voyage/OpenAI 無支払い枠のレート制限により `make eval` が完走できないことがある（`docs/adr/0003_m7_t3_eval_baseline_gap.md`, `docs/adr/0004_m7_make_eval_excluded.md`）。この制約が解消するまで、M7 の各タスク完了条件からは `make eval` 実行が除外されている（ADR 0004）。generate 品質の非劣化確認は、代わりに direct groundedness eval・補足書式検証（LLM-as-judge + 人手裁定）と手動スモークテストで担保する。

---

## 8. テスト方針

- **ユニット**: チャンキング・RRF 融合・引用整形など純粋ロジックを中心に。
- **統合**: 取り込み → 検索 → 生成のパスを、テスト用 DB（pgvector + pg_bigm）で検証。
- **LLM / 外部 API 呼び出しはモック / 記録再生**する。テストで実課金 API を叩かない。
- **Eval はテストとは別物**。合否ではなくスコア回帰の監視に使う（`make eval`）。
- 新機能・バグ修正には対応するテストを付ける。

---

## 9. Git / PR 規約

- コミットは **Conventional Commits**（`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`）。
- 1 PR = 1 関心事。レイヤをまたぐ大改修は分割する。
- PR 説明に **対応する要件 ID（FR-x / NFR-x）** と、RAG 挙動を変える場合は **Eval スコアの before/after** を記載する（M3 以降は CI が before/after を PR に自動記載する）。

---

## 10. Definition of Done（この全てを満たしてから完了とする）

- [ ] `make lint` と `make test` が通る
- [ ] 変更に対応するテストがある
- [ ] RAG 挙動（検索・プロンプト・チャンキング・埋め込み）を変えた場合、`make eval` を実行しスコアを PR に記載した
- [ ] 依存方向ルール（§3）を破っていない
- [ ] シークレット・実データをコミットしていない
- [ ] 関連する `docs/specs/` を更新した

---

## 11. DO NOT（やってはいけないこと）

- ❌ シークレット（API キー）・個人の実データをコミットする（同梱してよいのは `seed/` のシードコーパスのみ）
- ❌ プロンプトをコードにハードコードする（`prompts/` を使う）
- ❌ Eval を回さずにプロンプト / チャンキング / 埋め込みを変更する（M0 完了以降）
- ❌ 依存方向（§3）に反する import を書く（例: `retrieval` から `ingestion` を呼ぶ）
- ❌ `generation`・`evals` 以外の層で LLM を直接呼ぶ（§3 のとおり。Eval の LLM-as-judge は `evals/` から呼ぶ）
- ❌ テストで実課金の外部 API を叩く
- ❌ **仕様（`docs/specs/`）に無い機能を勝手に追加する**。必要と判断したら、まず仕様案を提示して合意を得る
- ❌ スコープ外（SaaS コネクタ / OAuth / マルチユーザー / ACL / エージェンティック RAG / PDF パース。requirements.md §11 参照）を v1 に混ぜ込む
- ❌ ジョブキュー（Redis/ARQ/Celery 等)を導入する（v1 は CLI + BackgroundTasks で足りる設計判断済み）。**例外: M9 の Google Drive 取り込みは、API 経由トリガのみプロセス非依存の再試行を目的に ARQ/Redis を使用する（CLI 経由トリガ・ローカル取り込みは引き続き同期実行/BackgroundTasks のまま）。`docs/specs/m9_google_drive_ingestion.md` §3.3 参照**
- ❌ チャットの基本 UX（streaming / auto-scroll / retry / thread 管理）を自前で再実装する（assistant-ui のコンポーネント・ランタイムを使う）

---

## 12. スペック駆動開発について

- 実装の前に `docs/specs/` の該当仕様を読み、必要なら**先に仕様を更新**してから実装する。
- **各マイルストーンの実装に着手する前に、フィーチャースペックのタスク分解を `docs/specs/mN_tasklist.md`（N はマイルストーン番号。例: `m0_tasklist.md`）として作成する**。チェックボックス形式で進捗を管理し、実装はこのタスクリストの順に進める。順序は「スペック本体（`mN_*.md`）→ タスクリスト（`mN_tasklist.md`）→ 実装」を守る。
- 仕様と実装が食い違ったら、**勝手に実装側へ寄せず**、差分を指摘して合意を取る。
- 大きな設計判断は仕様に根拠を残す（後からレビュアーが辿れるように）。

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.15 | 2026-07-17 | M9（Google Drive フォルダ取り込み）スペック起票に伴う改訂: §1 のスコープ外記述から Google Drive を例外化。§2 技術スタックの「ジョブキューは無い」記述に M9 の ARQ/Redis 例外を注記（レビューで発見した §1/§3/§11 との内部矛盾を解消）。§3 依存方向ルールに「Google Drive API へのアクセスは `ingestion/` のみ」を追加。§11 DO NOT のジョブキュー導入禁止に、M9 の API 経由トリガに限定した ARQ/Redis 例外を追記。詳細は `docs/specs/m9_google_drive_ingestion.md` |
| v0.14 | 2026-07-16 | M7 T5 追従: §3 依存方向ルールの「暫定例外」（`api` が `generation.condense` をグラフ外から直接呼ぶ状態）を解消した旨を反映。rewrite ノードが `graph` 内に実装され、`api` は生の `user_query`/`history` を `graph` に渡すのみになった |
| v0.13 | 2026-07-15 | ADR 0004反映: §7の「grounded/directプロンプト変更時はmake eval必須」ルールに、Voyage/OpenAIレート制限によりM7の完了条件からmake evalが除外されている旨の例外注記を追加 |
| v0.12 | 2026-07-15 | M7 T4 追従: §5 コマンド表に `make eval-routing`/`make eval-all` を追加。§7 に「THETA・rewrite プロンプト・grade ロジック変更時は `make eval-routing` 必須」「grounded/direct プロンプト変更時は `make eval` 必須」を追記 |
| v0.11 | 2026-07-15 | M7 T3 レビュー反映: §3 依存方向ルールに、rewrite ノード（T5）実装までの暫定例外（`api` が `generation.condense` をグラフ外から直接呼び出す状態）を明記し、実装との矛盾を解消 |
| v0.10 | 2026-07-15 | M7 T3 追従: §3 に新設パッケージ `graph/`（LangGraph によるグラフオーケストレーション層）を追加。依存方向ルールを更新（`graph` は `generation`/`retrieval` を独立に import してよい／`api` は `graph` 経由でこれらを呼ぶ／履歴のロード・永続化は `api` に残り `graph` には持ち込まない） |
| v0.9 | 2026-07-13 | M5 監査反映: §3 の依存方向記述を実装に合わせて修正（`api` は `generation`/`retrieval` を直接・独立に呼ぶ実態を明記／`evals/` を FS アクセス例外に追加） |
| v0.8 | 2026-07-11 | M4 追従: §4 に pg_bigm 入り Postgres イメージの注記、`INGEST_*` 設定が任意である旨、`make demo` が `.env` の `CORPUS_DIR` によらず常に `seed/corpus` を使う旨を追記 |
| v0.7 | 2026-07-08 | 全体レビュー反映: §11 DO NOT の LLM 制限を「`generation`・`evals` 以外」に修正（§3 との矛盾解消。M3 の LLM-as-judge 実装ブロッカー除去）。`LANGFUSE_*` を任意キー化（§4/§7。requirements v0.4 追従）。§5 コマンド表に `make setup` を追加。§7 に Eval CI の再現経路（migrate→ingest(seed)→eval）とゲート方針（検索ハード/生成ソフト）を追記。§9 に CI の before/after 自動記載を追記 |
| v0.6 | 2026-07-07 | Python バージョンを 3.13 に変更（§2）。requirements.md v0.4 に追従 |
| v0.5 | 2026-07-07 | フロントエンドのディレクトリを `web/` → `frontend/` に変更（§2/§3/§5）。マイルストーン実装前に `mN_tasklist.md` を作成する運用を §12 に追加 |
| v0.4 | 2026-07-07 | ファイル命名規約(スネークケース統一)を §6 に追加 |
| v0.3 | 2026-07-07 | フロントに assistant-ui を採用（§2 スタック更新）。DO NOT にチャット基本 UX の自前再実装禁止を追加 |
| v0.2 | 2026-07-07 | requirements v0.2 追従: connectors モジュール・ARQ/Redis worker・OAuth 関連を削除。`cli/` と `seed/` を構成に追加、`make ingest`/`make demo` に置換。DO NOT にジョブキュー導入禁止・スコープ外項目の更新。最小 Eval が M0 から存在する前提を §7 に明記 |
| v0.1 | 2026-07-04 | 初版（壁打ちドラフト） |