# Private RAG Apps — M6 フィーチャースペック: ローカルLLMプロバイダ（Ollama/Qwen3.5）切り替え (m6_local_llm_provider.md)

> 配置先: `docs/specs/m6_local_llm_provider.md`
> 対象マイルストーン: **M6**（M0〜M5 完了後の追加拡張。requirements.md には未記載のため、本スペックが一次ソース）
> 上位ドキュメント: 要件=`requirements.md`(v0.6)、構成=`architecture.md`(v0.5)、物理設計=`db_design.md`(v0.3)、規約=`AGENTS.md`(v0.9)。
> **矛盾時の優先順位**（AGENTS.md 冒頭）: 本スペック > AGENTS.md > 一般慣習。

---

## 1. 目的と背景

ユーザーがローカルで Ollama + Qwen3.5 を起動済みであり、これを本プロジェクトの生成（generation）部分で使えるようにしたい。目的は **本番のOpenAI利用を置き換えることではなく、ローカル開発・検証時にAPI課金なしで生成パスを試せる選択肢を追加すること**。

AGENTS.md §2 の技術スタックは現在 OpenAI GPT（生成専用）を前提としており、Ollama/ローカルLLMへの言及はどの `docs/specs/` にも無い（M0〜M5 いずれにも未記載）。AGENTS.md §11「仕様に無い機能を勝手に追加しない」に従い、本スペックを実装前に作成する。

---

## 2. スコープ

### 2.1 In scope

- `backend/src/private_rag_apps/generation/generator.py` の `condense()` と `generate_answer_stream()` に、設定で切り替え可能な Ollama 経由の呼び出しパスを追加する。
- `core/config.py` へのプロバイダ選択設定の追加。
- `.env.example` への設定例の追記。
- 対応するユニットテストの追加・更新（`backend/tests/test_chat.py`）。

### 2.2 Out of scope

| 項目 | 理由 |
|---|---|
| `evals/judge.py`（LLM-as-judge） | judge のスコアは `backend/evals/baselines/current.json` のコミット済みベースラインと比較される。judge モデルを変えるとベースラインの比較可能性が失われるため、judge は常に OpenAI 固定とする |
| Voyage 埋め込み・リランク | 本スペックは生成（LLM呼び出し）のみが対象。埋め込みモデルを変えると既存インデックスと非互換になる（AGENTS.md §7）ため対象外 |
| 新規 API エンドポイント・UI でのプロバイダ切り替えトグル | v1 は `.env`/設定ファイルでの切り替えのみ。ランタイムでの動的切り替えは行わない |
| プラグイン機構・プロバイダ抽象化インターフェース（ABC/Protocol 等） | 対応プロバイダは OpenAI/Ollama の2種、呼び出し箇所も2箇所のみであり、過剰な抽象化は導入しない（CLAUDE.md の premature abstraction 回避方針） |
| Ollama 自体のインストール・モデルのpull | ユーザー側の環境構築（`ollama pull` 等）は本スペックの対象外。前提として済んでいるものとする |
| Chat Completions API（`/v1/chat/completions`）への形状変換 | §3.2 の実機検証により不要と判明 |

---

## 3. 設計判断

### 3.1 プロバイダ選択は単一の設定値で行う

`llm_provider: Literal["openai", "ollama"]`（既定 `"openai"`）を `core/config.py` に追加し、`condense()`/`generate_answer_stream()` の両方がこの値を見て分岐する。プロセス起動時に一度読み込まれる `settings` を参照するのみで、リクエスト単位の動的切り替えは行わない。

### 3.2 ★ Ollama は Responses API を既にサポートしている（実機検証済み）

実装前に Ollama v0.31.2 + `qwen3.5:9b` に対して `curl` および `openai` Python SDK で `/v1/responses` を直接検証し、以下を確認した:

- **非ストリーミング**: `client.responses.create(model=..., input=..., instructions=..., max_output_tokens=...)` がそのまま動作し、`response.output_text` で結果を取得できる。`response.usage.input_tokens`/`response.usage.output_tokens` も OpenAI と同一のフィールド名で返る。
- **ストリーミング**: イベント型は OpenAI と完全に同一（`response.output_text.delta`、`response.completed` 等）。既存コードの `if event.type == "response.output_text.delta":` フィルタがそのまま機能する。
- **推論（思考）内容の分離**: Qwen3.5 は推論モデルであり、思考過程は `response.reasoning_summary_text.delta` という**別のイベント型**で流れる。既存コードは `response.output_text.delta` のみを拾うため、思考内容が誤ってチャット画面に漏れ出ることはない。
- 非対応なのはステートフル機能（`previous_response_id`/`conversation`）のみ。`generator.py` はそもそも毎回フルコンテキストを渡す作りでこれらを使っていないため、影響なし。

**結論**: Chat Completions API への形状変換は不要。`client = openai.OpenAI(...)` の生成部分（`base_url`/`api_key`）をプロバイダに応じて切り替えるだけで、`responses.create(...)` 呼び出し自体は流用できる。

### 3.3 ★ 推論モデル特有の `max_output_tokens` 不足問題（実機で再現・`reasoning: none` で解決）

Qwen3.5 は既定で回答前に長い思考過程を生成するため、**思考だけで `max_output_tokens` を使い切り、実際の回答（`output_text`）が空文字になる**現象を実機で再現した:

- `reasoning` パラメータ未指定・`max_output_tokens=200`: 出力200トークンが全て思考に消費され `output_text == ''`
- `reasoning={"effort": "low"}` を指定しても思考トークンは減る（342〜591トークン程度）だけでゼロにはならず、実際の `condense()` プロンプト（会話履歴込み）では `max_output_tokens=3072` でも空回答になるケースを確認した
- **`reasoning={"effort": "none"}`** を指定すると思考ステップ自体が行われなくなり、既存の `max_output_tokens`（condense=256、generate_answer_stream=1024）のままでも安定して空でない回答が得られることを実機確認した（condense: 21〜27トークンで完結、generate: 60トークン程度で完結。Ollamaが返す `reasoning.effort` の有効値は `"high"/"medium"/"low"/"max"/"none"` で `"minimal"` は無効値としてエラーになる）

**対応方針**: `llm_provider == "ollama"` のときのみ `reasoning={"effort": "none"}` を付与する。`max_output_tokens` はOpenAIと同じ値のまま変更しない（値を引き上げる案は実測の結果不要と判明したため採用しない）。

これは `responses.create(...)` に渡す kwargs の値がプロバイダによって変わるだけであり、呼び出し形状（API・イベントループ）自体は変わらない。OpenAI側の値・挙動は無変更。

**追加の保険（`condense()` のみ）**: 推論モデル特有の非決定性に備え、`output_text` が空/空白のみだった場合は例外扱いにせず、既存の `except` ブロックと同様に元のクエリへフォールバックするガードを追加した（`response.output_text.strip() or query`）。

### 3.4 モデル名は既存の `llm_model`/`condense_model` を再利用

Ollama用に専用の設定フィールド（例: `ollama_model`）は追加しない。`llm_provider="ollama"` のときは `llm_model`/`condense_model` にOllamaのモデルタグ（例: `qwen3.5:9b`）を設定する運用とし、設定サーフェスを増やさない。

### 3.5 クライアント生成の集約

`openai.OpenAI()` のインスタンス化ロジック（api_key/base_urlの選択）を `generation/llm_client.py`（新規）の `get_llm_client()` に集約する。インターフェース抽象化はせず、単一のファクトリ関数のみを追加する（§2.2 参照）。

---

## 4. 設定

`core/config.py` に追加する設定（`openai_api_key` 付近、既存の `Literal` インポート済みスタイルに合わせる）:

| 設定名 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `llm_provider` | `Literal["openai", "ollama"]` | `"openai"` | 生成に使うLLMプロバイダ。`condense`/`generate_answer_stream` のみ対象。judge は対象外 |
| `ollama_base_url` | `str` | `"http://localhost:11434/v1"` | Ollama の OpenAI互換エンドポイント（`/v1/responses`）。`llm_provider="ollama"` のときのみ使用 |
| `ollama_api_key` | `str` | `"ollama"` | Ollama はAPIキー不要だが `openai` SDKがダミー値を要求するため固定値を設定 |

`.env` 対応する環境変数: `LLM_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`（いずれも任意。未設定なら OpenAI 既定動作のまま変更なし）。

`max_output_tokens`/`reasoning` のプロバイダ別調整値（§3.3）は、既存の `max_output_tokens` 呼び出し箇所そのものが `core/config.py` を経由しないハードコード値であるため、本対応でも同様にコード内の定数として扱う（新規の設定キーは追加しない。将来チューニングが必要になった時点で config 化を検討）。

---

## 5. 実装方針

1. `generation/llm_client.py`（新規）: `get_llm_client() -> openai.OpenAI` のみ（プロバイダに応じて base_url/api_key を切り替える単一ファクトリ関数）。
2. `generator.py`: 両関数の `client = openai.OpenAI(api_key=settings.openai_api_key)` を `client = get_llm_client()` に置換。`responses.create(...)` の呼び出し箇所で、`settings.llm_provider == "ollama"` のときのみ `reasoning={"effort": "none"}` を渡す（§3.3）。`max_output_tokens` はOpenAI/Ollama共通のまま。イベントループ・usage記録・引用生成・早期return・`except` 節は無改修。`condense()` のみ `response.output_text.strip() or query` として空出力への保険を追加。
3. `.env.example`: `LLM_MODEL`/`CONDENSE_MODEL` の下にコメントアウトされたオプション設定として追記。

---

## 6. テスト方針

- `backend/tests/test_chat.py` の既存 OpenAI パステスト2件は、パッチ対象を `openai.OpenAI` から `get_llm_client` に更新する（モック本体はそのまま）。
- 新規テスト: `get_llm_client()` が `settings.llm_provider` に応じて正しい `base_url`/`api_key` を渡すことの確認、`monkeypatch.setattr(settings, "llm_provider", "ollama")` の状態で `condense()`/`generate_answer_stream()` が `reasoning={"effort": "none"}` 付きで `responses.create` を呼ぶことの確認、`output_text` が空白のみの場合に `condense()` が元のクエリへフォールバックすることの確認。
- すべてモックのみで完結し、実ネットワーク呼び出し（Ollamaサーバー・OpenAI API）は行わない（AGENTS.md §8）。

---

## 7. 手動検証手順（実施済み）

1. `ollama list` で使用するモデルタグを確認する（本セッションでは `qwen3.5:9b`、Ollama v0.31.2）。
2. `LLM_PROVIDER=ollama`/`LLM_MODEL=qwen3.5:9b`/`CONDENSE_MODEL=qwen3.5:9b` を設定し、`generate_answer_stream()`/`condense()` を実データ（citation付きchunk・会話履歴）で直接呼び出して検証。ストリーミングでトークンが逐次流れ、引用マーカー `[1]` を含む自然な回答が得られ、`condense()` も履歴を踏まえた自己完結クエリを返すことを確認。思考内容（`response.reasoning_summary_text.delta`）はイベント型フィルタにより画面に漏れないことをコードレベルで確認済み（§3.2）。
3. `LLM_PROVIDER` を既定（`openai`、未設定）に戻し、`settings.llm_provider` が `"openai"` に戻ることを確認（切替可能であることの核心チェック）。
4. **未実施（環境起因）**: フロントエンド経由のE2E確認、および retrieval を経由した本物のチャットフロー（`POST /api/chat`）でのOllama確認は、Voyage APIのレート制限（課金未設定によるもの。既知の環境ブロッカー）で seed corpus のインデックスが作成できず実施できなかった。`generator.py` の関数自体は実データで検証済みのため機能上の懸念はないが、Voyageの課金設定後に retrieval を含めたE2Eを再確認することが望ましい。

---

## 8. 受け入れ条件

- [x] `llm_provider`/`ollama_base_url`/`ollama_api_key` が `core/config.py` に追加され、`.env.example` に説明付きで記載されている
- [x] `condense()`/`generate_answer_stream()` が `llm_provider` に応じて client を正しく切り替え、Ollama時は `reasoning={"effort":"none"}` を付与する
- [x] `evals/judge.py` は無改修（OpenAI固定のまま）
- [x] 新規 LLM 呼び出し箇所が `generation/` の外に増えていない（AGENTS.md §3 依存方向）
- [x] 既存テストがパッチ更新のうえ通過し、Ollamaパスの新規テストが追加されている（9件通過）
- [x] `make lint` / `make test` が通る
- [x] 手動検証（§7）を実施し、`generator.py` レベルでOllama/OpenAI 双方の動作を確認した。retrieval経由のフルE2E（`POST /api/chat`）はVoyage APIレート制限により未実施（§7 に記録）

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.2 | 2026-07-14 | 実装・実機検証を反映。`max_output_tokens` 引き上げ案を撤回し、`reasoning={"effort": "none"}` により既存の値のまま解決できることを確認（§3.3）。`condense()` に空出力時のクエリへのフォールバックを追加。手動検証（§7）を実施し結果を記録。retrieval経由のE2E確認はVoyageレート制限により未実施であることを明記 |
| v0.1 | 2026-07-14 | 初版。Ollama/Qwen3.5 をローカル開発用のオプトインプロバイダとして `generation/` に追加する設計を定義。実機検証（Ollama v0.31.2 + qwen3.5:9b）により Responses API がそのまま流用できることを確認する一方、推論モデル特有の `max_output_tokens` 不足問題を発見し対応方針を明記。judge は対象外 |
