# M2 タスクリスト (m2_tasklist.md)

> 配置先: `docs/specs/m2_tasklist.md`
> 対応スペック: `docs/specs/m2_streaming_and_history.md`（以下「スペック」）
> 進め方: 上から順に実施する。各タスクは対応するスペックの節番号を付記。
> 各フェーズの末尾で `make lint` / `make test` を通し、RAG 挙動（condense プロンプト追加）に触れたら `make eval` を実行する（AGENTS.md §7/§10）。

---

## Phase 0 — 準備

- [ ] スペック §12「未決事項」を確認し、着手前に以下を決定する
  - [ ] 出典を assistant-ui の「カスタムパート」で持つか「メッセージ metadata」で持つか（導入版 API を確認して決定）
  - [ ] 検索 0 件の定型応答を履歴に保存するか（既定: 保存しない。変える場合はスペックを更新）
  - [ ] condense の発見的スキップ条件をどこまで作り込むか（M2 は「初回ターンのみスキップ」を最低ラインとする）
- [ ] 決定事項をスペック §12 に反映する（differ があれば先にスペックを更新してから実装、AGENTS.md §12）

---

## Phase 1 — バックエンド: `/api/chat` の SSE 化（スペック §4.1, §4.6）

- [ ] `sse-starlette`（or 同等）を導入し、`/api/chat` を `StreamingResponse` / `EventSourceResponse` に変更
- [ ] レスポンスヘッダーにバッファリング無効化設定を追加（`X-Accel-Buffering: no` 等）
- [ ] keepalive コメント（`: ping`）を `SSE_KEEPALIVE_SEC` 間隔で送出する実装
- [ ] `citations` イベント（rerank 直後、番号付き出典配列。§4.5）を実装
- [ ] `token` イベント（生成トークンごとの delta 配信）を実装
- [ ] `done` イベント（`message_id`, `conversation_id` を含む）を実装
      - ※ `message_id` の本採番は Phase 2（永続化）に依存。Phase 1 では暫定値/stub で配線し、Phase 2 で実 ID に結線する
- [ ] `error` イベント（生成/検索の回復不能な失敗時）を実装
- [ ] クライアント切断（`abortSignal` 相当）検知時に LLM ストリームを中断する実装
- [ ] **generate（streaming）span を同時に配線**（開始〜完了で閉じ、トークン数/コスト/レイテンシを完了時に確定記録。NFR-4。後付けにしない＝AGENTS.md §7）
- [ ] 検索 0 件時のフォールバック（生成スキップ→定型文を `token` で1度→`citations: []`→`done`）
- [ ] ユニットテスト: SSE イベント整形（citations/token/done/error のペイロード形状）
- [ ] ユニットテスト: 検索 0 件時のフォールバック応答

---

## Phase 2 — バックエンド: 会話の永続化 + 管理エンドポイント（スペック §4.2, §4.4, §8）

- [ ] `POST /api/conversations`（空会話作成。`RemoteThreadListAdapter.initialize()` 対応）を実装
- [ ] `GET /api/conversations`（一覧。`updated_at DESC`）を実装
- [ ] `GET /api/conversations/{id}`（履歴。messages を昇順、role/content/citations を含む）を実装
- [ ] `/api/chat` に `conversation_id` 省略時の遅延作成ロジックを実装（CLI/テスト経路用）
- [ ] **done 一括保存**を実装: `done` 直前に user + assistant メッセージ + citations を **1 トランザクションで INSERT**（正常終了時のみ。user 先行保存はしない）
- [ ] エラー・キャンセル時は user / assistant いずれも保存しないことを実装（失敗ターンは履歴に残さない。§4.2）
- [ ] 一括保存と同一トランザクションで `conversations.updated_at` を更新する実装
- [ ] タイトル自動設定（最初の user メッセージ先頭を `TITLE_MAX_CHARS` で切り詰め。LLM 呼び出しなし）
- [ ] `core/config.py` に `TITLE_MAX_CHARS` を追加（ハードコード禁止。AGENTS.md §6）
- [ ] Phase 1 の `done` の `message_id` を、ここで採番した実 ID に結線
- [ ] 統合テスト: `POST /api/conversations` → `POST /api/chat`(SSE) → `GET /api/conversations/{id}` の一連が通ることをテスト DB で検証
- [ ] 統合テスト: **エラー/キャンセル発生ターンで user / assistant とも保存されず**、履歴が user↔assistant の交互列を保つことを確認
- [ ] 統合テスト: 一覧が `updated_at DESC` で返ることを確認

---

## Phase 3 — バックエンド: condense（マルチターン クエリ書き換え）（スペック §4.3, §9）

- [ ] `generation` レイヤに condense 関数を追加（LLM 呼び出しは `generation` のみ。AGENTS.md §3 依存方向を確認）
- [ ] condense プロンプトを `prompts/` に追加（コードへのハードコード禁止）
- [ ] `core/config.py` に `CONDENSE_MODEL` / `CONDENSE_HISTORY_TURNS` を追加し、使用モデル名をトレースに記録
- [ ] **condense span を同時に配線**（トークン数/コスト/レイテンシを記録。初回スキップ時は span を生成しない。NFR-4／後付けにしない＝AGENTS.md §7）
- [ ] 初回ターン（履歴なし）は condense をスキップし、`message` をそのまま検索クエリにする実装
- [ ] condense 失敗時のフォールバック（元メッセージをそのまま検索クエリにして継続。§4.6）
- [ ] `CHAT_HISTORY_TOKEN_BUDGET` を設定化し、生成プロンプトへの履歴切り詰めに使用
- [ ] ユニットテスト: 初回ターンで condense が呼ばれないことを確認
- [ ] ユニットテスト: フォローアップ入力（指示語あり）で履歴を用いたクエリに書き換わることを確認（LLM はモック/記録再生）
- [ ] ユニットテスト: condense 失敗時に元メッセージへフォールバックすることを確認
- [ ] `make eval` を実行し、条件分岐追加前後で単一ターン生成の回帰がないことを確認（AGENTS.md §7）

---

## Phase 4 — フロントエンド: ChatModelAdapter（SSE 受信・累積 yield）（スペック §5.1, §5.2）

- [ ] `ChatModelAdapter.run({ messages, abortSignal })` を実装し `/api/chat` へ POST
- [ ] SSE レスポンスを読み取るパーサ（`event:`/`data:` 行の分解）を実装
- [ ] 累積状態（テキスト・出典パート）を**ループ外**で保持する実装（チャンクごとの content 再生成を避ける。既知の落とし穴に注意）
- [ ] `citations` 受信時に出典パートを content に一度だけ追加する実装
- [ ] `token` 受信ごとに累積テキストを更新し、`content:[text, …出典パート]` を yield する実装
- [ ] `done` 受信時に `message_id` / `conversation_id` を確定し、ストリームを終了する実装
- [ ] `error` 受信時に例外化し assistant-ui のエラー状態へ委譲する実装
- [ ] `abortSignal` 発火時に fetch を中断する実装
- [ ] `useLocalRuntime(chatModelAdapter)` の疎通確認（単発チャットが SSE で逐次表示されること）

---

## Phase 5 — フロントエンド: 出典カード（generative UI）（スペック §5.3）

- [ ] 出典カード用コンポーネントを実装（title / path / heading の表示）
- [ ] 回答本文中の `[n]` を出典カードへのアンカー/リンクにする実装
- [ ] `done` 後に本文中の `[n]` 出現番号を走査し、**出現した番号のカードのみ**最終表示する間引きロジック
- [ ] **範囲外 `[n]` の無視ガード**: citations に対応エントリの無い番号（例: `top_k` 超）はリンク化・カード化しない（§4.5/§5.3。NFR-6）
- [ ] 出典カードのクリックで元ソース情報を表示する実装
- [ ] ストリーミング中は全カードを保持しつつリンク化し、確定時に間引かれる挙動を確認するテスト/手動確認
- [ ] ユニットテスト: 範囲外 `[n]`（citations に無い番号）がカード化されないことを確認

---

## Phase 6 — フロントエンド: スレッド一覧・再開（スペック §5.4, §4.4）

- [ ] `RemoteThreadListAdapter.list()` を実装（`GET /api/conversations` に委譲）
- [ ] `RemoteThreadListAdapter.initialize()` を実装（`POST /api/conversations` に委譲）
- [ ] `ThreadHistoryAdapter`（or 相当）で `GET /api/conversations/{id}` から履歴復元を実装
- [ ] `useRemoteThreadListRuntime({ runtimeHook: () => useLocalRuntime(chatModelAdapter), adapter: threadListAdapter })` で結線
- [ ] 会話一覧 UI（左ペイン等）を実装
- [ ] 会話選択→履歴復元→続きの送信が通しで動作することを確認
- [ ] リネーム/アーカイブ/削除 UI は実装しないことを確認（スコープ外。§2.2, §4.4）

---

## Phase 7 — 可観測性（横断）・パフォーマンス計測（スペック §6, §7）

> span 本体は Phase 1（generate streaming）・Phase 3（condense）で配線済み。本フェーズは横断的な計測とトレースの締めに絞る。

- [ ] Phase 1/3 で配線した `condense` / `generate(stream)` span が 1 チャット = 1 トレースに正しく収まることを確認（span 構成: condense→embed_query→retrieve→rerank→generate）
- [ ] `ttft_ms` をトレース属性として記録する実装
- [ ] クライアント切断時にトレースを `cancelled` として閉じる実装
- [ ] TTFT・検索レイテンシの p95 を**初回/フォローアップ別に**実測し、暫定目標値をドキュメント化（スペック §7、§12 の `requirements.md` 反映と合わせる）
- [ ] （任意）範囲外 `[n]` が出た事象をトレース/ログに記録し、プロンプト品質を観測できるようにする（§4.5）

---

## Phase 8 — 仕上げ・受け入れ確認（スペック §10, §12）

- [ ] スペック §10 の受け入れ条件をすべてチェック（ストリーミング/出典UI/マルチターン/会話履歴/assistant-ui/可観測性/共通の各項目）
- [ ] 生成中リロードで進行中の回答が失われる（resumeRun 非対応）ことを**非目標として許容**する旨を確認（§2.2）
- [ ] `architecture.md` §7 を更新: `citations` 送出順（token 前）、`done` payload への `conversation_id` 追加
- [ ] `architecture.md` §7 API 表に `POST /api/conversations` を追加
- [ ] `architecture.md` §7 assistant-ui マッピングに「累積 content への出典追加」の注記を追加
- [ ] `requirements.md` §NFR-2 に TTFT / 検索レイテンシの p95 暫定目標（実測値ベース）を追記
- [ ] `make lint` / `make test` が通ることを最終確認
- [ ] `make eval` を実行し、M1 ベースラインからの劣化がないことを確認（結果を PR に記載。AGENTS.md §9）
- [ ] 対応する PR に要件 ID（FR-4/FR-5/FR-6）と Eval スコア before/after を記載

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.2 | 2026-07-07 | スペック v0.2 追従: Phase 2 を **done 一括保存**へ変更（user 先行保存タスクを削除、失敗ターン非保存の統合テストを追加）。generate span を Phase 1・condense span を Phase 3 へ移し、Phase 7 は横断計測に限定（span 後付けの回避）。Phase 5 に **範囲外 `[n]` 無視**の実装・テストを追加。Phase 1 の `done`→Phase 2 依存を注記。Phase 8 に resumeRun 非目標の確認を追加 |
| v0.1 | 2026-07-07 | 初版。m2_streaming_and_history.md §13 の実装順序に基づき Phase 0〜8 のチェックリストを作成 |