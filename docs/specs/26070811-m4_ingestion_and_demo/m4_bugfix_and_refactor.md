# バックエンドコードレビュー: M4 増分再取り込み・API/CLI 変更

> **解決状況（追記）**: 本レビューはコミット前の作業差分に対して書かれたが、指摘の大半は `254834b`（コーパス取込み、チャンク、ベクトル化の処理を追加）として反映済みの状態でコミットされていた（レビュー文書自体の更新のみが漏れていた）。残っていた D-12・D-13 は本追記と同時期のコミットで解消した。以下、原文は提出時点の記録として保持する。
>
> | 指摘 | 状態 | 備考 |
> |---|---|---|
> | A-1（安全弁作動時に `run.status` を全体errorにする） | ✅ 解決済み | `254834b`。`_apply_deletion_phase` が `status="success"` を維持し理由を `error` に記録。決定は `docs/specs/26070811-m4_ingestion_and_demo/spec.md` §13 に明記 |
> | A-2（`reset_index` がadvisory lockを取らない） | ✅ 解決済み | `254834b`。`acquire_start_lock`/`get_running_run`/`reap_stale_running` を共有ヘルパ化し `start_run`・`reset_index` 双方から使用 |
> | A-3（CLIが `os.environ` を直読みしchoicesを回避） | ✅ 解決済み | `254834b`。`core.config.Settings` に `ingest_trigger: Literal["cli","demo"]` / `force_delete: bool` を追加 |
> | B-4（戻り値型注釈の欠如） | ✅ 解決済み | `254834b`。5関数すべてに戻り値型（専用TypedDictを含む）を付与 |
> | B-5（`Any` の使用） | ✅ 解決済み | `254834b`。`Stats` をTypedDict化、`source_id` を `uuid.UUID` 化 |
> | C-6（UTC now の重複実装） | ✅ 解決済み | `254834b`。`core.time.utcnow()` に共通化 |
> | C-7（`func` の重複import） | ✅ 解決済み | `254834b` |
> | C-8（埋め込みモデル名のハードコード） | ✅ 解決済み | `254834b`。`settings.embed_model` に統一（indexer/searcher共有） |
> | C-9（安全弁ブロックがヘルパ未抽出） | ✅ 解決済み | `254834b`。`_apply_deletion_phase` として抽出 |
> | C-10（CLIがrun.status/errorを握りつぶす） | ✅ 解決済み | `254834b`。`status=="error"` 時に stderr 出力+非ゼロ終了 |
> | D-11（`test_ingest_api.py` の `finally`/`NameError` リスク） | ✅ 解決済み | `254834b`。`body` を `try` の前に取得し `finally` は `.get()` を使用 |
> | D-12（advisory lockの実並行性テスト欠如） | ✅ 解決済み | 本追記と同時期のコミットで `test_start_run_advisory_lock_serializes_concurrent_starts` を追加 |
> | D-13（`_cleanup` の無条件全削除） | ✅ 解決済み | 本追記と同時期のコミットで `run_ids` による範囲指定に変更 |
> | E（`docs/specs/26070811-m4_ingestion_and_demo/tasklist.md` Phase8/changelog不整合） | ✅ 解決済み | `docs/specs/26070811-m4_ingestion_and_demo/tasklist.md` v0.5 changelogで解消（Phase 8は5/7チェック済み、残り2件は理由付きで意図的に対象外） |

## Context（背景）

バックエンドのコードレビュー依頼を受けた。現在 git 上に M4（増分再取り込み・コーパス管理API・デモモード仕上げ）関連の未コミット差分が存在する：

- 変更: `ingestion/indexer.py`, `api/main.py`, `cli/main.py`, `core/config.py`
- 新規: `ingestion/concurrency.py`, `ingestion/diff.py`, `backend/seed`（`../seed` へのsymlink）, テスト4本

このレビューはこの未コミット差分を対象にした（バックエンド全体の悉皆レビューではない）。`docs/specs/26070811-m4_ingestion_and_demo/spec.md`（v0.2）・`docs/specs/26070811-m4_ingestion_and_demo/tasklist.md` と突き合わせ、AGENTS.md の規約（依存方向・型注釈・設定の一元化・async等）に照らして検証済み。以下は実際にファイルを読んで確認した指摘のみ（未検証の推測は含めていない）。★は特に重要。

---

## A. 仕様と実装の不整合（★最優先・AGENTS.md §12「差分を指摘して合意を取る」対象）

### 1. ★安全弁発動時、`run.status` が実行全体を "error" にし、仕様の決定と食い違う

- **該当箇所**: `ingestion/indexer.py:58-63`
```python
if allowed:
    stats["deleted"] = _apply_deletions(db, found_paths)
    run.status = "success"
else:
    run.status = "error"
    run.error = f"delete phase aborted: {reason}"
```
- **仕様**: `docs/specs/26070811-m4_ingestion_and_demo/spec.md` §4.3/§14 は「安全弁発動時に実行全体をerrorにするか、削除フェーズのみ中断して追加/更新は残すか」を未決事項として明記し、`docs/specs/26070811-m4_ingestion_and_demo/tasklist.md` Phase 0 で **「デフォルト維持」＝削除フェーズのみ中断** と決定済み（スペック差分なしとされている）。
- **実装との齟齬**: 追加/更新は各ドキュメントごとに既にcommit済み（`_process_one` 内で都度 `db.commit()`、`indexer.py:95,113,126`）にもかかわらず、安全弁が働くと `run.status = "error"` で**実行全体**を error 扱いにする。`test_ingestion_indexer.py:236` もこの挙動をそのままアサートしている。
- **なぜ問題か**: `GET /api/ingest/runs` やCLI出力を見る運用者・UI・監視は「全滅」と「追加更新は成功、削除だけ安全に見送られた」を区別できない。
- **提案**: 一存で実装側に寄せず、まず差分をチームに提示して合意を取る（AGENTS.md §12）。選択肢: (a) 安全弁トリップ時も `status="success"` のまま警告を`stats`/専用フィールドに残す、(b) `running`/`success`/`error` に加え `partial` 相当の状態を設ける。決定後は `docs/specs/26070811-m4_ingestion_and_demo/spec.md` §14 の記述・該当テストも合わせて更新する。

### 2. `DELETE /api/index` が `start_run` と同じ排他（advisory lock）を取っていない

- **該当箇所**: `api/main.py:278-291` vs `ingestion/concurrency.py:38-50`
- `start_run` は「running行の有無チェック→INSERT」を `pg_advisory_xact_lock`（`concurrency.py:42`）で原子化しているが、`reset_index` は同じチェックを素の `SELECT`（`api/main.py:283`）だけで行い、ロックを取らない。そのため理論上、`reset_index` のチェックと実際の `DELETE` の間に `POST /api/ingest` が割り込むレースが起こり得る（single-userアプリなのでウィンドウは狭いが、`start_run` 側がまさにこの種のレースを防ぐために導入した保護と非対称）。
- **提案**: `concurrency.py` に「advisory lockを取ってrunning行の有無を見る」共通ヘルパを切り出し、`start_run`・`reset_index` の両方から使う。

### 3. CLIの `--trigger` デフォルトが環境変数由来で `choices` バリデーションをすり抜ける

- **該当箇所**: `cli/main.py:23-34`
```python
p_ingest.add_argument("--trigger", default=os.environ.get("INGEST_TRIGGER", "cli"),
                       choices=["cli", "demo"], ...)
p_ingest.add_argument("--force-delete", action="store_true",
                       default=os.environ.get("FORCE_DELETE", "0") == "1", ...)
```
- `argparse` の `choices` は「コマンドラインで明示された値」しか検証せず、`default=` の値はノーチェックで通る。`INGEST_TRIGGER` に想定外の値が入っていても `IngestRun.trigger`（DB制約なしのstring）にそのまま書き込まれる。
- 加えて `os.environ.get(...)` を直接読んでおり、AGENTS.md §6「設定は `core/config.py`（pydantic-settings）経由」に反する（`FORCE_DELETE` の `"0"/"1"` 文字列比較も pydantic の bool 型なら不要になる処理）。
- **提案**: `INGEST_TRIGGER`/`FORCE_DELETE` を `core/config.py` の `Settings` にフィールド化する（`trigger` は `Literal["cli","demo"]` 等）。

---

## B. AGENTS.md 規約違反（型注釈・`Any`）

### 4. 新規追加した4エンドポイント + CLI関数に戻り値の型注釈が無い

`api/main.py:210`(`list_sources`) / `:247`(`trigger_ingest`) / `:261`(`list_ingest_runs`) / `:279`(`reset_index`) / `cli/main.py:8`(`ingest`)。同じdiffで追加された `_run_ingest_in_background`（`api/main.py:234`）は `-> None` が付いており、この5関数だけ抜けている（`mypy --disallow-untyped-defs` で実検出済み）。可能ならPydanticレスポンスモデルを定義し `response_model=` で形も保証する方が良い。

### 5. `Any` の新規使用

`ingestion/indexer.py:15`（`Stats = Dict[str, Any]`）と `:131`（`_insert_chunks` の `source_id: Any`）。`Stats` の形は `{added, updated, deleted, skipped, failed_files}` 固定なので `TypedDict` 化できる。`source_id` は常に `Source.id`（`models/rag.py` で `Mapped[UUID]`）なので `uuid.UUID` で足りる。

> 補足: `backend/pyproject.toml` に `[tool.mypy]` が無く、mypy自体が依存関係にも入っていない（確認済み）。クリーンな環境では `make lint` の `uv run mypy .` が動かない状態で、上記の型注釈欠落が今のlintでは検出されない一因。本diffが起因ではないが、根が深いので合わせて共有。

---

## C. リファクタリング・簡素化の余地

### 6. `concurrency.py` が "UTC now" を2箇所で重複してインライン実装

`concurrency.py:16-17,30` は `indexer.py:18-19` の `_now()` と同じ処理だが、`concurrency` は `indexer` からimportされる側なので再利用できない。両者の共通依存である `core` に小さなヘルパ（例: `core.time.utcnow()`）を1つ置くと解消できる。

### 7. `api/main.py` で `func` の重複import

モジュールレベル `api/main.py:10`（`from sqlalchemy import desc, func`）と、既存の`event_generator`内ローカル `api/main.py:188`（`from sqlalchemy.sql import func`）が重複。今回のdiffで`func`がモジュールレベルに来たため、188行目は不要になった。

### 8. Embeddingモデル名がハードコードのまま、"Ingestion Settings" 新設のタイミングを逃している

`indexer.py:169`（`model="voyage-4-lite"`）。同種のハードコードは `retrieval/searcher.py:65` にも既存（本diff由来ではない）。今回 `core/config.py` にIngestion用設定を5つ新設したのに、同じ関数内のこの値は据え置かれた。`settings.embed_model` を新設し両方から参照する形が自然（変更時は AGENTS.md §7 により再インデックス＋`make eval` 必須）。

### 9. 安全弁判定〜削除適用ブロックの抽出

`indexer.py:51-63` は同ファイル内の他処理（`_insert_chunks`/`_apply_deletions`/`_flush_stats`）がヘルパへ切り出されているのに対し、この十数行だけ `execute_ingestion` に直書きされている。`_apply_deletion_phase(...)` 程度に切り出すと、A-1を修正する際の変更点も1箇所にまとまる。

### 10. CLIが `run_ingestion` の戻り値（`status`/`error`）を握りつぶしている

`cli/main.py:8-15` は `run.status == "error"` でも `"Ingestion complete."` と表示し、終了コードは常に0。`make ingest`/`make demo` をCI/cronから呼んだ場合、安全弁トリップ等の異常に気づけない。戻り値を見て非ゼロ終了＋`run.error`をstderrに出す方が安全。

---

## D. テストの気になる点

### 11. `test_ingest_api.py:87-101` の `finally` が `NameError` を起こしうる

91行目のassertが失敗すると `body` が未定義のまま100行目の `body["id"]` で `NameError` になり、本来の失敗理由が隠れる。

### 12. advisory lockの「同時2リクエスト競合」自体を検証するテストが無い

`test_ingestion_concurrency.py` の4テストは全て逐次実行で、`pg_advisory_xact_lock`（`concurrency.py:42`）を削除しても同じ結果になる（先にcommitされた`running`行だけで判定が通ってしまうため）。`test_ingest_api.py` の実スレッドテストは近い性質だが、それは「バックグラウンド処理中のrunning行」を見ているだけで、advisory lockが守る開始時レースとは別物。

### 13. `test_ingestion_indexer.py` の `_cleanup` が無条件 `db.query(IngestRun).delete()`

兄弟ファイル `test_ingestion_concurrency.py::_cleanup_runs` は対象IDでスコープしているのに対し、こちらは全件削除。テスト並列実行時のフレーキネスの芽。

---

## E. 補足（コードではなく仕様プロセス）

`docs/specs/26070811-m4_ingestion_and_demo/tasklist.md` Phase 8（§13 上位ドキュメント反映: `db_design.md`/`architecture.md`/`requirements.md`、最終 `make lint`/`make test`、PRでのFR-1/2/7/8・NFR-8明記）が全項目未チェック。また同ファイルchangelog最新行（v0.4）は「Phase 3実装完了、Phase4以降は未着手」と書かれているが、本文のPhase 4〜7チェックボックスは全てチェック済みになっており、changelogと本文が食い違っている。AGENTS.md §10のDoDは「関連するdocs/specsを更新した」を要求しているため、マージ前にどちらが実態か確認しPhase 8を消化する必要がある。

---

## 進め方の提案

- A-1は仕様側との合意が必要なため最優先で相談（コードを一存で直さない）。
- A-2, A-3, B-4, B-5 は挙動を変えずに直せる範囲。
- 承認いただければ A→B→C の順に着手し、都度 `make test`/`make lint` を実行して確認する。C-8（設定化）はAGENTS.md §7により再インデックス＋`make eval`が必要になる点に注意。

## Verification

- 既存テスト `backend/tests/test_ingestion_indexer.py` / `test_ingestion_concurrency.py` / `test_ingestion_diff.py` / `test_ingest_api.py` を修正のたびに実行。
- `make lint` / `make test`（mypy未導入の懸念はB章末尾を参照。対応するかどうかも合意が必要）。
- A-1を修正する場合は `docs/specs/26070811-m4_ingestion_and_demo/spec.md` §14 の記述更新も合わせて行う（AGENTS.md §12）。
