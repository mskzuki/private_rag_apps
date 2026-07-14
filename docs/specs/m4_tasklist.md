# M4 タスクリスト (m4_tasklist.md)

> 配置先: `docs/specs/m4_tasklist.md`
> 対応スペック: `docs/specs/m4_ingestion_and_demo.md`（v0.2、以下「スペック」）
> 進め方: 上から順に実施。各タスクに対応スペックの節番号を付記。
> 各フェーズ末尾で `make lint` / `make test` を通す。取り込みロジックは純粋部分（判定・安全弁・stats）をユニット、DB 反映を統合で検証する（AGENTS.md §8）。

---

## Phase 0 — 準備・方針確定（スペック §13 未決事項）

- [x] スペック §13「未決事項」を確認し、着手前に以下を決定する
  - [x] `content_hash` を生バイトにするか正規化後にするか（既定: 生バイト。§4.1）→ **デフォルト維持**（`loader.py`で生バイトSHA256実装済み）
  - [x] 安全弁発動時に実行全体を error にするか、削除フェーズのみ中断か（既定: 削除フェーズのみ。§4.3）→ **デフォルト維持**
  - [x] 15 分達成のためビルド済み DB イメージを publish するか（§7.2）→ **Phase 7に据え置き**
  - [x] `INGEST_DELETE_GUARD_RATIO`(0.5) / `INGEST_STALE_RUNNING_SEC` の暫定値 → `core/config.py`に暫定値（0.5 / 600秒）としてコメント付きで記録
- [x] 決定に伴うスペック差分があれば、実装前にスペックを更新する（AGENTS.md §12）→ **デフォルト維持のためスペック差分なし**

---

## Phase 1 — docker-compose + pg_bigm 入り Postgres（スペック §7.1）

> M3 の Eval CI も同じ DB を使うため最優先。ここが通らないと `CREATE EXTENSION` もデモも CI も動かない。

- [x] **pgvector + pg_bigm を両方インストールした Postgres イメージ**を用意（Dockerfile: pgvector ベースに pg_bigm をビルド/インストール、または postgres ベースに両方）→ `backend/docker/db/Dockerfile.local`で既に実装済み（検証のみ実施）
- [x] `docker-compose.yml` の db サービスをそのイメージに切り替え → 既に切り替え済み
- [x] クリーン起動で 0001_init の `CREATE EXTENSION pgcrypto/vector/pg_bigm` が通ることを確認（db_design §2）→ 稼働中DBで`pg_extension`に`pgcrypto/vector/pg_bigm`が存在することを確認
- [x] **`make migrate` がクリーン DB に対して成功する**ことをゲートとして確認（拡張→全テーブル→インデックスまで到達。全フェーズの統合テスト/デモの土台。M4 は新規マイグレーション追加なし）→ `alembic current`が head(0003)であることを確認、`chunks_content_bigm`（gin_bigm_ops）インデックスの存在も確認
- [ ] （Phase 0 の決定次第）ビルド済みイメージの publish 手順を用意 → Phase 7に据え置き
- [x] `.env.example` の**雛形**を用意（必須キー `OPENAI_API_KEY`/`VOYAGE_API_KEY`/`DATABASE_URL`/`CORPUS_DIR`、`LANGFUSE_*` は任意と明記）。**`INGEST_*` 系の追記と最終化は Phase 7** — **M5で完了**: `core/config.py` の全設定キー（retrieval/chat・streaming/evaluation/ingestion 各ブロック、計16項目）を `.env.example` に追記済み

---

## Phase 2 — 増分再取り込みロジック（スペック §4）

- [x] コーパス走査（`CORPUS_DIR` 配下の `.md`/`.txt` 再帰。対応形式外・読込不能は skip 分類。§4.5）→ `loader.py`で実装済み（変更なし）
- [x] `content_hash` 計算と変更判定（無変更 skip / 変更・新規 → 更新対象。§4.1）→ `ingestion/diff.py: classify()`
- [x] **soft-delete 済み path の復活経路**（既存削除済み行を path で引き当て、無変更＝`deleted_at` 解除のみ / 変更＝`deleted_at` 解除 + 全置換 / 生存＝通常判定 / 完全新規＝INSERT。§4.1）→ `diff.py: Action.REVIVE_ONLY` + `indexer.py: _process_one`
- [x] **埋め込みを全チャンク分そろえてから**（トランザクション外・バッチ）実行（§4.2、`INGEST_EMBED_BATCH_SIZE`）。**embed 呼び出しの Langfuse トレースを同時に配線**（後付けにしない＝AGENTS.md §7。可視化確認は Phase 6）→ `indexer.py: _embed_documents`（`@observe`維持）
- [x] **全置換を短いトランザクション**（当該 source の chunks delete + insert を 1 トランザクション。埋め込み失敗時は DB を触らず古い chunks 維持。§4.2）→ `indexer.py: _process_one`（REPLACE分岐）
- [x] 削除反映（走査で消えた source に `deleted_at`。§4.3）→ `indexer.py: _apply_deletions`
- [x] **削除安全弁**（分母=生存 source 数、`INGEST_DELETE_GUARD_RATIO` 未満/走査 0 件で削除フェーズ中断・`ingest_runs.error` 記録。`FORCE_DELETE` でバイパス。§4.3）→ `diff.py: should_apply_deletions`
- [x] **多重実行の抑止**: `running` 行の存在で拒否（実行中ずっと有効）+ 「running 確認→INSERT」を advisory lock で原子化（開始の race のみ）（§4.4）→ `ingestion/concurrency.py: start_run`
      - ※ **実タイミング（API レスポンス後に BackgroundTasks が走る間の重複）の検証は Phase 3** で行う。Phase 2 では start/finish で `ingest_runs` の状態遷移と advisory lock ヘルパを提供するところまで実施済み
- [x] **stale running の回収**（`started_at` 古い & `finished_at IS NULL` を開始時に `error` へ回収。`INGEST_STALE_RUNNING_SEC`。§4.4）→ `concurrency.py: reap_stale_running`
- [x] `ingest_runs.stats` 記録（`{added, updated, deleted, skipped, failed_files}` + `trigger`/`status`）と**逐次 UPDATE**（`INGEST_STATS_FLUSH_EVERY`。§4.5/§5.2）→ `indexer.py: _flush_stats`
- [x] **CLI エントリポイント**（`cli/` の薄い層。`ingestion` サービスを呼ぶ。AGENTS.md §3）: `make ingest CORPUS=...`（`trigger='cli'`）/ `make demo`（`trigger='demo'`）/ **`FORCE_DELETE=1`**（安全弁バイパス。§4.3）を配線 → `cli/main.py`に`--trigger`/`--force-delete`追加、Makefile更新
- [x] `core/config.py` に増分系設定を追加（`INGEST_DELETE_GUARD_RATIO` / `INGEST_ADVISORY_LOCK_KEY` / `INGEST_STALE_RUNNING_SEC` / `INGEST_STATS_FLUSH_EVERY` / `INGEST_EMBED_BATCH_SIZE`。§10。ハードコード禁止）
- [ ] **（v0.6 追記）embed呼び出しのペーシング**（スペック §4.2/§10 v0.4）: `core/config.py` に `ingest_embed_min_interval_sec: float = 21.0` を追加。`indexer.py: _embed_documents` にVoyage呼び出し間の最低間隔待機を実装（同一source内バッチ間・source間の両方に効かせる）。ユニットテスト（`time.sleep`をmonkeypatchし実APIを叩かず間隔を検証）を `tests/test_ingestion_indexer.py` に追加
- [x] ユニットテスト（純粋部分）: content_hash 判定（無変更/変更/新規/復活）、削除安全弁のしきい値境界、スキップ分類、stats 集計 → `tests/test_ingestion_diff.py`
- [x] 統合テスト（テスト DB・CLI 同期経路）: 全置換の原子性（更新中の検索が 0 件中間状態を見ない／埋め込み失敗で古い chunks 残存）、削除反映後に `retrieval` 対象外化、**復活**（無変更＝再埋め込みなし/変更＝全置換）、**stale running 回収で再実行可**、`FORCE_DELETE` で安全弁バイパス → `tests/test_ingestion_indexer.py` + `tests/test_ingestion_concurrency.py`
      - ※ **running 排他が「実行中ずっと効く」検証は Phase 3**（BackgroundTasks 経路）で実施（今回は同期経路の拒否のみ検証済み）

---

## Phase 3 — コーパス管理 API（スペック §5.1〜§5.3）

- [x] `GET /api/sources`（path/title/**チャンク数(GROUP BY 集計で N+1 回避)**/最終取り込み日時/deleted_at。**`?include_deleted=true` で削除済みも返す**クエリパラメータ受け口。§5.1）→ `api/main.py: list_sources`
- [x] `POST /api/ingest`（BackgroundTasks 起動 → `ingest_run` id を即返す。`running` があれば 409 相当で拒否。§5.2）→ `api/main.py: trigger_ingest`。`start_run`をリクエストハンドラで同期実行しrun idを即返し、実処理は`_run_ingest_in_background`（独立したSessionを使用）へ委譲するよう`indexer.py`を`start_run`/`execute_ingestion`に分割
- [x] `GET /api/ingest/runs`（履歴・進行状態。`stats` の逐次更新を反映。§5.2）→ `api/main.py: list_ingest_runs`
- [x] `DELETE /api/index`（sources/chunks のみ削除・**会話は保持**・**取り込み中は拒否**・アプリログに記録。§5.3）→ `api/main.py: reset_index`（`reap_stale_running`も呼び出し、stale running行が誤って初期化をブロックしないようにした）
- [x] 統合テスト（★BackgroundTasks 実タイミング）: `POST /api/ingest` が id を返した**後にワーカーが走っている間**に 2 個目の `POST /api/ingest` が拒否される（running 排他が実行中ずっと効く。スペック §4.4 の設計はこの経路で初めて検証できる）→ `tests/test_ingest_api.py: test_second_post_ingest_rejected_while_first_still_processing`（threadingで実タイミングを再現）
- [x] ユニット/統合テスト: 初期化後に会話が残る、取り込み中の初期化が拒否される、`include_deleted` の出し分け → `tests/test_ingest_api.py`

---

## Phase 4 — データ管理 UI（スペック §5.4）

- [x] ソース一覧画面（path/title/チャンク数/最終取り込み日時、削除済みトグル）→ `frontend/src/app/sources/page.tsx`
- [x] 再取り込みボタン → `POST /api/ingest` → `runs` ポーリングで進捗（running/success/error + stats）表示、実行中はボタン無効化 → 同ページ内`handleReingest`/`startPolling`
- [x] インデックス初期化ボタン → 確認ダイアログ → `DELETE /api/index` → `AlertDialog`（shadcn）で確認後`handleReset`
- [x] 管理画面は素の Next.js（shadcn/ui）で実装（assistant-ui はチャット用。§5.4）→ `table`/`alert-dialog`/`badge`をshadcn CLIで追加、`frontend/src/lib/sources-api.ts`にAPIクライアント。Playwrightでブラウザ実動作を確認済み（一覧表示・削除済みトグル・再取り込み中の409エラー表示・初期化確認ダイアログの成功/失敗両経路）

---

## Phase 5 — デモモード仕上げ + seed（スペック §6、M3 結合）

- [x] `make demo` を 1 コマンド化（`docker compose up -d db` → `make migrate` → `make ingest`(seed, trigger='demo') → 起動案内。§6.1）→ Makefileの`demo`ターゲットで`migrate`+`ingest --trigger demo`を実行
- [x] **`make demo` は `CORPUS_DIR=seed/` として動く**よう Makefile を構成（§6.1）→ `demo`ターゲットに`CORPUS_DIR=seed/corpus`を明示指定し、`.env`の個人用CORPUS_DIR設定を上書き
  - ★副次的に発見・修正した不具合: `backend/seed/`は未追跡の空ディレクトリで、ローカル（非Docker）実行時に`cd backend && ...`のcwdから相対パス`seed/corpus`を解決すると存在しないパスになっていた（Dockerでは`docker-compose.yml`の`./seed:/app/seed`マウントで偶然動いていたのみ）。`backend/seed`を`../seed`へのシンボリックリンクに変更し、ローカル実行でも実体のseedコーパスに解決されるようにした（Docker側は volume mount がシンボリックリンクを上書きするため引き続き問題なし）
- [x] seed コーパスを仕上げ（日本語含む現実的構成・実データ非混入=NFR-3。§6.2）→ 既存の4ファイル（AGENTS.md/architecture.md/db_design.md/requirements.md、いずれも日本語の実質的なプロジェクト文書）で既に充足していることを確認。個人の実データは含まれない
- [x] **seed 変更差分を洗い出す**（追加/リネーム/削除された path の一覧化）→ 今回のセッションでseedのpath自体は変更していないため差分なし
- [x] **M3 データセットの該当 path を更新**（リネーム/削除で壊れた `relevant` path を修正。§6.2 の seed↔Eval 結合）→ 差分がないため更新不要
- [x] **M3 データセットの path 実在チェックを通す**（M3 §12 のスキーマ検証。seed↔Eval 整合を M4 完了条件に）→ `evals.schema.validate_paths`で31件全データセット項目がseed/corpus配下に実在することを確認
- [x] 2 回目の `make demo` が無変更 skip で速いことを確認（増分の実証。§6.1）→ Voyage embedをモックし実際のseed/corpus(4ファイル)に対して`run_ingestion(trigger='demo')`を2回実行。1回目added=4、2回目skipped=4・embed呼び出しなしを確認

---

## Phase 6 — 取り込みトレースの横断確認（スペック §8）

> embed 呼び出しのトレース配線自体は Phase 2 で実施済み（後付け回避）。本フェーズは可視化と no-op の横断確認に絞る。

- [x] 取り込み実行が 1 トレースにまとまり、source ごとの embed トークン/コスト/レイテンシが記録されることを確認 → **コードレベルで確認**: `execute_ingestion`（`@observe(name="ingest_run")`）が各sourceの処理内で`_embed_documents`（`@observe(name="embed_documents")`）を呼び出す構造になっており、Langfuseのcontextvarベースの入れ子トレースにより1 run=1トレース、source（実際にはバッチ）ごとに子spanとして記録される。CLI同期経路・API BackgroundTasks経路（別スレッド）のいずれも、そのスレッド内で新規トレースが開始されるため正しく機能する。**トークン/コストの自動記録は既存の`retrieval/searcher.py: _embed_query`と同水準**（Voyageのレスポンスから明示的なusage_details記録はしておらず、rerank呼び出し（`_rerank`）のみ`update_current_generation`で明示記録している既存の非対称性を踏襲）。実際のLangfuseダッシュボードでの可視化は**本環境に実クレデンシャルが無いため未実施** — 実クレデンシャル設定後に一度目視確認を推奨
- [x] skip の効果（今回実際に埋め込んだ分だけコストが出る）が Langfuse で可視化されることを確認 → コードレベルでは、SKIP/REVIVE_ONLY分岐は`_embed_documents`を呼ばないため該当sourceの子spanが生成されない設計になっており、skip効果は構造的にトレースへ反映される。ダッシュボードでの目視確認は上記と同様未実施
- [x] `LANGFUSE_*` 未設定時に no-op で完動することを確認（§7.3/§8）→ Phase 5の検証（`LANGFUSE_PUBLIC_KEY`未設定のまま`run_ingestion`を実データのseed/corpusに対し2回実行）で実際に確認済み。「Authentication error: ... Client will be disabled」という警告が出るのみで、処理自体は正常にadded=4→skipped=4まで完走した

---

## Phase 7 — 15 分クイックスタート実測 + README（スペック §7.2, §6.3）

- [x] **クリーン環境**（キャッシュ無し）で `git clone → docker compose up → make demo` を実測し **15 分以内**を確認（NFR-8）→ **部分実測**: `docker compose build --no-cache db`（pg_bigmソースビルド込み）= 約27秒、`docker compose up -d db`起動 = 数秒、`make migrate`（既存スキーマへの再適用）= 1秒未満。これらは十分15分に収まる規模と確認。**M5追記（2026-07-13）**: 実 `OPENAI_API_KEY`/`VOYAGE_API_KEY` を用いて `make demo` の実埋め込み呼び出しを実行し、seedコーパス4ファイルの索引化に成功した。ただし利用したVoyageアカウントが無料枠（支払い方法未登録・3RPM上限）だったため、埋め込み呼び出しが `RateLimitError` で失敗する実バグ（`voyageai.Client()` の `max_retries` が既定で0＝リトライ無効だった）を発見・修正した（`core/config.py` に `voyage_max_retries` 設定を追加、`retrieval/searcher.py`・`ingestion/indexer.py` の全Client生成箇所に適用）。この修正後は安定して完走する。なお本セッションはDocker/uv/pnpmのキャッシュが温まった環境での実測であり、真にキャッシュ無しの初回ダウンロード時間・第三者/別マシンでの実測はまだ行っていない（推奨事項として残す）
  - ★副次的に発見・修正したバグ: `make setup`はAGENTS.md §5の記載（uv sync+pnpm install+.env生成+DB起動）と異なり、実装は`uv sync`のみだった。フルセットアップを行うよう修正（Makefile）
- [ ] **超過時の分岐**: ビルド（pg_bigm 含む）が支配的なら Phase 0 の決定に戻り、**publish 済み DB イメージ利用に切替**て再計測（§7.2）→ 上記の通りpg_bigmビルドは約27秒と軽量なため、現時点では不要と判断。真の15分超過が実測で確認された場合のみ対応
- [x] **`.env.example` の最終化**（Phase 2 で追加した `INGEST_*` 系を含め全設定キーを反映）→ `backend/.env.example`にINGEST_*系（コメントアウトで既定値を明記）とセクションコメントを追加
- [x] README クイックスタート**本文**（コマンド列・必要キー・所要時間・CORPUS_DIR 差し替え手順）を用意（GIF/図は M5。§6.3）→ `README.md`のクイックスタートを`make setup`の実際の挙動に合わせて更新し、データ管理UIへの導線とCORPUS_DIR差し替え手順を追記

---

## Phase 8 — 仕上げ・受け入れ確認（スペック §11, §13）

- [x] スペック §11 の受け入れ条件をすべてチェック（増分/管理API・UI/デモ・再現性/共通）→ `m4_ingestion_and_demo.md` §11 を更新。15分クイックスタートの項目のみ、実Voyageキー不在のため部分実測に留まる（要ユーザー側最終実測）
- [x] 上位ドキュメント反映（§13）:
  - [x] `db_design.md`（running 行 + advisory lock の排他・stale 回収・削除安全弁・復活経路の注記）→ §7に追記、変更履歴 v0.3
  - [x] `architecture.md` §6/§10（埋め込み事前・短トランザクション全置換・削除安全弁・`DELETE /api/index` スコープ）→ 追記、変更履歴 v0.5
  - [x] `architecture.md` §1 / `AGENTS.md` §4（`docker-compose.yml` の pg_bigm 入りイメージ）→ 追記、AGENTS.md変更履歴 v0.8
  - [ ] `requirements.md` §9/FR-7（seed 確定と Eval データセットの整合チェックを M4 完了条件に）→ **未反映**。`docs/requirements.md`は本セッション開始前から別件（v0.4→v0.5化）の未コミット編集が進行中であったため、競合を避けて今回は変更を見送った。同内容は本スペック（§6.2/§13）に既に記載済み。requirements.md側の反映はその編集が確定した後に別途行うこと
- [x] `make lint` / `make test` が通ることを最終確認 → backend 62 test全通過、ruff/mypy共にクリーン（`evals/`配下の既存5件のlint警告は本セッション対象外・未変更）。frontend `pnpm lint`/`pnpm fmt:check`とも exit 0（shadcn生成ファイルの警告2件は既存分・対象外）
- [ ] 対応 PR に要件 ID（FR-1/FR-2/FR-7/FR-8/NFR-8）を記載（AGENTS.md §9）→ PR作成はユーザー側の操作のため対象外

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.6 | 2026-07-14 | スペック`m4_ingestion_and_demo.md` v0.4（embed呼び出しペーシング追記）に対応するタスクをPhase 2に追加。ライブ実行でVoyage無支払い枠のRPM上限により発生した取り込み部分失敗を受けた追記 |
| v0.5 | 2026-07-11 | Phase 4〜8 実装完了を反映しM4を完了扱いとする（requirements.md反映のみ後日対応）。Phase 4: データ管理UI（`frontend/src/app/sources/page.tsx`、shadcn `table`/`alert-dialog`/`badge`追加）をPlaywrightでブラウザ実動作確認。Phase 5: `make demo`のCORPUS_DIR固定化、seed↔M3データセットの整合確認（差分なし）。副次的に`backend/seed`が未追跡の空ディレクトリでローカル実行時にコーパスパスが解決できないバグを発見し、`../seed`へのシンボリックリンクに修正。Phase 6: Langfuseトレース配線をコードレベルで確認（実クレデンシャル無しのためダッシュボード目視は未実施）。Phase 7: `make setup`がAGENTS.md記載の内容(uv sync+pnpm install+.env生成+DB起動)を実装していなかったバグを修正。pg_bigmビルド+DB起動+migrateの部分実測(合計1分未満)、`.env.example`最終化、READMEクイックスタート更新（実VOYAGE_API_KEY不在のためフル実測は未了、ユーザー側での最終実測を推奨）。Phase 8: db_design.md/architecture.md/AGENTS.mdへ反映。requirements.mdは別件の未コミット編集と競合するため見送り |
| v0.4 | 2026-07-11 | Phase 3 実装完了を反映。`indexer.py`の`run_ingestion`を`start_run`+`execute_ingestion`に分割し、APIハンドラで`start_run`を同期実行してrun idを即返しつつ実処理をBackgroundTasks（独立Session）へ委譲する構成にした。`GET /api/sources`・`POST /api/ingest`・`GET /api/ingest/runs`・`DELETE /api/index`を実装、threadingを使った実タイミングの多重起動拒否テストを含め`tests/test_ingest_api.py`を追加。Phase 4以降は未着手 |
| v0.3 | 2026-07-11 | Phase 0〜2 実装完了を反映。Phase 0 は既定値維持で確認済み（スペック差分なし）。Phase 1 はDockerfile.local/docker-compose.ymlが既に整備済みのため検証のみ実施（pg_bigm拡張・head migration・bigmインデックスを確認）。Phase 2 は `ingestion/diff.py`（classify/should_apply_deletions）・`ingestion/concurrency.py`（start_run/reap_stale_running）を新規追加し、`indexer.py`を増分ロジック対応に書き換え。`core/config.py`にINGEST_*設定を追加、CLIに`--trigger`/`--force-delete`、Makefileの`demo`ターゲットにtrigger='demo'を配線。Phase 3以降は未着手 |
| v0.2 | 2026-07-08 | セルフレビュー反映: (1) Phase 1 に **`make migrate` クリーン成功ゲート**を追加。(2) Phase 2 に **CLI エントリポイント**（`make ingest`/`make demo`/`FORCE_DELETE`・trigger 出し分け）と **embed トレース同時配線**を追加。(3) running 排他の**実タイミング検証を Phase 3（BackgroundTasks 経路）に委譲**、Phase 2 はヘルパ提供まで。(4) Phase 5 の seed↔M3 を「差分洗い出し→**M3 データセット更新**→検証」に分解。(5) minor: `GET /api/sources` の `include_deleted` パラメータ、Phase 6 をトレース横断確認に縮小、Phase 7 に 15 分超過時の分岐と `.env.example` 最終化 |
| v0.1 | 2026-07-08 | 初版。m4_ingestion_and_demo.md v0.2 §14 の実装順序に基づき Phase 0〜8 を作成。pg_bigm イメージを Phase 1 に前倒し（M3 CI と兼用）。増分は復活経路・埋め込み事前/短トランザクション全置換・削除安全弁+FORCE_DELETE・running 排他+stale 回収・stats 逐次更新を反映。seed↔M3 の path 整合チェックを Phase 5 の完了条件に |