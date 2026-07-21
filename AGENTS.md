# AGENTS.md

> このファイルは **AI コーディングエージェント（Claude Code 等）** がこのリポジトリで作業する際のガイドです。
> 人間向けの概要は `README.md`、要件は `docs/requirements.md`、詳細仕様は `docs/specs/` を参照してください。
> **矛盾がある場合は `docs/specs/` の個別仕様が最優先**、次に本ファイル、最後に一般的な慣習の順で従ってください。

---

## 1. プロジェクト概要

Private RAG Apps は、ローカルのプライベートドキュメントコーパス（Markdown / テキスト）を取り込み、ハイブリッド検索 + リランクで出典付き回答を返す RAG チャットアプリケーション。単一ユーザー向け。技術ショーケースとして、**品質評価（Eval）・可観測性・クリーンな境界**を重視する。

SaaS コネクタ（Notion/Slack）・OAuth・マルチユーザー・ACL は **v1 スコープ外**（requirements.md §11）。**Google Drive の限定的な取り込み（単一固定フォルダ・サービスアカウント認証・OAuth 不使用）は M9 で例外的にスコープイン**（`docs/specs/26071710-m9_google_drive_ingestion/spec.md`）。

---

## 2. 技術スタック

- **Backend**: Python 3.13, FastAPI, uvicorn（`backend/`, パッケージ名 `private_rag_apps`）
- **Store**: PostgreSQL + pgvector + **pg_bigm**（ベクトル + 日本語全文）, Alembic
- **AI**: OpenAI GPT（生成）, Voyage voyage-4-lite / rerank-2.5（埋め込み・リランク）
- **Observability**: Langfuse
- **Frontend**: Next.js (App Router), TypeScript, **assistant-ui**（shadcn/ui ベースのチャット UI。カスタムランタイムで自前 SSE を受ける）（`frontend/`）
- **取り込み**: CLI（`make ingest` / `make demo`）+ API からの BackgroundTasks。ジョブキューは無い（**例外: M9 の Google Drive 取り込みは、API 経由トリガ（`POST /api/ingest/gdrive`）のみ ARQ/Redis を使用する。`docs/specs/26071710-m9_google_drive_ingestion/spec.md` §3.3 参照**）
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
│   └── specs/                 # スペック駆動開発。実装前にここを更新する（配置ルールは §6 参照）
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
│   │   ├── worker/            # ARQ ジョブ関数（Google Drive 取り込みの API 経由トリガ専用。M9〜）
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
- `worker`（M9 で新設。ARQ ジョブ関数）も `cli` と同様、`ingestion` を呼ぶ薄い層としロジックを持たない（`cli`/`worker` はいずれも `ingestion.execute_gdrive_ingestion()` という同一の入口を呼ぶ。ロジックの二重実装を避ける）。`api` は `worker` を直接呼ばず、Redis 経由でジョブを enqueue するのみ（`graph` 同様、`worker` から `api` への逆流はさせない）。`docs/specs/26071710-m9_google_drive_ingestion/spec.md` §4.5/§4.6
- `core` は全レイヤから参照される共有基盤。`core` から上位レイヤを import しない。
- **LLM 呼び出しは `generation/`（と `evals/`）のみ**。
- **埋め込み呼び出しは `ingestion/`（インデックス時）と `retrieval/`（クエリ時）のみ**。
- **リランク呼び出しは `retrieval/` のみ**。
- ローカル FS（コーパス）へのアクセスは `ingestion/`（取り込み時の読み込み）と `evals/`（corpus ハッシュ算出・データセットの path 実在検証）のみ。
- **Google Drive API へのアクセスは `ingestion/` のみ**（M9。ローカル FS アクセスと同じ局所化ルールを適用する。`docs/specs/26071710-m9_google_drive_ingestion/spec.md`）。

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
  - 例: `db_design.md`, `ingest_runs.py`
  - 例外: フレームワーク側の規約で固定されるもの（`README.md`, `AGENTS.md`, Next.js の予約ファイル名等）はそのまま使う。
  - `docs/specs/` 配下のディレクトリ名（`YYMMDDHH-<spec_name>`）は下記「スペックの配置ルール」の例外として **`-` 区切りの日時プレフィックス**を使う。ディレクトリ内のファイル名（`spec.md`/`tasklist.md`）自体はスネークケースの通常ルールに従う。

### スペックの配置ルール

`docs/specs/` 配下は、マイルストーン／機能ごとに1ディレクトリを作り、その中に `spec.md`（スペック本体）と `tasklist.md`（タスクリスト）を置く。

```
docs/specs/YYMMDDHH-<spec_name>/spec.md
docs/specs/YYMMDDHH-<spec_name>/tasklist.md
```

- `YYMMDDHH`: **スペック本体（`spec.md`）を作成した日時**（西暦下2桁・月・日・時、24時間表記）。`git log --follow --diff-filter=A --format=%ad --date=format:%y%m%d%H -- <path>` 等で当時のコミット日時を確認できる。
- `<spec_name>`: スペックの内容を表すスネークケースの識別子（例: `m0_walking_skelton`, `m9_google_drive_ingestion`）。マイルストーン番号を含める場合は先頭に `mN_` を付ける。
- タスクリストは対応するスペックと同じディレクトリに `tasklist.md` として置く（別日時プレフィックスは作らない。スペック本体の日時に揃える）。
- スペック本体・タスクリスト以外の付随ドキュメント（レビュー記録等）を同じディレクトリに置く場合は、`spec.md`/`tasklist.md` と衝突しない元の名前のまま置く（例: `docs/specs/26070811-m4_ingestion_and_demo/m4_bugfix_and_refactor.md`）。
- 例: `docs/specs/26070714-m0_walking_skelton/spec.md`, `docs/specs/26071710-m9_google_drive_ingestion/tasklist.md`

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
- **Eval CI は再現経路をたどる**（M3 以降）: DB 起動 → `make migrate` → `make ingest`（seed）→ `make eval` → committed baseline と比較。ゲートは**検索指標=ハード / 生成指標=ソフト**（詳細: `docs/specs/26070805-m3_eval_expansion/spec.md` §7）。
- **（M7）THETA・rewrite プロンプト・grade ロジックを変更したら `make eval-routing` を必須とする**（grounded/direct の経路判定精度への影響を確認するため。合格基準は `docs/specs/26071422-m7_adaptive_routing/spec.md` §7.2。初期THETAはcalibration splitでのgrid search（grounded見逃し率≤0.05を制約に direct適中を最大化）により0.56に決定した。根拠データ: `backend/evals/reports/m7-score-distribution.md`）。
- **（M7）grounded / direct プロンプトを変更したら `make eval` を必須とする**（`make eval` は生成に実際に使われる grounded プロンプトを経由するため、プロンプト変更の影響がここに現れる。詳細: `docs/specs/26071422-m7_adaptive_routing/spec.md` §7.3）。**現状の例外:** Voyage/OpenAI 無支払い枠のレート制限により `make eval` が完走できないことがある（詳細は `docs/specs/26071422-m7_adaptive_routing/tasklist.md` T3/T4 完了条件を参照）。この制約が解消するまで、M7 の各タスク完了条件からは `make eval` 実行が除外されている。generate 品質の非劣化確認は、代わりに direct groundedness eval・補足書式検証（LLM-as-judge + 人手裁定）と手動スモークテストで担保する。

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
- ❌ ジョブキュー（Redis/ARQ/Celery 等)を導入する（v1 は CLI + BackgroundTasks で足りる設計判断済み）。**例外: M9 の Google Drive 取り込みは、API 経由トリガのみプロセス非依存の再試行を目的に ARQ/Redis を使用する（CLI 経由トリガ・ローカル取り込みは引き続き同期実行/BackgroundTasks のまま）。`docs/specs/26071710-m9_google_drive_ingestion/spec.md` §3.3 参照**
- ❌ チャットの基本 UX（streaming / auto-scroll / retry / thread 管理）を自前で再実装する（assistant-ui のコンポーネント・ランタイムを使う）

---

## 12. スペック駆動開発について

- 実装の前に `docs/specs/` の該当仕様を読み、必要なら**先に仕様を更新**してから実装する。
- **各マイルストーンの実装に着手する前に、フィーチャースペックのタスク分解を `docs/specs/YYMMDDHH-<spec_name>/tasklist.md`（配置ルールは §6「スペックの配置ルール」参照。例: `docs/specs/26070714-m0_walking_skelton/tasklist.md`）として作成する**。チェックボックス形式で進捗を管理し、実装はこのタスクリストの順に進める。順序は「スペック本体（`spec.md`）→ タスクリスト（`tasklist.md`）→ 実装」を守る。
- 仕様と実装が食い違ったら、**勝手に実装側へ寄せず**、差分を指摘して合意を取る。
- 大きな設計判断は仕様に根拠を残す（後からレビュアーが辿れるように）。

---

## 変更履歴

変更の経緯・判断根拠は `docs/decisions.md`、詳細な変更点は `git log -- AGENTS.md` を参照。現行 v0.17（2026-07-21）: `docs/specs/` の配置ルールを `docs/specs/<milestone>_*.md` から `docs/specs/YYMMDDHH-<spec_name>/{spec.md,tasklist.md}` に変更（§6）。