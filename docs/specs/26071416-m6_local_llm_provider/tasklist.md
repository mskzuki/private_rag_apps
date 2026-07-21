# M6 タスクリスト (docs/specs/26071416-m6_local_llm_provider/tasklist.md)

> 配置先: `docs/specs/26071416-m6_local_llm_provider/tasklist.md`
> 対応スペック: `docs/specs/26071416-m6_local_llm_provider/spec.md`（v0.1、以下「スペック」）
> 進め方: 上から順に実施。各タスクに対応スペックの節番号を付記。

---

## Phase 0 — 事前実機検証（スペック §3.2, §3.3）

- [x] `ollama --version` / `ollama list` でバージョンとモデルタグを確認（Ollama v0.31.2、`qwen3.5:9b`）
- [x] `/v1/responses` に非ストリーミング・ストリーミング両方で疎通確認し、イベント型・usage形状がOpenAIと一致することを確認
- [x] 推論モデル特有の `max_output_tokens` 不足問題を発見し、対応方針（`reasoning={"effort":"none"}`。§3.3で最終確定）を確定

## Phase 1 — 設定追加（スペック §4）

- [x] `core/config.py` に `llm_provider`（既定 `"openai"`）、`ollama_base_url`、`ollama_api_key` を追加

## Phase 2 — クライアントファクトリ（スペック §3.5, §5.1）

- [x] `generation/llm_client.py` を新規作成し、`get_llm_client() -> openai.OpenAI` を実装

## Phase 3 — `generator.py` 改修（スペック §3.3, §5.2）

- [x] `condense()`/`generate_answer_stream()` の client 生成部分を `get_llm_client()` に置換
- [x] `settings.llm_provider == "ollama"` のときのみ `reasoning={"effort": "none"}` を `responses.create(...)` に渡すよう分岐を追加（`max_output_tokens` は実測の結果引き上げ不要と判明し、OpenAIと同じ値のまま）
- [x] `condense()` に `output_text` が空/空白のみの場合のクエリへのフォールバックを追加
- [x] 引用生成・早期return・イベントループ・usage記録・`except` 節は無改修であることを確認

## Phase 4 — `.env.example`（スペック §5.3）

- [x] `LLM_PROVIDER`/`OLLAMA_BASE_URL`/`OLLAMA_API_KEY` をコメントアウト状態で追記し、Ollama利用時の設定例を記載

## Phase 5 — テスト（スペック §6）

- [x] 既存2テスト（`test_generate_answer_stream_with_chunks`, `test_condense_with_history`）のパッチ対象を `get_llm_client` に更新
- [x] `get_llm_client()` のプロバイダ別 base_url/api_key を検証する新規テストを追加
- [x] Ollamaプロバイダ時に `reasoning={"effort":"none"}` が渡ることを検証する新規テストを追加、および `condense()` の空出力フォールバックを検証するテストを追加
- [x] `cd backend && uv run pytest tests/test_chat.py -v` で全テスト通過を確認（9件通過）

## Phase 6 — 検証・クローズ（スペック §7, §8）

- [x] `make lint` が通る（backend: ruff + mypy 問題なし。frontend: 既存の未関連警告2件のみ）
- [x] `make test` が通る（backend 73件通過）
- [x] 手動検証（スペック §7）: `generator.py` の `condense()`/`generate_answer_stream()` を実データで直接呼び出し、Ollama(qwen3.5:9b)で正常動作（ストリーミング・引用マーカー・condense・空回答なし）することを確認
- [x] 手動検証: `LLM_PROVIDER` を既定に戻し、`settings.llm_provider` が `"openai"` になることを確認
- [x] スペック §8 受け入れ条件を全てクローズ（フロントエンド/retrieval経由のE2EはVoyageレート制限により未実施。スペック §7 に明記済みで、機能実装自体の受け入れ条件はクローズ扱いとする）
