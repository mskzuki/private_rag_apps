# M9 タスクリスト: Google Drive フォルダ取り込み (v0.2)

- Spec: `m9_google_drive_ingestion.md`（v0.2）
- Status: Not started
- 実行順序: T0 → T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
- 規約: 各タスクは「完了条件をすべて満たす」まで次に進まない。スコープ外の変更を行わない。判断に迷う点はタスク内の「実装ノート」の範囲でのみ裁量を認め、それ以外はスペックに差し戻す

**2026-07-17 スペックレビューでの決定事項（詳細はスペック v0.2 参照）:**
1. `make worker`（ARQ worker起動）は `make web` と同じくホスト上で直接プロセスを起動する方式とする。docker-compose に専用サービスは追加しない（Redis サービスのみ追加）
2. 新設パッケージ `worker/` は `cli` と同様「`ingestion` を呼ぶ薄い層」（AGENTS.md §3 に反映済み）。ジョブ関数にロジックを持ち込まない
3. 削除安全弁（`INGEST_DELETE_GUARD_RATIO`）とグローバル排他ロック（advisory lock）は、いずれもソース種別（local_fs/google_drive）を混同しないことが重要リスクとしてスペック §8 に明記されている。T4/T5 で明示的にテストすること
4. Google Drive 機能は完全にオプトイン（`DRIVE_FOLDER_ID` 空 = 無効）。`make demo` のクリーンルーム体験（NFR-8）に影響してはならない
5. 実際の GCP サービスアカウント・Drive フォルダを使った手動スモークテストはユーザー環境に依存する。自動化できない場合は T8 で明示的に持ち越しとして記録する（M7 の Voyage/OpenAI ブロッカーと同様の扱い）

---

## T0: 前提確認

**目的:** 実装に着手する前に、スペックが前提とする環境・依存関係が実際に成立するかを確認する。

**作業項目:**
1. 新規依存（`arq`, `google-api-python-client`, `google-auth`, `google-auth-httplib2` 等）が `uv add` で解決可能か確認する（バージョン競合がないか）
2. `backend/alembic/versions/0003_chat_history.py` の `revision` 値を確認し、新規マイグレーション `0004_drive_source_fields.py` の `down_revision` に設定する値を確定する
3. `models/rag.py` の `Source`/`IngestRun`/`Chunk` の現在のフィールド定義を再確認し、スペック §4.2 のマイグレーション DDL と齟齬がないか確認する（特に `Chunk.metadata_` の命名、`Source.source_updated_at` の型）
4. ローカル Redis（docker-compose 経由）が問題なく起動することを確認する

**完了条件:**
- [x] 上記 4 点の確認結果がタスクノートに記録されている
- [x] 依存関係の競合や重大な前提の誤りが見つかった場合、対応方針（スペック修正 or 実装で吸収）をタスクノートに記録してから T1 に進む → 該当なし。4点すべてがスペックの前提と整合していることを確認した（`0003_chat_history.py` docstringの軽微な表記不整合のみ検出したが機能的な問題はなく、T1着手時の申し送り事項として下記2.に記録済み）

**タスクノート（2026-07-17 記録）:**
1. **依存関係:** `backend/` で `uv add --no-sync arq google-api-python-client google-auth google-auth-httplib2` を実行し解決可能性を確認（利用中の `uv 0.11.27` に `--dry-run` フラグが無いため `--no-sync` で解決のみ行った）。**`Resolved 113 packages in 201ms`、エラー・警告なし**。transitiveに追加される新規パッケージは17個（`arq`, `cffi`, `cryptography`, `google-api-core`, `google-api-python-client`, `google-auth`, `google-auth-httplib2`, `hiredis`, `httplib2`, `proto-plus`, `pyasn1`, `pyasn1-modules`, `pycparser`, `pyjwt`, `pyparsing`, `redis`, `uritemplate`）で、既存の `openai`/`voyageai`/`langfuse`/`langgraph`/`sqlalchemy` 等との衝突は無い。確認後 `git checkout -- pyproject.toml` で復元し、`uv.lock`（`.gitignore` で非追跡）はバックアップから復元して作業前の状態に戻した（`git status` clean を確認済み）。
2. **`down_revision`:** `backend/alembic/versions/0003_chat_history.py:14` の `revision: str = '0003'` を確認。よって新規 `0004_drive_source_fields.py` の `down_revision` は **`'0003'`** とする。なお同ファイル冒頭のdocstring（3行目 `Revision ID: a4188e3436db`）は実際に使われる `revision` 変数の値と一致しない古い表記（`alembic revision` 初回生成時のハッシュ値が、手動で連番 `'0003'` に書き換えた後も docstring 側だけ残存したものと推測。`0001`/`0002` の docstring は revision 変数と一致しており `0003` のみの表記ゆれ）。Alembic自体は docstring ではなく `revision`/`down_revision` 変数でチェーンを解決するため機能的な問題はないが、**T1で `0004_drive_source_fields.py` を書く際はこの表記ゆれを踏襲せず、docstring も `Revision ID: 0004` / `Revises: 0003` で実値と揃えること**（軽微な申し送り事項）。
3. **`models/rag.py` とスペック §4.2 の整合性:** 齟齬なし。
   - `Source.source_updated_at`（`models/rag.py:21`）: `Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP(timezone=True))`。DDL上も `timestamptz`（`0001_init.py:33`）で、稼働中の dev DB (`\d sources`) でも `timestamp with time zone` と実測確認済み。§4.2 が想定する「Driveの `modifiedTime` をそのまま格納する」用途と型面で矛盾しない。
   - `Chunk.metadata_`（`models/rag.py:36`）: Python属性名は `metadata_`（SQLAlchemy宣言的Baseが `metadata` を予約しているため）だが、`mapped_column("metadata", JSONB, ...)` によりDBカラム名は `metadata`（稼働中DBの `\d chunks` でも確認）。ただし**スペック §4.2 のDDLは `sources`/`ingest_runs` のみを変更し `chunks` には触れない**（spec本文を grep しても "metadata" の言及なし）ため、M9のマイグレーションには影響しない。将来 `chunks.metadata` に触れる実装が出た場合の命名注意点として記録するのみ。
   - 制約名の実値確認（稼働中 dev DB、`private_rag_apps-db-1` コンテナ、読み取りのみ）: `sources` の `UNIQUE(path)` 制約名は実際に `sources_path_key`（Postgresのデフォルト命名規則どおり）で、§4.2 の `ALTER TABLE sources DROP CONSTRAINT sources_path_key;` はそのまま成立する。`ingest_runs.trigger` のCHECK制約名は `ingest_runs_trigger_check`（値 `cli`/`api`/`demo`）で、§4.2 が「変更しない」とする前提と一致。
4. **Redis起動確認:** `docker-compose.yml` の変更はT5の責務のためT0では変更せず、非破壊的な方法で2通り確認した。(a) `docker run --name m9-t0-redis-check -p 16379:6379 redis:7-alpine`、(b) スクラッチパッドに置いた使い捨て `docker-compose` ファイル（`services: redis: image: redis:7-alpine`、プロジェクト名 `m9-t0-check` で本体のcomposeプロジェクトと分離）。いずれも正常起動し `redis-cli ping` が `PONG` を返した。確認後 `docker compose ... down` / `docker rm -f` でコンテナ・ネットワークを削除し、`git diff docker-compose.yml` が空（無変更）であることを確認済み。ローカル既定ポート `6379` も未使用であることを確認した。

**スコープ外:** 実際の GCP サービスアカウント作成（T2/T8 で必要になった時点で対応）。

---

## T1: データモデル拡張

**目的:** `sources`/`ingest_runs` にソース種別を導入し、Drive ソースの識別キー（`external_id`）を後方互換の形で追加する。

**成果物（スペック §4.2）:**
- `backend/alembic/versions/0004_drive_source_fields.py`
- `models/rag.py` の `Source`/`IngestRun` 更新

**作業項目:**
1. マイグレーション: `source_type`（既定 `local_fs`、CHECK制約）・`external_id`・`source_url` を `sources` に追加し、既存 `UNIQUE(path)` をパーシャルユニークインデックス2本（ローカル: `path`、Drive: `external_id`）に置き換える
2. マイグレーション: `ingest_runs` に `source_type`（既定 `local_fs`、CHECK制約）を追加する。既存 `trigger` CHECK制約は変更しない
3. `models/rag.py` の SQLAlchemy モデルをマイグレーション後のスキーマに同期させる
4. ダウングレード（`downgrade()`）を実装し、ロールバック手順を確認する

**完了条件:**
- [x] `alembic upgrade head` が既存データに対してエラーなく適用できる（既存ローカルソースは全て `source_type='local_fs'` になる） → **達成**（`test_migration_drive_source_fields.py::TestMigrationCycle::test_upgrade_head_marks_existing_local_sources_as_local_fs` で、使い捨てPostgres DBに0003まで適用→旧スキーマでレガシー行をINSERT→`alembic upgrade head`→`source_type='local_fs'`/`external_id`・`source_url`はNULLを確認。加えて実際の `rag_test` DBにも `alembic upgrade head` を適用しエラーなく完了（`Running upgrade 0003 -> 0004, drive_source_fields`））
- [x] `alembic downgrade -1` でロールバックできる → **達成**（`test_downgrade_minus_one_restores_local_path_uniqueness` で新規3カラム削除・`sources_path_key` 復元・ローカルpath一意性の再現を確認。加えて重複pathを持つDrive行が存在する場合にダウングレードがunique violationで失敗し、トランザクショナルDDLにより0004のままアトミックにロールバックされることも `test_downgrade_minus_one_fails_atomically_when_drive_rows_share_path` で確認（既知のダウングレード制約としてマイグレーションファイル内にコメントで明記））
- [x] 同一 `path` を持つ複数の Drive ソース（`source_type='google_drive'`）が共存できることをテストで確認（パーシャルユニークインデックスの検証） → **達成**（`TestPartialUniqueIndexes::test_google_drive_sources_can_share_path`）
- [x] ローカルソースの `path` 一意性制約が従来通り機能することをテストで確認（リグレッション） → **達成**（`TestPartialUniqueIndexes::test_local_fs_sources_still_enforce_path_uniqueness`）
- [x] 既存の全テストがリグレッションゼロで通過 → **達成**（`make test` で132件全通過、`make lint`（ruff/mypy）もclean。新規追加分は上記4テストを含む計7件）

**スコープ外:** ローダー・indexer 統合（T3/T4）。

---

## T2: Drive 認証・クライアント層

**目的:** サービスアカウント認証で Drive API に安全に接続できる薄いクライアント層を用意する。

**成果物（スペック §4.3, §4.5）:**
- `core/config.py`: `DRIVE_FOLDER_ID` / `DRIVE_SERVICE_ACCOUNT_FILE` / `REDIS_URL` / `INGEST_GDRIVE_JOB_MAX_TRIES`
- `backend/src/private_rag_apps/ingestion/gdrive_client.py`
- README: サービスアカウント作成〜フォルダ共有手順

**作業項目:**
1. `core/config.py` に上記設定を追加する（値のハードコード禁止。既定値は空文字列 = 無効）
2. `gdrive_client.py`: `list_children(folder_id) -> list[DriveFile]` / `download_content(file) -> bytes` 相当の薄いラッパーを実装（`files.list`/`files.get`/`files.export` を呼ぶのみ。探索・変更検知ロジックは持たない）
3. 認証情報未設定・不正時に早期に分かりやすいエラーになることを実装する（スペック §4.9 エラー処理表）
4. README にサービスアカウント作成・JSON キー取得・対象フォルダの共有手順をステップバイステップで追加する

**完了条件:**
- [x] `gdrive_client.py` の各関数がモック化した Drive API（`googleapiclient` のモック）に対してテストされている → **達成**（`backend/tests/test_ingestion_gdrive_client.py`。`build()` を `patch` し、`service.files().list/get_media/export` を `MagicMock` で模擬。`list_children` はレスポンスのフィールドパース・`pageToken` によるページネーション追従・サブフォルダに対して再帰しないことを確認するテストを含む。`download_content` は通常ファイル＝`get_media`／Googleドキュメント＝`export(mimeType=text/plain)` の呼び分けを確認。計17テスト、`make test` で green）
- [x] `DRIVE_FOLDER_ID`/`DRIVE_SERVICE_ACCOUNT_FILE` が空の場合、明確なエラーメッセージで即座に失敗することをテストで確認 → **達成**（`TestConfigValidation` の3ケース。`GoogleDriveClient()` のコンストラクタ内で `_require_drive_config()` が即座に `GoogleDriveConfigError` を送出し、設定名と README 参照を含むメッセージであることを確認）
- [x] サービスアカウントのメールアドレスをエラーメッセージに含め、共有手順を案内することを確認（フォルダ未共有時） → **達成**（`test_folder_not_found_or_not_shared_raises_access_error_with_email_and_instructions`／`test_folder_forbidden_raises_access_error_with_email_and_instructions`。`list_children` が404/403を受けた際、`service_account.Credentials` から取得した `service_account_email` と「閲覧者」「共有」という語を含む `GoogleDriveAccessError` に変換することを確認。**設計ノート:** Drive API は「フォルダが存在しない」と「フォルダは存在するが未共有」を区別する情報を返さないため、404/403 いずれも同一の `GoogleDriveAccessError` に統合している。スペック §4.9 のエラー処理表自体も両者を「対象フォルダが見つからない／共有されていない」という1行の事象としてまとめているため、実装はスペックの粒度と一致している）
- [x] README の手順が確認できる（実際に GCP プロジェクトを作る必要はないが、手順の欠落・矛盾がないか読み直す） → **達成**（README「Google Driveからの取り込み」節に、GCPプロジェクトでのDrive API有効化→サービスアカウント作成→JSONキー作成・ダウンロード→`backend/.env`設定→対象フォルダの共有、の5ステップを記載。ダウンロードしたJSONキーをコミットしないよう明記し、既定の保存先候補（`backend/secrets/`）を`.gitignore`に追加済み。取り込みコマンド自体（`make ingest-gdrive`／`POST /api/ingest/gdrive`）はT3以降で実装される旨の注記を先頭に記載し、未実装機能への言及による矛盾を回避している。読み直しでステップの欠落・前後矛盾は無し）
- [x] テストで実 Drive API を叩いていない（AGENTS.md §8） → **達成**（`test_ingestion_gdrive_client.py` はネットワークアクセスを一切行わない。`googleapiclient.discovery.build` を `patch` で差し替え、認証情報は `cryptography` でローカル生成したRSA鍵を使った使い捨てのサービスアカウントJSONキーファイルから読み込む。実 Google API・実ネットワーク呼び出しは無し。`make test` はDBを使う既存の分離ルール（`DATABASE_URL=...rag_test`）に従って実行し、全149件中この17件を含めてpass）

**スコープ外:** フォルダ再帰探索・変更検知ロジック（T3）。

---

## T3: Drive 探索・ローダー実装

**目的:** Drive フォルダを再帰的に走査し、既存の `content_hash` ベース変更検知にそのまま渡せる内部表現へ正規化する。

**成果物（スペック §4.4, §4.5）:**
- `backend/src/private_rag_apps/ingestion/gdrive_loader.py`

**作業項目:**
1. `DRIVE_FOLDER_ID` を起点に `gdrive_client.list_children()` で再帰探索し、ページネーション（`pageToken`）を処理する
2. mimeType 判定（対応形式: `text/plain`・`text/markdown` 等・Google ドキュメント）+ ファイル名拡張子による救済判定を実装する。フォルダは再帰、ショートカット・非対応 mimeType はスキップしログ・統計に残す
3. 変更検知の2段構え: まず Drive `modifiedTime` を `sources.source_updated_at` と比較し無変化ならダウンロードを省略。変化があれば本文取得（`files.get(alt=media)` / `files.export`）→ SHA256 で `content_hash` 計算 → 既存 `ingestion/diff.py::classify()` にそのまま渡す（**新規の変更検知ロジックを classify() の外に持ち込まない**）
4. ローダーの出力として `loader.py::Document` 相当に `source_type`/`external_id`/`source_url` を加えた内部表現を生成する

**完了条件:**
- [ ] フォルダ再帰探索（子フォルダ・ページネーション）がモックでテストされている
- [ ] mimeType判定 + 拡張子救済判定のロジックがテストされている（曖昧な mimeType のケースを含む）
- [ ] ショートカット・非対応 mimeType のスキップがログ・統計（`ingest_runs.stats` 相当）に残ることを確認
- [ ] `modifiedTime` 事前フィルタで無変化ファイルのダウンロードが省略されることをテストで確認
- [ ] `content_hash` の最終判定が既存 `classify()` にそのまま委譲されており、classify() 自体を変更していないことをコードレビューで確認
- [ ] テストで実 Drive API を叩いていない

**スコープ外:** indexer への統合・チャンキング以降のパイプライン（T4）。

---

## T4: indexer 統合・CLI コマンド

**目的:** Drive ローダーの出力を既存 ingestion パイプライン（チャンキング・埋め込み・upsert・削除検知）に接続する。

**成果物（スペック §4.5 後半, §4.8）:**
- `ingestion/indexer.py`: `execute_gdrive_ingestion(folder_id)` 新設、ソース照合・削除検知の `source_type` 分岐
- `cli/main.py`: `ingest-gdrive` サブコマンド
- `Makefile`: `ingest-gdrive` ターゲット

**作業項目:**
1. `_process_one` 相当のソース照合ロジックを `source_type` に応じて分岐させる（ローカル: `Source.path == doc.path` / Drive: `Source.external_id == doc.external_id`）
2. 削除検知（消えたソースの論理削除）を `source_type` ごとに独立させる。**走査結果とDB生存ソースの突き合わせがローカル/Driveで混同されないこと**（スペック §8 の重要リスク）
3. 削除安全弁（`INGEST_DELETE_GUARD_RATIO`）の判定をソース種別ごとの生存数・ヒット数比率で行う
4. `execute_gdrive_ingestion(folder_id)` を新設し、`gdrive_loader` の出力を既存のチャンキング・埋め込み・upsert 段（`execute_ingestion` と共通のコード）に渡す
5. `cli/main.py` に `ingest-gdrive` サブコマンドを追加し、`Makefile` に `ingest-gdrive` ターゲットを追加する

**完了条件:**
- [ ] `execute_gdrive_ingestion()` がチャンキング以降で `execute_ingestion()` と共通のコードパスを通ることをコードレビューで確認（二重実装がない）
- [ ] ソース照合が `source_type` に応じて正しく分岐することをテストで確認（同一 `path` のローカル/Driveソースが誤って同一視されない）
- [ ] 削除安全弁・削除検知がソース種別ごとに独立して判定されることをテストで確認（例: ローカルソースが大量に見えても Drive ソースの削除判定に影響しない、逆も同様）
- [ ] `make ingest-gdrive` がモック化した Drive クライアントに対して一気通貫（探索→取り込み→DB反映）で動作する
- [ ] 既存のローカル取り込み（`make ingest`/`make demo`、既存テスト）がリグレッションゼロで通過する

**スコープ外:** ARQ/API 経由のトリガ（T5）。

---

## T5: ARQ/Redis ジョブ基盤・API エンドポイント

**目的:** API 経由トリガ（`POST /api/ingest/gdrive`）をプロセス再起動に耐える形で実行できるようにする。

**成果物（スペック §4.6, §4.8, §5）:**
- `docker-compose.yml`: `redis` サービス追加
- `backend/pyproject.toml`: `arq` 追加
- `backend/src/private_rag_apps/worker/`（`settings.py`, `tasks.py`）
- `api/main.py`: `POST /api/ingest/gdrive`
- `Makefile`: `worker` ターゲット

**作業項目:**
1. `docker-compose.yml` に `redis`（`redis:7-alpine` 等）を追加する。Drive 機能を使わない場合でも起動して問題ないことを確認する
2. `worker/settings.py`: ARQ `WorkerSettings` を定義（Redis接続、`max_tries` 等）。`worker/tasks.py`: ジョブ関数 `run_gdrive_ingestion(ctx)` は `execute_gdrive_ingestion()` を呼ぶのみで独自ロジックを持たない（AGENTS.md §3、`cli` と同じ位置づけ）
3. `POST /api/ingest/gdrive`: 呼び出し時点で `ingest_runs` の `running` 行を同期的に作成してから ARQ へ enqueue する（既存 `POST /api/ingest` と同じパターン）。多重実行の抑止（advisory lock + `status='running'` チェック）をソース種別に関わらずグローバルに維持する
4. リトライ設定 `INGEST_GDRIVE_JOB_MAX_TRIES`（既定3）を実装し、最終試行失敗後に `ingest_runs.status='error'` を記録する
5. `Makefile` に `worker` ターゲットを追加する（`cd backend && uv run arq private_rag_apps.worker.settings.WorkerSettings`。ホスト上で直接起動、専用 docker イメージは作らない）

**完了条件:**
- [ ] `docker compose up` で `redis` が問題なく起動する
- [ ] `POST /api/ingest/gdrive` が `ingest_runs` の `running` 行を同期作成してから応答を返すことをテストで確認
- [ ] ローカルで起動した ARQ worker（実 Redis、モック Drive クライアント）がジョブを実際に消費し `execute_gdrive_ingestion()` を呼ぶことを統合テストで確認
- [ ] `INGEST_GDRIVE_JOB_MAX_TRIES` 回失敗後に `ingest_runs.status='error'` が記録されることをテストで確認
- [ ] グローバル排他ロックにより、ローカル取り込み実行中は Drive 取り込みジョブが（逆方向も）起動できないことをテストで確認
- [ ] `GET /api/ingest/runs` の応答形式（既存 API 契約）が変更されていないことを確認
- [ ] `worker/tasks.py` がジョブ関数のみでロジックを持たないことをコードレビューで確認（AGENTS.md §3 準拠）

**スコープ外:** citation 連携（T6）。定期自動同期・スケジューリング（スコープ外、スペック §1）。

---

## T6: citation 連携（バックエンド + フロントエンド）

**目的:** Drive ソースの出典から元ドキュメントに直接アクセスできるようにする。

**成果物（スペック §4.7）:**
- `graph/state.py`: `ScoredChunk`/`Citation` に `source_type`/`source_id` 追加
- `retrieval/searcher.py::_format_chunks()` 更新
- `generation/generator.py::generate_answer_stream()` 更新
- `frontend/src/components/Citations.tsx` 更新
- `GET /api/sources` に `source_type`/`source_url` 追加

**作業項目:**
1. `graph/state.py` の `ScoredChunk`/`Citation` TypedDict に `source_type: str` `source_id: str | None` を追加する
2. `retrieval/searcher.py::_format_chunks()` で `Source` から `source_type`/`external_id`/`source_url` を取得しチャンク dict に含める
3. `generation/generator.py::generate_answer_stream()` の citations 組み立てに `source_type`/`source_id` を追加する（**既存フィールド `n`/`title`/`path`/`heading`/`chunk_id` は変更しない**）
4. `frontend/src/components/Citations.tsx`: `source_type === "google_drive"` の場合 `c.source_url`（`webViewLink`）を使うよう `href` 分岐を追加する。ローカルの場合は既存の `file://${c.path}` のまま
5. `GET /api/sources` に `source_type`/`source_url` を含める

**完了条件:**
- [ ] 既存の citation payload フィールド（`n`/`title`/`path`/`heading`/`chunk_id`）に変更がないこと（M7 T3/T6 と同じ手法の stub 構造検証テストで担保）
- [ ] ローカルソース・Driveソースそれぞれで citations に正しい `source_type`/`source_id` が入ることをテストで確認
- [ ] `Citations.tsx` の href 分岐がローカル/Driveそれぞれで正しいリンクになることをフロントエンドテストで確認
- [ ] `GET /api/sources` のレスポンスに `source_type`/`source_url` が含まれることを確認（ローカルソースでは `source_url` が null になることを含む）
- [ ] 既存の citation 関連テスト（SSE構造検証・generator・API）がリグレッションゼロで通過

**スコープ外:** 引用表示自体の高度化（回答の根拠ハイライト等。スペック Out of scope）。

---

## T7: エラー処理・観測性

**目的:** Drive 固有の失敗モードを既存パターンに沿って扱い、運用時の診断可能性を確保する。

**成果物（スペック §4.9, §6）:**
- スペック §4.9 エラー処理表の実装
- Langfuse `gdrive_scan` span 計装

**作業項目:**
1. Drive API レート制限（429）に指数バックオフを実装する（既存の埋め込みAPI失敗時と同様の方針。`INGEST_EMBED_MIN_INTERVAL_SEC` に相当するペーシング思想を踏襲）
2. サービスアカウント認証失敗・対象フォルダ未共有を実行開始前に早期検知し、`ingest_runs.error` に分かりやすいメッセージ（共有手順の案内を含む）を記録する
3. 個別ファイルのダウンロード失敗時はスキップして続行し `ingest_runs.stats.failed_files` に記録する（既存パターン）
4. `gdrive_loader` に `@observe(name="gdrive_scan")` 相当の span を追加し、走査件数・API呼び出し回数を記録する
5. ARQ ジョブ実行自体が既存の `@observe(name="ingest_run")` の枠内で trace 化されることを確認する（T5 の実装がこの前提を壊していないか確認するのみで、新規実装は不要な可能性が高い）

**完了条件:**
- [ ] スペック §4.9 エラー処理表の各行に対応するテストがある（429バックオフ、認証失敗の早期検知、フォルダ未共有、個別ファイル失敗、ARQ最大試行超過）
- [ ] `gdrive_scan` span が実装されていることをコードレビューで確認（実 Langfuse UI 上の確認は環境依存のため必須としない。M7 T7 と同じ扱い。可能であれば実施し記録する）
- [ ] エラーメッセージにサービスアカウントのメールアドレスと共有手順が含まれることを確認（フォルダ未共有ケース）

**スコープ外:** Langfuse ダッシュボードの新規フィルタ設計（既存の trace metadata の枠組みで足りる）。

---

## T8: テスト・ドキュメント最終反映・動作確認

**目的:** M9 全体の品質を確定し、実装内容をドキュメントに反映する。

**成果物:**
- `make lint`/`make test` 通過
- README 更新（サービスアカウント設定〜動作確認手順）
- `architecture.md`/`db_design.md` への実装内容反映

**作業項目:**
1. `make lint`/`make test` を実行し全て通過することを確認する
2. README に T2 で追加した手順を最終確認し、実際にこのドキュメントだけを見て Drive 機能を有効化できるか通読する
3. `architecture.md`/`db_design.md` に M9 の実装内容（データモデル変更・新規コンポーネント）を反映する（スペック §9 の通り、M4/M7 同様このタイミングで反映）
4. 可能であれば実際の GCP サービスアカウント + テスト用 Drive フォルダで手動スモークテストを行う（作成→共有→`make ingest-gdrive`→`make demo`相当の確認→citationリンクの実クリック確認）。ユーザー環境に依存するため実施できない場合は、その旨と再現手順をタスクノートに明記し持ち越しとする

**完了条件:**
- [ ] `make lint`/`make test` が通過する
- [ ] README の手順が完結している（追加のGCPアカウント作成以外に読者が迷う箇所がない）
- [ ] `architecture.md`/`db_design.md` が更新されている
- [ ] 手動スモークテストを実施したか、実施できない場合はその理由と持ち越しである旨がタスクノートに記録されている
- [ ] `docs/decisions.md` の M9 関連3件の決定（サービスアカウント認証・identity key一般化・ARQ限定利用）が実装と齟齬がないことを確認

**スコープ外:** なし（最終タスク）。

---

## 全体の完了定義（M9 クローズ条件）

- [ ] T0–T8 の全完了条件を満たす
- [ ] 既存のローカル取り込み（CLI/API/BackgroundTasks）の挙動が一切変更されていない（リグレッションゼロ。全既存テスト通過が根拠）
- [ ] 依存方向ルール（AGENTS.md §3）を破っていない。特に `worker`/`ingestion` は薄い層のまま、Google Drive API アクセスは `ingestion/` に限定
- [ ] `make demo` のクリーンルーム体験（NFR-8）に影響がない（`DRIVE_FOLDER_ID` 未設定時、Drive関連コードパスに一切触れない）
- [ ] `make eval`/`make eval-routing` は対象外（チャンキング・埋め込みモデル・プロンプトを一切変更しないため。スペック §7）
- [ ] シークレット（サービスアカウント JSON キー）・実データをコミットしていない
- [ ] `sources.path` の UNIQUE 制約変更がロールバック可能な形で実装されている（T1）
