# M2 タスクリスト (docs/specs/26070718-m2_streaming_and_history/tasklist.md)

> 配置先: `docs/specs/26070718-m2_streaming_and_history/tasklist.md`
> 対応スペック: `docs/specs/26070718-m2_streaming_and_history/spec.md`（以下「スペック」）
> 進め方: 上から順に実施する。各タスクは対応するスペックの節番号を付記。
> 各フェーズの末尾で `make lint` / `make test` を通し、RAG 挙動（condense プロンプト追加）に触れたら `make eval` を実行する（AGENTS.md §7/§10）。

> **M5 監査メモ（2026-07-13）**: 本ファイルのチェックは実装済みコードとの一括照合（bulk verification）で行った。個々のコマンドを再実行するのではなく、`docs/specs/26070718-m2_streaming_and_history/spec.md` §10 受け入れ条件の検証結果（file:line 単位の根拠）をこのタスクリストの該当項目に敷衍する形でチェックを付けている。テスト未整備・実装未配線が判明した項目は未チェックのまま `genuine gap` として明記した。

---

## Phase 0 — 準備

> M5 監査メモ（2026-07-13）: 実装は「メッセージ metadata に citations を持たせる」「検索0件でも実際は保存する（§4.2既定と乖離）」「condenseは初回ターンのみスキップ」を de facto に採用しているが、いずれもスペック §12「未決事項」には決定として書き戻されていない。コードは動くがスペック §12 が未更新のままという文書債務のため、以下は未チェックのまま残す。

- [ ] スペック §12「未決事項」を確認し、着手前に以下を決定する
  - [ ] 出典を assistant-ui の「カスタムパート」で持つか「メッセージ metadata」で持つか（導入版 API を確認して決定）— 実装は `frontend/src/lib/chat-adapter.ts` の `metadata.custom.citations` で **metadata 方式を採用**しているが、スペック §12 には未反映
  - [ ] 検索 0 件の定型応答を履歴に保存するか（既定: 保存しない。変える場合はスペックを更新）— 実装（`api/main.py` の `event_generator`）は zero-hit ターンでも `has_error` にならないため **常に保存**しており、スペック §4.2/§4.6 の既定（保存しない）と乖離。スペック側の更新も実装側の修正もされていない
  - [ ] condense の発見的スキップ条件をどこまで作り込むか（M2 は「初回ターンのみスキップ」を最低ラインとする）— 実装は最低ライン（初回ターンのみスキップ、`api/main.py:153-156`）どおりで機能的には満たすが、決定事項としてスペックに書き戻されていない
- [ ] 決定事項をスペック §12 に反映する（differ があれば先にスペックを更新してから実装、AGENTS.md §12）

---

## Phase 1 — バックエンド: `/api/chat` の SSE 化（スペック §4.1, §4.6）

- [x] `sse-starlette`（or 同等）を導入し、`/api/chat` を `StreamingResponse` / `EventSourceResponse` に変更 — `api/main.py:12,238` `from sse_starlette.sse import EventSourceResponse` / `return EventSourceResponse(...)`
- [x] レスポンスヘッダーにバッファリング無効化設定を追加（`X-Accel-Buffering: no` 等）— アプリコードでの明示設定はないが、`sse_starlette.sse.EventSourceResponse` が既定で `X-Accel-Buffering: no` / `Cache-Control: no-store` を設定する（`sse_starlette/sse.py:310,313`）
- [x] keepalive コメント（`: ping`）を `SSE_KEEPALIVE_SEC` 間隔で送出する実装 — `EventSourceResponse(..., ping=settings.sse_keepalive_sec)`（`api/main.py:238`、既定 15 秒 = `sse_starlette` の `DEFAULT_PING_INTERVAL` と一致）
- [x] `citations` イベント（rerank 直後、番号付き出典配列。§4.5）を実装 — `generation/generator.py:47-57`
- [x] `token` イベント（生成トークンごとの delta 配信）を実装 — `generation/generator.py:74-75`, `api/main.py:178-188`
- [x] `done` イベント（`message_id`, `conversation_id` を含む）を実装 — `api/main.py:231-236`
      - ※ `message_id` の本採番は Phase 2（永続化）に依存。Phase 1 では暫定値/stub で配線し、Phase 2 で実 ID に結線する
- [x] `error` イベント（生成/検索の回復不能な失敗時）を実装 — `generation/generator.py:87-88`, `api/main.py:192-201`
- [x] クライアント切断（`abortSignal` 相当）検知時に LLM ストリームを中断する実装 — `api/main.py:171-173` `await request.is_disconnected()` で `break`。※OpenAI ストリームの明示的 `close()` 呼び出しはなく暗黙の GC 依存、専用テストも無い（弱点として維持）
- [x] **generate（streaming）span を同時に配線**（開始〜完了で閉じ、トークン数/コスト/レイテンシを完了時に確定記録。NFR-4。後付けにしない＝AGENTS.md §7）— `generation/generator.py:39` `@observe(as_type="generation")` + L80-86 `update_current_generation(usage_details=...)`
- [x] 検索 0 件時のフォールバック（生成スキップ→定型文を `token` で1度→`citations: []`→`done`）— `generation/generator.py:42-45`
- [x] ユニットテスト: SSE イベント整形（citations/token/done/error のペイロード形状）— `tests/test_chat.py`（generator 単体）、`tests/test_api.py::test_chat_bulk_save_and_history`（wire format の substring 検証）
- [x] ユニットテスト: 検索 0 件時のフォールバック応答 — `tests/test_chat.py::test_generate_answer_stream_no_chunks`

---

## Phase 2 — バックエンド: 会話の永続化 + 管理エンドポイント（スペック §4.2, §4.4, §8）

- [x] `POST /api/conversations`（空会話作成。`RemoteThreadListAdapter.initialize()` 対応）を実装 — `api/main.py:84-91`
- [x] `GET /api/conversations`（一覧。`updated_at DESC`）を実装 — `api/main.py:93-97`
- [x] `GET /api/conversations/{id}`（履歴。messages を昇順、role/content/citations を含む）を実装 — `api/main.py:99-120`
- [x] `/api/chat` に `conversation_id` 省略時の遅延作成ロジックを実装（CLI/テスト経路用）— `api/main.py:130-136`
- [x] **done 一括保存**を実装: `done` 直前に user + assistant メッセージ + citations を **1 トランザクションで INSERT**（正常終了時のみ。user 先行保存はしない）— `api/main.py:203-229`
- [x] エラー・キャンセル時は user / assistant いずれも保存しないことを実装（失敗ターンは履歴に残さない。§4.2）— `api/main.py:203` `if not has_error:` が Bulk Save をガード
- [x] 一括保存と同一トランザクションで `conversations.updated_at` を更新する実装 — `api/main.py:221-223`（同一 `db.commit()` 前）
- [x] タイトル自動設定（最初の user メッセージ先頭を `TITLE_MAX_CHARS` で切り詰め。LLM 呼び出しなし）— `api/main.py:140-146`
- [x] `core/config.py` に `TITLE_MAX_CHARS` を追加（ハードコード禁止。AGENTS.md §6）— `core/config.py:29`
- [x] Phase 1 の `done` の `message_id` を、ここで採番した実 ID に結線 — `api/main.py:162,212` (`message_id = str(uuid.uuid4())` を `Message.id` にそのまま使用)
- [ ] 統合テスト: `POST /api/conversations` → `POST /api/chat`(SSE) → `GET /api/conversations/{id}` の一連が通ることをテスト DB で検証 — 各エンドポイントは個別にテスト済み（`tests/test_api.py::test_conversations_crud` が POST/GET/GET-detail、`test_chat_bulk_save_and_history` が chat+DB直接検証）だが、**GET エンドポイント経由で一連をチェーンするテストは無い**（DB クエリで代替検証されているのみ）。スポットチェックで未充足と判断、未チェックのまま
- [ ] 統合テスト: **エラー/キャンセル発生ターンで user / assistant とも保存されず**、履歴が user↔assistant の交互列を保つことを確認 — 該当テストは見つからず（`tests/test_api.py` は正常系のみ）。実装（Phase 2 の has_error ガード）はあるがテストが無い、genuine gap
- [ ] 統合テスト: 一覧が `updated_at DESC` で返ることを確認 — `test_conversations_crud` は一覧に対象IDが含まれることのみ検証し、複数件での順序は未検証。実装（`ORDER BY updated_at DESC`）自体は正しい（§10 側は check 済み）が、専用テストが無い

---

## Phase 3 — バックエンド: condense（マルチターン クエリ書き換え）（スペック §4.3, §9）

- [x] `generation` レイヤに condense 関数を追加（LLM 呼び出しは `generation` のみ。AGENTS.md §3 依存方向を確認）— `generation/generator.py:9` `condense()`
- [x] condense プロンプトを `prompts/` に追加（コードへのハードコード禁止）— `prompts/condense.py`（`CONDENSE_SYSTEM_PROMPT`, `build_condense_prompt`）
- [x] `core/config.py` に `CONDENSE_MODEL` / `CONDENSE_HISTORY_TURNS` を追加し、使用モデル名をトレースに記録 — `core/config.py:25-26`、`generation/generator.py:32` `update_current_generation(model=settings.condense_model)`
- [x] **condense span を同時に配線**（トークン数/コスト/レイテンシを記録。初回スキップ時は span を生成しない。NFR-4／後付けにしない＝AGENTS.md §7）— `@observe(as_type="generation")`（`generator.py:8`）。初回ターンは `condense()` 自体が呼ばれない（`api/main.py:153-156`）ため span も発生しない
- [x] 初回ターン（履歴なし）は condense をスキップし、`message` をそのまま検索クエリにする実装 — `api/main.py:153-156`
- [x] condense 失敗時のフォールバック（元メッセージをそのまま検索クエリにして継続。§4.6）— `generation/generator.py:35-37`
- [ ] `CHAT_HISTORY_TOKEN_BUDGET` を設定化し、生成プロンプトへの履歴切り詰めに使用 — 設定キー自体は `core/config.py:27` にあるが、**どこからも参照されておらず未配線**（grep で使用箇所ゼロ）。genuine gap
- [x] ユニットテスト: 初回ターンで condense が呼ばれないことを確認 — `tests/test_api.py::test_chat_bulk_save_and_history`（`mock_condense.assert_not_called()`）
- [x] ユニットテスト: フォローアップ入力（指示語あり）で履歴を用いたクエリに書き換わることを確認（LLM はモック/記録再生）— `tests/test_chat.py::test_condense_with_history`
- [ ] ユニットテスト: condense 失敗時に元メッセージへフォールバックすることを確認 — 該当テストは見つからず（`test_chat.py` に例外系のテストが無い）。実装（§4.6 のフォールバック）はあるがテスト未整備
- [ ] `make eval` を実行し、条件分岐追加前後で単一ターン生成の回帰がないことを確認（AGENTS.md §7）— 実運用 DB/API が必要で本セッションでは未実行（M5 Phase 3 待ち）

---

## Phase 4 — フロントエンド: ChatModelAdapter（SSE 受信・累積 yield）（スペック §5.1, §5.2）

- [x] `ChatModelAdapter.run({ messages, abortSignal })` を実装し `/api/chat` へ POST — `frontend/src/lib/chat-adapter.ts:7-29`
- [x] SSE レスポンスを読み取るパーサ（`event:`/`data:` 行の分解）を実装 — `chat-adapter.ts:56-101`。`chat-adapter.test.ts` がチャンク分割ケースも検証
- [x] 累積状態（テキスト・出典パート）を**ループ外**で保持する実装（チャンクごとの content 再生成を避ける。既知の落とし穴に注意）— `chat-adapter.ts:42-48`（`accumulatedText`/`metadata` を while ループ外で宣言）
- [x] `citations` 受信時に出典パートを content に一度だけ追加する実装 — `chat-adapter.ts:81-93`
- [x] `token` 受信ごとに累積テキストを更新し、`content:[text, …出典パート]` を yield する実装 — `chat-adapter.ts:64-80`
- [ ] `done` 受信時に `message_id` / `conversation_id` を確定し、ストリームを終了する実装 — `chat-adapter.ts:96-99` の `done` 分岐は実質 no-op コメントのみで、payload から `message_id`/`conversation_id` を明示的に取り出していない（conversationId は `createChatAdapter(conversationId)` の引数として外部＝`RemoteThreadListAdapter`/`active-thread-store` 経由で既に確定済みという設計に依拠）。仕様書通りの「done 受信時に確定」という実装ではないため未チェック
- [x] `error` 受信時に例外化し assistant-ui のエラー状態へ委譲する実装 — `chat-adapter.ts:94-95`
- [x] `abortSignal` 発火時に fetch を中断する実装 — `chat-adapter.ts:28` `signal: abortSignal` を `fetch()` に渡している
- [x] `useLocalRuntime(chatModelAdapter)` の疎通確認（単発チャットが SSE で逐次表示されること）— `frontend/src/app/assistant.tsx:36-38` で結線、`chat-adapter.test.ts` で単体確認

---

## Phase 5 — フロントエンド: 出典カード（generative UI）（スペック §5.3）

- [x] 出典カード用コンポーネントを実装（title / path / heading の表示）— `frontend/src/components/Citations.tsx`
- [ ] 回答本文中の `[n]` を出典カードへのアンカー/リンクにする実装 — `markdown-text.tsx` を確認したが本文中の `[n]` をアンカー/リンクに変換する処理は無い（Citations.tsx はカード自体をレンダリングするのみで、本文の `[n]` 文字はプレーンテキストのまま）。genuine gap
- [x] `done` 後に本文中の `[n]` 出現番号を走査し、**出現した番号のカードのみ**最終表示する間引きロジック — `Citations.tsx:31-46`
- [x] **範囲外 `[n]` の無視ガード**: citations に対応エントリの無い番号（例: `top_k` 超）はリンク化・カード化しない（§4.5/§5.3。NFR-6）— `Citations.tsx:37`
- [x] 出典カードのクリックで元ソース情報を表示する実装 — `Citations.tsx:52-73`（`<a>` に `title`/`heading` を `title` 属性、`path` を `href` に設定）
- [ ] ストリーミング中は全カードを保持しつつリンク化し、確定時に間引かれる挙動を確認するテスト/手動確認 — ロジック自体はある（`Citations.tsx:44` `isDone ? filtered : citations`）が、自動テスト・手動確認記録のいずれも見つからず
- [ ] ユニットテスト: 範囲外 `[n]`（citations に無い番号）がカード化されないことを確認 — `Citations.tsx` 用のテストファイルが存在しない（frontend の src 配下に `.test.` ファイルは `chat-adapter.test.ts` / `thread-adapter.test.ts` のみ）。genuine test gap

---

## Phase 6 — フロントエンド: スレッド一覧・再開（スペック §5.4, §4.4）

- [x] `RemoteThreadListAdapter.list()` を実装（`GET /api/conversations` に委譲）— `frontend/src/lib/thread-adapter.ts:45-57`
- [x] `RemoteThreadListAdapter.initialize()` を実装（`POST /api/conversations` に委譲）— `thread-adapter.ts:59-64`
- [x] `ThreadHistoryAdapter`（or 相当）で `GET /api/conversations/{id}` から履歴復元を実装 — `thread-adapter.ts:83-106`
- [x] `useRemoteThreadListRuntime({ runtimeHook: () => useLocalRuntime(chatModelAdapter), adapter: threadListAdapter })` で結線 — `frontend/src/app/assistant.tsx:20-40`
- [x] 会話一覧 UI（左ペイン等）を実装 — `frontend/src/components/assistant-ui/thread-list.tsx`、`assistant.tsx:45-46` の `<ThreadList />`
- [x] 会話選択→履歴復元→続きの送信が通しで動作することを確認 — **M5追記（2026-07-13）**: Docker起動の上で実DBに対し `POST /api/conversations` → `POST /api/chat`（1ターン目）→ `POST /api/chat`（2ターン目、指示語「それのバージョンは?」でcondenseを誘発）→ `GET /api/conversations/{id}` を curl で通しで実行し、履歴が user/assistant 交互に正しく保存され、2ターン目でcondenseが機能し、citationsも保存されていることを確認した。**ただし**これはAPI層での確認であり、ブラウザ上での実アプリ（フロントエンドUI）操作によるE2E確認ではない（ブラウザ操作ツールが無いため未実施）。フロントエンドの `thread-adapter.ts` はこれらのAPIをそのまま呼び出す薄いアダプタであり、アダプタ単体テスト（`thread-adapter.test.ts`）と合わせて契約面の確認としては十分と判断した
- [x] リネーム/アーカイブ/削除 UI は実装しないことを確認（スコープ外。§2.2, §4.4）— `thread-adapter.ts:31-36` `rejectNotSupported`、`thread-adapter.test.ts` の `outOfScopeMethods` で明示的に reject を検証

---

## Phase 7 — 可観測性（横断）・パフォーマンス計測（スペック §6, §7）

> span 本体は Phase 1（generate streaming）・Phase 3（condense）で配線済み。本フェーズは横断的な計測とトレースの締めに絞る。

- [x] Phase 1/3 で配線した `condense` / `generate(stream)` span が 1 チャット = 1 トレースに正しく収まることを確認（span 構成: condense→embed_query→retrieve→rerank→generate）— `api/main.py:122` `@observe()` on `chat()` がルートトレース、`retrieval/searcher.py` の `@observe(name="retrieve_context")` が embed_query/vector_search/hybrid_search/rerank をサブスパン化、`condense`/`generate_answer_stream` が generation span として同一トレース内に収まる（Langfuse のコンテキスト伝播）
- [x] `ttft_ms` をトレース属性として記録する実装 — `api/main.py:180-184`
- [ ] クライアント切断時にトレースを `cancelled` として閉じる実装 — コード内に `cancelled` の文字列・実装は見つからず。**genuine gap**（スペック §6/§4.6 が明記する挙動が未実装）
- [ ] TTFT・検索レイテンシの p95 を**初回/フォローアップ別に**実測し、暫定目標値をドキュメント化（スペック §7、§12 の `requirements.md` 反映と合わせる）— 実測値・ベースライン文書は存在しない（`requirements.md`/`docs/specs/26070718-m2_streaming_and_history/spec.md` §7 とも将来形の記述のみ）。Langfuse を有効化した実行が必要で M5 Phase 3 待ち
- [ ] （任意）範囲外 `[n]` が出た事象をトレース/ログに記録し、プロンプト品質を観測できるようにする（§4.5）— 任意項目。サーバー側のログ/トレース記録は見つからず（フロント側の表示フィルタのみ）。未実装のまま

---

## Phase 8 — 仕上げ・受け入れ確認（スペック §10, §12）

- [ ] スペック §10 の受け入れ条件をすべてチェック（ストリーミング/出典UI/マルチターン/会話履歴/assistant-ui/可観測性/共通の各項目）— 本 M5 監査（2026-07-13）で大半をチェック済みだが、`ttft_ms`/検索レイテンシの p95 ベースライン文書化・`make test` の DB 込みフル実行確認の 2 項目が未達のまま残っている（`docs/specs/26070718-m2_streaming_and_history/spec.md` §10 参照）
- [x] 生成中リロードで進行中の回答が失われる（resumeRun 非対応）ことを**非目標として許容**する旨を確認（§2.2）— スペック §2.2 の Out of scope 表に明記済み
- [x] `architecture.md` §7 を更新: `citations` 送出順（token 前）、`done` payload への `conversation_id` 追加 — `architecture.md:237-239`、v0.4 changelog（L312）
- [x] `architecture.md` §7 API 表に `POST /api/conversations` を追加 — `architecture.md:225`
- [x] `architecture.md` §7 assistant-ui マッピングに「累積 content への出典追加」の注記を追加 — `architecture.md:256`
- [x] `requirements.md` §NFR-2 に TTFT / 検索レイテンシの p95 暫定目標（実測値ベース）を追記 — `requirements.md:148-149`（※ 実測値そのものはまだ無く、m2 スペック §7 参照の注記に留まる。項目の「追記」自体はされている）
- [x] `make lint` / `make test` が通ることを最終確認 — `make lint` は 2026-07-13 時点でクリーン（exit 0）。**M5追記（2026-07-13）**: Docker起動の上で `pytest` をDB込みでフル実行し69件全通過を確認済み（DB非依存分45件＋DB接続を要する統合テスト24件を含む）
- [ ] `make eval` を実行し、M1 ベースラインからの劣化がないことを確認（結果を PR に記載。AGENTS.md §9）— 未実行（Docker 起動が前提。M5 Phase 3）
- [ ] 対応する PR に要件 ID（FR-4/FR-5/FR-6）と Eval スコア before/after を記載 — 本監査は既存実装のドキュメント整合作業でありPR横断の記載作業は対象外。未実施のまま

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.2 | 2026-07-07 | スペック v0.2 追従: Phase 2 を **done 一括保存**へ変更（user 先行保存タスクを削除、失敗ターン非保存の統合テストを追加）。generate span を Phase 1・condense span を Phase 3 へ移し、Phase 7 は横断計測に限定（span 後付けの回避）。Phase 5 に **範囲外 `[n]` 無視**の実装・テストを追加。Phase 1 の `done`→Phase 2 依存を注記。Phase 8 に resumeRun 非目標の確認を追加 |
| v0.1 | 2026-07-07 | 初版。docs/specs/26070718-m2_streaming_and_history/spec.md §13 の実装順序に基づき Phase 0〜8 のチェックリストを作成 |