# M4 タスクリスト (m4_tasklist.md)

> 配置先: `docs/specs/m4_tasklist.md`
> 対応スペック: `docs/specs/m4_ingestion_and_demo.md`（v0.2、以下「スペック」）
> 進め方: 上から順に実施。各タスクに対応スペックの節番号を付記。
> 各フェーズ末尾で `make lint` / `make test` を通す。取り込みロジックは純粋部分（判定・安全弁・stats）をユニット、DB 反映を統合で検証する（AGENTS.md §8）。

---

## Phase 0 — 準備・方針確定（スペック §13 未決事項）

- [ ] スペック §13「未決事項」を確認し、着手前に以下を決定する
  - [ ] `content_hash` を生バイトにするか正規化後にするか（既定: 生バイト。§4.1）
  - [ ] 安全弁発動時に実行全体を error にするか、削除フェーズのみ中断か（既定: 削除フェーズのみ。§4.3）
  - [ ] 15 分達成のためビルド済み DB イメージを publish するか（§7.2）
  - [ ] `INGEST_DELETE_GUARD_RATIO`(0.5) / `INGEST_STALE_RUNNING_SEC` の暫定値
- [ ] 決定に伴うスペック差分があれば、実装前にスペックを更新する（AGENTS.md §12）

---

## Phase 1 — docker-compose + pg_bigm 入り Postgres（スペック §7.1）

> M3 の Eval CI も同じ DB を使うため最優先。ここが通らないと `CREATE EXTENSION` もデモも CI も動かない。

- [ ] **pgvector + pg_bigm を両方インストールした Postgres イメージ**を用意（Dockerfile: pgvector ベースに pg_bigm をビルド/インストール、または postgres ベースに両方）
- [ ] `docker-compose.yml` の db サービスをそのイメージに切り替え
- [ ] クリーン起動で 0001_init の `CREATE EXTENSION pgcrypto/vector/pg_bigm` が通ることを確認（db_design §2）
- [ ] **`make migrate` がクリーン DB に対して成功する**ことをゲートとして確認（拡張→全テーブル→インデックスまで到達。全フェーズの統合テスト/デモの土台。M4 は新規マイグレーション追加なし）
- [ ] （Phase 0 の決定次第）ビルド済みイメージの publish 手順を用意
- [ ] `.env.example` の**雛形**を用意（必須キー `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY`/`DATABASE_URL`/`CORPUS_DIR`、`LANGFUSE_*` は任意と明記）。**`INGEST_*` 系の追記と最終化は Phase 7**（設定キーが出そろってから）

---

## Phase 2 — 増分再取り込みロジック（スペック §4）

- [ ] コーパス走査（`CORPUS_DIR` 配下の `.md`/`.txt` 再帰。対応形式外・読込不能は skip 分類。§4.5）
- [ ] `content_hash` 計算と変更判定（無変更 skip / 変更・新規 → 更新対象。§4.1）
- [ ] **soft-delete 済み path の復活経路**（既存削除済み行を path で引き当て、無変更＝`deleted_at` 解除のみ / 変更＝`deleted_at` 解除 + 全置換 / 生存＝通常判定 / 完全新規＝INSERT。§4.1）
- [ ] **埋め込みを全チャンク分そろえてから**（トランザクション外・バッチ）実行（§4.2、`INGEST_EMBED_BATCH_SIZE`）。**embed 呼び出しの Langfuse トレースを同時に配線**（後付けにしない＝AGENTS.md §7。可視化確認は Phase 6）
- [ ] **全置換を短いトランザクション**（当該 source の chunks delete + insert を 1 トランザクション。埋め込み失敗時は DB を触らず古い chunks 維持。§4.2）
- [ ] 削除反映（走査で消えた source に `deleted_at`。§4.3）
- [ ] **削除安全弁**（分母=生存 source 数、`INGEST_DELETE_GUARD_RATIO` 未満/走査 0 件で削除フェーズ中断・`ingest_runs.error` 記録。`FORCE_DELETE` でバイパス。§4.3）
- [ ] **多重実行の抑止**: `running` 行の存在で拒否（実行中ずっと有効）+ 「running 確認→INSERT」を advisory lock で原子化（開始の race のみ）（§4.4）
      - ※ **実タイミング（API レスポンス後に BackgroundTasks が走る間の重複）の検証は Phase 3** で行う。Phase 2 では start/finish で `ingest_runs` の状態遷移と advisory lock ヘルパを提供するところまで
- [ ] **stale running の回収**（`started_at` 古い & `finished_at IS NULL` を開始時に `error` へ回収。`INGEST_STALE_RUNNING_SEC`。§4.4）
- [ ] `ingest_runs.stats` 記録（`{added, updated, deleted, skipped, failed_files}` + `trigger`/`status`）と**逐次 UPDATE**（`INGEST_STATS_FLUSH_EVERY`。§4.5/§5.2）
- [ ] **CLI エントリポイント**（`cli/` の薄い層。`ingestion` サービスを呼ぶ。AGENTS.md §3）: `make ingest CORPUS=...`（`trigger='cli'`）/ `make demo`（`trigger='demo'`）/ **`FORCE_DELETE=1`**（安全弁バイパス。§4.3）を配線
- [ ] `core/config.py` に増分系設定を追加（`INGEST_DELETE_GUARD_RATIO` / `INGEST_ADVISORY_LOCK_KEY` / `INGEST_STALE_RUNNING_SEC` / `INGEST_STATS_FLUSH_EVERY` / `INGEST_EMBED_BATCH_SIZE`。§10。ハードコード禁止）
- [ ] ユニットテスト（純粋部分）: content_hash 判定（無変更/変更/新規/復活）、削除安全弁のしきい値境界、スキップ分類、stats 集計
- [ ] 統合テスト（テスト DB・CLI 同期経路）: 全置換の原子性（更新中の検索が 0 件中間状態を見ない／埋め込み失敗で古い chunks 残存）、削除反映後に `retrieval` 対象外化、**復活**（無変更＝再埋め込みなし/変更＝全置換）、**stale running 回収で再実行可**、`FORCE_DELETE` で安全弁バイパス
      - ※ **running 排他が「実行中ずっと効く」検証は Phase 3**（BackgroundTasks 経路）で実施

---

## Phase 3 — コーパス管理 API（スペック §5.1〜§5.3）

- [ ] `GET /api/sources`（path/title/**チャンク数(GROUP BY 集計で N+1 回避)**/最終取り込み日時/deleted_at。**`?include_deleted=true` で削除済みも返す**クエリパラメータ受け口。§5.1）
- [ ] `POST /api/ingest`（BackgroundTasks 起動 → `ingest_run` id を即返す。`running` があれば 409 相当で拒否。§5.2）
- [ ] `GET /api/ingest/runs`（履歴・進行状態。`stats` の逐次更新を反映。§5.2）
- [ ] `DELETE /api/index`（sources/chunks のみ削除・**会話は保持**・**取り込み中は拒否**・アプリログに記録。§5.3）
- [ ] 統合テスト（★BackgroundTasks 実タイミング）: `POST /api/ingest` が id を返した**後にワーカーが走っている間**に 2 個目の `POST /api/ingest` が拒否される（running 排他が実行中ずっと効く。スペック §4.4 の設計はこの経路で初めて検証できる）
- [ ] ユニット/統合テスト: 初期化後に会話が残る、取り込み中の初期化が拒否される、`include_deleted` の出し分け

---

## Phase 4 — データ管理 UI（スペック §5.4）

- [ ] ソース一覧画面（path/title/チャンク数/最終取り込み日時、削除済みトグル）
- [ ] 再取り込みボタン → `POST /api/ingest` → `runs` ポーリングで進捗（running/success/error + stats）表示、実行中はボタン無効化
- [ ] インデックス初期化ボタン → 確認ダイアログ → `DELETE /api/index`
- [ ] 管理画面は素の Next.js（shadcn/ui）で実装（assistant-ui はチャット用。§5.4）

---

## Phase 5 — デモモード仕上げ + seed（スペック §6、M3 結合）

- [ ] `make demo` を 1 コマンド化（`docker compose up -d db` → `make migrate` → `make ingest`(seed, trigger='demo') → 起動案内。§6.1）
- [ ] **`make demo` は `CORPUS_DIR=seed/` として動く**よう Makefile を構成（§6.1）
- [ ] seed コーパスを仕上げ（日本語含む現実的構成・実データ非混入=NFR-3。§6.2）
- [ ] **seed 変更差分を洗い出す**（追加/リネーム/削除された path の一覧化）
- [ ] **M3 データセットの該当 path を更新**（リネーム/削除で壊れた `relevant` path を修正。§6.2 の seed↔Eval 結合）
- [ ] **M3 データセットの path 実在チェックを通す**（M3 §12 のスキーマ検証。seed↔Eval 整合を M4 完了条件に）
- [ ] 2 回目の `make demo` が無変更 skip で速いことを確認（増分の実証。§6.1）

---

## Phase 6 — 取り込みトレースの横断確認（スペック §8）

> embed 呼び出しのトレース配線自体は Phase 2 で実施済み（後付け回避）。本フェーズは可視化と no-op の横断確認に絞る。

- [ ] 取り込み実行が 1 トレースにまとまり、source ごとの embed トークン/コスト/レイテンシが記録されることを確認
- [ ] skip の効果（今回実際に埋め込んだ分だけコストが出る）が Langfuse で可視化されることを確認
- [ ] `LANGFUSE_*` 未設定時に no-op で完動することを確認（§7.3/§8）

---

## Phase 7 — 15 分クイックスタート実測 + README（スペック §7.2, §6.3）

- [ ] **クリーン環境**（キャッシュ無し）で `git clone → docker compose up → make demo` を実測し **15 分以内**を確認（NFR-8）
- [ ] **超過時の分岐**: ビルド（pg_bigm 含む）が支配的なら Phase 0 の決定に戻り、**publish 済み DB イメージ利用に切替**て再計測（§7.2）
- [ ] **`.env.example` の最終化**（Phase 2 で追加した `INGEST_*` 系を含め全設定キーを反映）
- [ ] README クイックスタート**本文**（コマンド列・必要キー・所要時間・CORPUS_DIR 差し替え手順）を用意（GIF/図は M5。§6.3）

---

## Phase 8 — 仕上げ・受け入れ確認（スペック §11, §13）

- [ ] スペック §11 の受け入れ条件をすべてチェック（増分/管理API・UI/デモ・再現性/共通）
- [ ] 上位ドキュメント反映（§13）:
  - [ ] `db_design.md`（running 行 + advisory lock の排他・stale 回収・削除安全弁・復活経路の注記）
  - [ ] `architecture.md` §6/§10（埋め込み事前・短トランザクション全置換・削除安全弁・`DELETE /api/index` スコープ）
  - [ ] `architecture.md` §1 / `AGENTS.md` §4（`docker-compose.yml` の pg_bigm 入りイメージ）
  - [ ] `requirements.md` §9/FR-7（seed 確定と Eval データセットの整合チェックを M4 完了条件に）
- [ ] `make lint` / `make test` が通ることを最終確認
- [ ] 対応 PR に要件 ID（FR-1/FR-2/FR-7/FR-8/NFR-8）を記載（AGENTS.md §9）

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.2 | 2026-07-08 | セルフレビュー反映: (1) Phase 1 に **`make migrate` クリーン成功ゲート**を追加。(2) Phase 2 に **CLI エントリポイント**（`make ingest`/`make demo`/`FORCE_DELETE`・trigger 出し分け）と **embed トレース同時配線**を追加。(3) running 排他の**実タイミング検証を Phase 3（BackgroundTasks 経路）に委譲**、Phase 2 はヘルパ提供まで。(4) Phase 5 の seed↔M3 を「差分洗い出し→**M3 データセット更新**→検証」に分解。(5) minor: `GET /api/sources` の `include_deleted` パラメータ、Phase 6 をトレース横断確認に縮小、Phase 7 に 15 分超過時の分岐と `.env.example` 最終化 |
| v0.1 | 2026-07-08 | 初版。m4_ingestion_and_demo.md v0.2 §14 の実装順序に基づき Phase 0〜8 を作成。pg_bigm イメージを Phase 1 に前倒し（M3 CI と兼用）。増分は復活経路・埋め込み事前/短トランザクション全置換・削除安全弁+FORCE_DELETE・running 排他+stale 回収・stats 逐次更新を反映。seed↔M3 の path 整合チェックを Phase 5 の完了条件に |