# M7: Adaptive Routing — LangGraph によるクエリ書き換えと回答経路制御

- Status: Accepted (rev.5 — holdout direct 誤りの部分再取得により GO 判定に復帰。ADR 0006)
- Depends on: M2 (SSE streaming), M3 (conversation history), M4 (evaluation), M6 (LLM provider abstraction。rewrite は `generation.condense()` 経由でこれに乗る), 既存 retrieval pipeline (hybrid search + rerank)
  - **注: 上記の実装完了状況はタスク T0 で着手前に確認する（スペック群の存在 ≠ 実装完了）**
- Blocked by: なし
- Blocks: M8 候補 (clarify / HITL / LLM grader。ADR 0006 により GO 達成のため exit criteria 非該当。任意の将来改善検討として位置づけ)

## 改訂履歴

- rev.5: 2026-07-17 ユーザー指示により holdout の direct 誤り4件（`g014`,`g037`,`a019`,`a028`）を含む未検証 None 件を Voyage API に再取得した結果、holdout direct 誤りが 4/18→2/18（基準 ≤3 件）に減少し、判定が **NO-GO→GO** に反転（THETA=0.56 は不変）。`g014`/`g037` は Voyage 失敗による誤判定と判明し訂正、`a019`(0.707)/`a028`(0.574) は実スコア取得の上で正当なグレーゾーン誤判定と判明。GO 達成により §7.4 の LLM grader 昇格 exit criteria はもはや満たされず、起票は必須から任意の将来検討に位置づけを変更。§7.2 の実績注記を更新
- rev.4: 2026-07-16 T7（可観測性の仕上げとドキュメント確定）で M7 実装完了に伴い Draft → Accepted に更新。(1) §6 に Langfuse 計装の実装詳細を追記（grade のみ明示的 span を追加、他3ノードは既存 `@observe()` 済み下位関数への自動ネストで充足、trace レベル metadata は `update_current_trace()` ではなく `propagate_attributes()` を使用。インストール済み SDK に前者が存在しないため）。(2) §7.2 に実績値と ADR 参照の注記を追加（direct 誤り 4/18 で NO-GO・ADR 0005 で受理済み、補足書式 3/5 の既知の制約）。(3) §10 の未決事項3件をすべて解消・記録（N=5 のまま不変・レイテンシ超過はM8持ち越し、補足書式の最終文言、judge のモデル/プロンプト所在）。(4) `docs/specs/26071422-m7_adaptive_routing/tasklist.md` の T7 完了条件・全体の完了定義を、ADR 0004（`make eval` 対象外）・ADR 0005（NO-GO 受理）と整合する記述に修正
- rev.3: 2026-07-14 実装着手前レビューで現行コードとの差分が判明し反映。(1) rewrite は新規 Anthropic クライアントではなく既存 `generation.condense()` を再利用・拡張する方式に変更（§3.3, §4.3）。(2) グラフ層は新規トップレベルパッケージ `private_rag_apps/graph/` とし、AGENTS.md §3 改訂を T3 のタスクに追加（§4.1）。(3) grade の前提として `retrieval/searcher.py::_rerank()` に `rerank_score` を追加する最小限の変更が必要であることを明記（§4.3 grade。retrieval 内部無変更方針の唯一の例外）。(4) generate への履歴注入に関する誤った前提（rev.1/rev.2 とも「既存実装済み」としていたが実際は未実装）を訂正（§4.3 generate）。(5) SSE 既存イベント名を実装に合わせ `citation`→`citations` に修正（§5.2）
- rev.2: THETA_HIGH を廃止し grade を 2 値化（route は grounded / direct のみ）。「一般知識に基づく補足」は経路から grounded プロンプト内の書式ルールに変更。用語定義（§2）を新設。eval データセットの calibration / holdout 分割を導入。その他レビュー指摘の反映
- rev.1: 初版

---

## 1. 背景と目的

現状、すべての質問が無条件に retrieval パイプラインを通る。これには 2 つの問題がある。

1. 一般知識の質問（例: Python の文法、統計の一般論）に対しても検索・rerank・context 注入が走り、レイテンシとトークンを浪費する
2. コーパスに無関係な chunk が context に混入し、回答品質を下げる、あるいはコーパス由来であるかのような誤帰属を生む

M7 では LangGraph を導入し、**クエリ書き換え → 検索 → 関連度判定（grade）→ 経路分岐** という適応的なフローを実装する。回答は常に「コーパスに根拠があるか」を判定した上で生成される。

### スコープ

**In:**
- LangGraph によるグラフオーケストレーション基盤の導入
- 会話履歴を考慮したクエリ書き換え（rewrite ノード）
- 常時検索 + rerank score 閾値（THETA）による grade（使用可否判定）
- 2 経路の回答生成: `grounded` / `direct`。grounded 内での「一般知識に基づく補足」書式（§2 参照）
- SSE プロトコルへのグラフ実行イベントの追加（後方互換）
- routing eval データセット（calibration / holdout 分割）と `make eval` ターゲットの新設

**Out（明示的に非スコープ）:**
- clarify / 聞き返し（HITL）→ M8 候補
- LangGraph checkpointer の導入 → M8 で HITL と同時に検討
- LLM grader → 閾値方式の eval 結果を見て判断（§7.4 の exit criteria 参照）
- マルチターンのエージェント的探索（再検索ループ）
- LangChain の LLM 抽象（ChatAnthropic 等）の採用 → 恒久的に不採用（§4 参照）

---

## 2. 用語定義

本スペックおよび M7 関連文書で使う用語を定義する。実装・eval・UI 表示はこの定義に従う。

### chunk / rerank score

**chunk** はコーパス（プロジェクト設計ドキュメント群）を分割した検索単位。retrieval パイプラインは hybrid search（pgvector + pg_bigm → RRF）で候補 chunk を集め、reranker が各 chunk に **rerank score**（クエリとの関連度スコア）を付与して降順に返す。M7 はこの既存パイプラインの出力を入力として扱い、内部には変更を加えない。

### THETA（θ）

chunk を回答の根拠として**採用するかどうかの足切り閾値**。rerank score が THETA 以上の chunk のみを残し（= `kept`）、未満は捨てる。M7 の grade で使う閾値は**この 1 本のみ**。

- 値はハードコードせず config 管理とする
- 初期値は routing eval データセット（calibration split）上でのキャリブレーション（§7）により決定する
- reranker のスコア分布はモデル・コーパス依存であり、**絶対値に意味を求めない**。eval セットに対するキャリブレーション結果のみを根拠とする
- 誤判定コストの非対称性（§3.1）により、キャリブレーションは「grounded の見逃し最小化」を優先する（迷ったら低めに引く）

### grounded（経路）

**足切りを生き残った chunk が 1 件以上ある**場合の回答経路。kept chunks を context として注入し、引用付きで回答を生成する。

質問の一部が context でカバーできない場合、その部分は回答内の独立した「**一般知識に基づく補足**」セクション（後述）で補う。**カバレッジの判定（context で答えられる範囲の見極め）は grade ではなく generate 時の LLM が行う**。数値閾値では質問へのカバレッジを測定できないため（rev.2 での設計変更。詳細は §5.3 generate）。

### direct（経路）

**足切りの結果、kept が 0 件**（= コーパスに関連する記述が見つからなかった）場合の回答経路。context 注入なしで LLM の一般知識のみから回答する。

direct 経路では**コーパスの内容に言及してはならない**（記憶からの捏造防止）。ドキュメント固有の質問と思われる場合は「ドキュメントに該当する記述が見つからなかった」旨を回答内で伝える。

### 一般知識に基づく補足（書式ルール。経路ではない）

grounded 回答内で、context に根拠のない内容を補う場合の**必須書式**。「---」区切りの見出し付きセクションとして本文と明示的に分離し、セクション内では引用マーカーを使用しない。これは grade が決める経路ではなく、**grounded プロンプトが generate に課す出力規約**である。

### 経路判定の全体像

```
retrieval が返した chunks の rerank score
        │
        ▼
  score >= THETA の chunk を kept に残す
        │
   ┌────┴─────┐
 kept == 0   kept >= 1
   │            │
 direct      grounded
（一般知識のみ） （引用付き。カバー外は「補足」セクションで補う）
```

---

## 3. 設計原則

### 3.1 誤判定コストの非対称性（最重要）

- **grounded にすべき質問を direct に流す誤り = 致命的**。コーパスに関するハルシネーションが発生し、プライベート RAG の存在意義が毀損される
- **direct でよい質問を grounded に流す誤り = 軽微**。無関係 chunk の混入リスクとトークン浪費に留まる（かつ THETA の足切りで大半は防がれる）

したがって、**迷ったら grounded 側に倒す**。THETA・プロンプト・eval 指標のすべてがこの原則から導出される。grade の主要指標は accuracy ではなく **「grounded にすべき質問の見逃し数」** とする。

### 3.2 Single Source of Truth

- 会話履歴の正は既存の conversation テーブル（M3）。LangGraph は状態を永続化しない（checkpointer 不使用）
- グラフは 1 リクエスト = 1 実行のステートレスな関数として扱う

### 3.3 LangGraph は薄く使う

- LangGraph の責務は **状態機械・分岐・イベント伝搬のみ**
- LLM 呼び出しは既存の generation/ 層（`get_llm_client()`。M6 で導入した OpenAI 互換クライアント。設定で OpenAI/Ollama を切替）をそのまま使う。prompt cache 制御、streaming ハンドリング、Langfuse span 設計を既存実装のまま保つため
- rewrite ノード（§4.3）も新規 LLM プロバイダを追加せず、既存の `generation.condense()` を再利用・拡張する。Anthropic 等の新規依存は本スペックのスコープでは追加しない（rev.3）
- `langgraph` のみ依存に追加し、`langchain` / `langchain-anthropic` は追加しない

### 3.4 将来の checkpointer 導入に備えた制約

M8 で HITL を導入する場合に `PostgresSaver` を局所変更で差し込めるよう、以下を実装規約とする。

- State は JSON シリアライズ可能な `TypedDict` に限定する。DB コネクション、HTTP クライアント、Langfuse オブジェクト等を State に入れない（ノード関数がクロージャ / DI で保持する）
- 履歴のロード / 永続化はグラフの**外**（FastAPI ハンドラ層）に置き、グラフ本体を純粋に保つ

### 3.5 eval の独立性

- 閾値の決定（calibration）と合格判定（holdout）に同一データを使わない（§7.1）
- eval の再現性を確保する: routing 判定に関与する LLM 呼び出し（rewrite）は temperature=0 とし、eval 実行はキャッシュ機構で決定的に再現できるようにする（§7.3）

---

## 4. アーキテクチャ

### 4.1 グラフ構造

```
                    ┌─────────┐
 (history + query)  │ rewrite │  会話履歴を考慮した検索クエリ生成
        ──────────▶ └────┬────┘
                         │
                    ┌────▼─────┐
                    │ retrieve │  既存 hybrid search + rerank（常時実行）
                    └────┬─────┘
                         │
                    ┌────▼────┐
                    │  grade  │  THETA で chunk を filter（純関数）
                    └────┬────┘
                         │ conditional edge
                ┌────────┴────────┐
             kept == 0        kept >= 1
                │                 │
          ┌─────▼────┐     ┌─────▼─────┐
          │ generate │     │ generate  │
          │ (direct) │     │ (grounded)│
          └─────┬────┘     └─────┬─────┘
                └────────┬───────┘
                       (END)
```

generate は 1 ノードとし、`state["route"]` によってプロンプトテンプレートを切り替える（2 ノードに分割しない。プロンプト以外のロジックが共通のため）。

**パッケージ配置（rev.3）:** グラフ実装は `generation/`・`retrieval/` と並ぶ新規トップレベルパッケージ `private_rag_apps/graph/` とする（`api/` 配下には置かない）。これに伴い AGENTS.md §3 の依存方向ルールに「`graph` は `generation` と `retrieval` を独立に import してよい／`api` は `graph` 経由でこれらを呼ぶ／履歴のロード・永続化は `api`（FastAPI ハンドラ層）に残り `graph` には持ち込まない（§3.4 と整合）」を明文化する改訂を T3 で行う（M5 監査時の §3 改訂と同様の扱い）。

### 4.2 State 定義

```python
class GraphState(TypedDict):
    # input（ハンドラ層が組み立てる）
    conversation_id: str
    user_query: str
    history: list[Message]          # 既存 DB からロード済み。JSON 化可能な dict 形式

    # rewrite の出力
    search_query: str               # 書き換え後クエリ。書き換え不要なら user_query と同値
    rewrite_applied: bool

    # retrieve / grade の出力
    retrieved: list[ScoredChunk]    # rerank score 付き全件
    kept: list[ScoredChunk]         # THETA 通過分
    route: Literal["grounded", "direct"]

    # generate の出力
    citations: list[Citation]
```

`Message` / `ScoredChunk` / `Citation` はすべて plain dict（TypedDict）とし、Pydantic モデルを State に置かない（§3.4）。

### 4.3 ノード仕様

#### rewrite
- **入力:** `user_query`, `history`（直近 N ターン。既存の `settings.condense_history_turns` を踏襲）
- **処理:** 新規実装ではなく、既存の `generation.condense(query, history_messages)`（M2/M3 で実装済み。`generation/generator.py`）を再利用・拡張する。履歴を踏まえ、指示語・照応（「それ」「さっきの」）を解決した自己完結の検索クエリを生成する。履歴が空、または書き換え不要と判定した場合は `user_query` をそのまま通す
- **モデル:** 既存の `get_llm_client()`（M6。`settings.llm_provider` で OpenAI/Ollama を切替）をそのまま使う。**temperature=0** を明示指定する（eval 再現性のため。§3.5）。Anthropic 等の新規 LLM プロバイダは追加しない（rev.3）
- **出力:** `search_query`, `rewrite_applied`（`condense()` への追加実装。現状の `condense()` は書き換え有無を返さないため、この差分のみが新規実装）
- **フォールバック:** LLM 呼び出し失敗時は `search_query = user_query` で続行し、警告ログと Langfuse に記録（`condense()` の既存フォールバックを踏襲。rewrite は best-effort。ここで全体を落とさない）
- **レイテンシ目安:** p95 で +800ms 以内。超過が常態化する場合は N の削減または履歴の事前要約化を検討する（M7 内では計測と記録まで）

#### retrieve
- **処理:** 既存の retrieval サービス（hybrid search → RRF → rerank）を `search_query` で呼ぶ。**M7 では retrieval 内部に変更を加えない**。top_k は既存設定を踏襲
- **出力:** `retrieved`（rerank score 降順）

#### grade
- **前提（rev.3）:** `retrieved` の各 chunk が `rerank_score` を持つこと。**現状の `retrieval/searcher.py::_rerank()` は Voyage のスコアをチャンクに付与していない**（並べ替えのみ）ため、T4 で `_rerank()` の返り値に `rerank_score` フィールドを追加する最小限の変更を行う。ランキングロジック自体は変更しない。§8 リスク表の「retrieval 内部に変更を加えない」方針に対する唯一の例外として扱う
- **処理:** LLM を使わない純関数。
  - `kept = [c for c in retrieved if c.rerank_score >= THETA]`
  - `route = "direct" if len(kept) == 0 else "grounded"`
- **閾値:** THETA 1 本のみ（§2）。config 管理とし、**eval なしの変更を AGENTS.md の `make eval` 必須ルールの対象に含める**

#### generate
- **処理:** `route` に応じてプロンプトを切り替え、既存 SDK ラッパーで streaming 生成
  - **会話履歴は現行実装と同じ扱いとする: generate 自体には生の `history` を渡さない（rev.3 訂正）**。現行の `generate_answer_stream(query, context_chunks)` は `query` と `context_chunks` のみを受け取り、履歴は rewrite（`condense()`）側でのみ検索クエリに反映されている。rev.1/rev.2 は「generate も既存実装で履歴文脈を注入されている」という誤った前提に基づいていたため rev.3 で訂正した。M7 でもこの前提（generate は履歴を直接見ない）を維持し、スコープを広げない。generate が履歴を直接参照する必要が生じた場合は別途スペック化する
  - `grounded`: 既存の RAG プロンプトを基礎に、以下を追加する —「context で回答できる部分は引用付きで答える。質問に context でカバーできない部分が含まれる場合に限り、『---』区切りの『**一般知識に基づく補足**』セクションに分離して補う。補足セクション内では引用マーカーを使用しない。カバーできるか迷う内容は補足側に置く」。**質問カバレッジの判定はこのプロンプト指示に委ねる**（§2 grounded 参照）
  - `direct`: context 注入なし。システムプロンプトで「コーパス（プロジェクト設計ドキュメント）の内容には言及しない。ドキュメント固有の質問と思われる場合は、該当する記述が見つからなかった旨を伝える」ことを明示（direct 経路でのコーパス捏造の防止。§7.2 の groundedness eval で検証）
- **出力:** SSE 経由でトークンを stream（§5）、`citations`

---

## 5. ストリーミング統合（M2 差分）

### 5.1 方式

`graph.astream(stream_mode="custom")` + `get_stream_writer()` を使用する。generate ノード内で既存の Anthropic streaming を回し、トークンを writer に渡す。**`astream_events` は使わない**（LangChain のイベント形式に SSE ペイロードを引きずられないため）。

FastAPI ハンドラは writer 経由のイベントをそのまま既存 SSE フォーマットに変換する。M2 のペイロード構造は変更しない。

### 5.2 SSE イベント型の追加

既存イベント型（`token`, `citations`, `done`, `error`。rev.3: `citation`→`citations` に表記修正、実装に合わせた）は不変。以下を追加する。クライアント（assistant-ui 側）は未知イベント型を無視する実装であることを T3 で確認する（壊れる場合、default-ignore の小修正を T3 スコープに含める）。

| event | payload | 発火タイミング |
|---|---|---|
| `node_start` | `{"node": "rewrite" \| "retrieve" \| "grade" \| "generate"}` | 各ノード開始時 |
| `route_decided` | `{"route": "grounded" \| "direct", "kept": int, "dropped": int, "top_score": float \| null}` | grade 完了時 |
| `rewrite_result` | `{"applied": bool, "query": str}` | rewrite 完了時（UI でのデバッグ表示用） |

フロントエンドでの表示は M7 では最小実装（route バッジの 2 状態表示のみ）とし、リッチな進捗 UI は M5 の showcase 磨き込みの範疇とする。

### 5.3 互換性の検証方法

LLM 生成は非決定的であるため、「実 LLM でのペイロード diff 比較」は検証手段として成立しない。検証は 2 層に分ける。

1. **構造検証（自動・決定的）:** 生成を stub（固定文字列を返す mock クライアント）に差し替えた統合テストで、SSE イベント型の系列・各イベントの JSON スキーマ・順序を、現行実装のキャプチャと比較する
2. **実機検証（手動）:** 実 LLM で TTFT・トークン表示・citation・done の体感確認

---

## 6. 可観測性（Langfuse）

- 1 リクエスト = 1 trace は既存踏襲。グラフの各ノードを span として trace 配下にぶら下げる（LangGraph のコールバック連携ではなく、既存の Langfuse クライアントをノード関数内から直接使う）
- trace レベルの metadata に `route`, `rewrite_applied`, `theta`, `kept_count`, `top_score` を記録する。**閾値チューニングの分析はこの metadata を根拠に行う**
- direct 経路の trace には検索結果 span 以降の構造が異なる点に注意（ダッシュボードのフィルタ設計）

**実装（T7、rev.4 追記）:**

- `rewrite`（`condense()`）・`retrieve`（`retrieve_context()`）・`generate`（`generate_answer_stream()` / `generate_direct_answer_stream()`）の3ノードは、それぞれが呼び出す下位関数が既に `@observe(...)` デコレータ済みであり、`/api/chat` の `@observe()` ルートスパンの子として自動的にネストされるため、ノード自身への追加の span 計装は不要と判断した
- `grade` は LLM/IO 呼び出しを持たない純関数で、対応する `@observe()` 済みの下位関数が存在しないため、`grade` 自身が `get_client().start_as_current_observation(name="grade", as_type="span")` で明示的に span を作成する（`backend/src/private_rag_apps/graph/nodes/grade.py`）
- trace レベルの metadata は `get_client().update_current_trace()` ではなく `propagate_attributes(metadata={...})` で記録する。理由: インストール済みの langfuse SDK（4.14系、OTel ネイティブ実装）には `update_current_trace()` メソッドが存在しない（`docs/specs/26071422-m7_adaptive_routing/spec.md` 執筆時点のスペック記述は旧SDK系のAPIを前提にしていたため、実装時に判明した差分）。`route` / `theta` / `kept_count` / `top_score` は `grade` ノードで、`rewrite_applied` は `rewrite` ノードで、それぞれ値が確定した時点で記録する
- direct 経路でも `retrieve` / `grade` は必ず実行される（グラフの配線は `rewrite → retrieve → grade → (conditional) → generate` で固定。分岐は `grade` 後の conditional edge であり、両経路とも同じ `generate` ノードに合流するため、`retrieve`/`grade` の span・trace metadata が経路によって欠落することはない。`backend/src/private_rag_apps/graph/builder.py` の配線を参照）。この構造上の保証はコードレビューで確認済み。実 Langfuse UI 上での grounded/direct 両trace表示の手動確認は、この worktree の `backend/.env` に有効な `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` が設定されていない（プレースホルダーのまま）ため実施していない。AGENTS.md §4 の通り `LANGFUSE_*` は任意設定であり、未設定時の計装は no-op として動作することを単体テスト実行時のログ（Langfuse 送信失敗が例外化されず無害に記録されるのみで、テストは全通過）で確認済み

---

## 7. 評価

### 7.1 routing eval データセット（新規・実装より先に作成）

`eval/datasets/routing.jsonl`。各行:

```json
{"id": str, "query": str, "history": list, "expected_route": "grounded" | "direct",
 "category": "corpus" | "general" | "ambiguous" | "followup",
 "split": "calibration" | "holdout",
 "expected_search_query": str | null}   // followup のみ。rewrite 評価用
```

| カテゴリ | 件数 | expected_route | 例 |
|---|---|---|---|
| corpus | 40 | grounded | 「このプロジェクトで RRF を採用した理由は」 |
| general | 40 | direct | 「Python の walrus operator とは」 |
| ambiguous | 30 | 記述の実在で決定 | 「HNSW の efConstruction はどう決めるべきか」 |
| followup | 20 | 記述の実在で決定（大半 grounded、direct 期待も数件含める） | history: RRF の議論 → query: 「それの重み付けは？」 |

作成規約:

- **calibration / holdout 分割:** カテゴリ内で層化し 70 / 30 に分割する。**閾値決定（grid search）は calibration のみ、合格判定は holdout のみ**を使う。件数が rev.1 より増えているのは分割による統計力低下の補償
- corpus / followup は既存 eval セット（シードコーパス由来）から流用・改変してよい
- **ambiguous は手作業で厳選する**。expected_route は「コーパスに関連記述が実在するか」で機械的に決め、判断根拠（該当ドキュメントのパス or「記述なし」）を全件 README に記録する
- general / ambiguous(direct) は「コーパスに記述なし」を検索で確認する
- followup の history に含める assistant 応答は、**実アプリで生成した実物を記録して使う**（人工的な応答文は照応解決の難度を歪める）。作成手順は README に規定する
- 複合質問（一部 corpus・一部一般論。「補足」書式の検証用）を 5 件程度作成し、**routing.jsonl ではなく既存 e2e eval セットに追加する**（補足は経路でなく生成品質の問題のため。§7.2 参照）

### 7.2 指標と合格基準

grid search（calibration 上）は「grounded 見逃し率 ≤ 0.05 を制約に direct 適中を最大化」で行う。**合格判定は holdout 上で、小標本のため率でなく件数で定義する**:

| 指標 | 定義（holdout 上） | 基準 |
|---|---|---|
| grounded 見逃し | expected=grounded が direct になった件数 | **≤ 1 件（必達）** |
| direct 誤り | expected=direct が grounded になった件数 | ≤ 3 件 |
| rewrite quality | followup で retrieval hit@k が rewrite なし比で低下しないこと | 非劣化 |
| direct groundedness | direct 経路の回答におけるコーパス固有の固有名詞・数値の捏造（LLM-as-judge） | **judge が違反と判定した件は人手裁定し、真の違反 0**（judge の偽陽性のみでブロックしない） |
| 補足書式の遵守 | e2e eval の複合質問 5 件で、context 外の内容が補足セクションに分離されていること（LLM-as-judge + 人手確認） | 5/5 |

**注（T7 追記、rev.4。rev.5 で direct 誤りの行を更新）: 本表は THETA・grade ロジックの将来的なチューニング目標として残す。** 実績は以下の通り（詳細: `docs/specs/26071422-m7_adaptive_routing/tasklist.md` T4/T7、`backend/evals/reports/m7-score-distribution.md`、`backend/evals/reports/m7-recalibration-result.md`）:

| 指標 | 実績（holdout） | 基準を満たすか |
|---|---|---|
| grounded 見逃し | 0/21 | 達成（≤ 1 件） |
| direct 誤り | **2/18**（`a019`=0.707, `a028`=0.574） | **達成**（基準 ≤ 3 件。T4 時点では 4/18 で NO-GO だったが、2026-07-17 に該当4件を含む holdout 未検証 None 件を Voyage API に再取得した結果、`g014`/`g037` は Voyage rerank 一時失敗による誤判定と判明し訂正。`a019`/`a028` は実スコア取得の上で THETA 付近の正当なグレーゾーン誤判定と判明し残存） |
| rewrite quality | 非劣化（rewrite 有り 18/20 正解 vs rewrite 無し 14/20 正解） | 達成 |
| direct groundedness | 人手裁定後の真の違反 0 | 達成 |
| 補足書式の遵守 | 3/5 | **未達**（プロンプト調整を1回試行したが悪化のため撤回。既知の制約として記録。構造化出力化を M7 追補スペックとして提案） |

holdout 上で grounded 見逃し・direct 誤りとも基準を達成したため、**§7.4 の LLM grader 昇格 exit criteria（「holdout 上で基準を同時に満たせないことが示された場合」）はもはや満たされていない**（ADR 0006）。ADR 0005 が承認した「グレーゾーン限定 LLM grader を別スペックとして起票する」方針は、必須の対応ではなく**任意の将来改善検討**に位置づけを変更する。ただし `a019`(0.707)/`a028`(0.574) という具体的なグレーゾーン誤判定事例が実在することは変わらないため、検討の価値自体は残る。

### 7.3 Makefile ターゲット

```
make eval               # 既存 e2e eval（retrieval + generation）+ 複合質問の補足書式検証
make eval-routing       # 7.1 のデータセットで rewrite → retrieve → grade を評価（generate は実行しない: 高速）
make eval-all           # 両方
```

- `eval-routing` は `--cached-rewrite` オプションを持つ: rewrite 結果を jsonl にキャッシュし、閾値チューニング時は retrieval 以降のみ再実行する（再現性・速度・コスト。§3.5）
- AGENTS.md の eval 必須ルールに以下を追記する: **THETA、rewrite プロンプト、grade ロジックの変更時は `make eval-routing` を必須とする。grounded / direct プロンプトの変更時は `make eval` を必須とする**

### 7.4 LLM grader への昇格判断（exit criteria）

閾値方式で §7.2 の grounded 見逃し・direct 誤りの基準を holdout 上で**同時に満たせない**ことが示された場合に限り、グレーゾーン（THETA 近傍のスコア帯）のみを対象とした LLM grader の追加を別スペックとして起票する。eval による証明なしに LLM grader を先行実装しない。

---

## 8. 依存関係とリスク

| リスク | 影響 | 緩和策 |
|---|---|---|
| M3 / M4 が実装未完で M7 の前提が欠ける | 着手不能 | T0 で前提確認を最初に行う |
| rerank score の分布が想定より平坦で閾値が引けない | grade 精度が出ない | routing eval を実装前に作成し、スコア分布を先に可視化（T1–T2）。NO-GO なら §7.4 へ |
| custom stream と既存 SSE の接合で順序・バッファリング問題 | M2 のストリーミング体験の劣化 | T3 で pass-through グラフ + stub 構造検証を先行（§5.3） |
| assistant-ui が未知 SSE イベントで壊れる | フロント全体 | T3 に互換性テストを含める |
| 「補足」書式をプロンプト指示だけで守らせられない | grounded 回答で出典境界が滲む | e2e eval の複合質問 5 件で検証（§7.2）。守れない場合は補足セクションの構造化出力化を M7 追補で検討 |
| LangGraph のバージョン追従コスト | 保守 | 依存を `langgraph` 単体に限定。prebuilt / langchain 系 API を使わない |

---

## 9. タスク分割（概要）

詳細は `docs/specs/26071422-m7_adaptive_routing/tasklist.md`。実装順序: **T0 → T1 → T2 →（GO/NO-GO）→ T3 → T4 → T5 → T6 → T7**

- T0: 前提確認（M2/M3/M4 の実装完了状況）
- T1: routing eval データセット作成（calibration / holdout 分割込み）
- T2: スコア分布分析と THETA 初期値決定（GO/NO-GO 判定）
- T3: LangGraph 最小導入（pass-through + stub 構造検証）
- T4: grade ノードと 2 経路分岐 + 補足書式プロンプト
- T5: rewrite ノード
- T6: SSE 追加イベント + フロント最小表示
- T7: 可観測性の仕上げとドキュメント確定

---

## 10. 未決事項（T7 時点ですべて解消・決定済み。rev.4）

- **rewrite に渡す履歴ターン数 N の最終値:** 「初期値 6」としていた本文の記述は誤りで、実際は既存 `settings.condense_history_turns` のデフォルト値である **5** を M7 全体を通じて変更せず採用した（T5 スコープ外決定。`backend/src/private_rag_apps/core/config.py`）。T5 でレイテンシを実測したところ p50=1004ms、p95=1308ms（ローカル Ollama qwen3.5:9b、実 LLM 呼び出し20件）であり、目安の p95 ≤ 800ms を超過している。**M7 ではこれに対応せず、N 削減の要否を含めて M8 以降の検討事項として持ち越す**
- **「一般知識に基づく補足」セクションの文言・書式の最終形:** T4 で `backend/src/private_rag_apps/prompts/routing.py` に実装済み。見出しは `SUPPLEMENT_HEADING = "## 一般知識に基づく補足"` で、本スペックの初期表記をそのまま最終形として採用している。補足セクション内で出典番号 `[n]` を使わせない指示について、より強い文言への調整を1回試みたが悪化した（3/5→1/5、ローカル小型モデルでの測定）ため撤回し、調整前の文言に確定した。書式遵守は複合質問5件中3件に留まる既知の制約であり、恒久対応（補足の構造化出力化）は M7 追補スペックとして別途起票する方針（`prompts/routing.py` のモジュール docstring、`.superpowers/sdd/task-T4-report.md` 参照）
- **LLM-as-judge（direct groundedness / 補足書式）に使うモデルとプロンプト:** モデルは `settings.judge_model`（環境変数 `JUDGE_MODEL`。OpenAI固定、`backend/src/private_rag_apps/evals/judge.py::_call_judge()`）。プロンプトは `backend/src/private_rag_apps/prompts/judge.py` の `JUDGE_DIRECT_GROUNDEDNESS_PROMPT` / `JUDGE_SUPPLEMENT_FORMAT_PROMPT`。judge が違反と判定した件は人手裁定し、真の違反のみをカウントする運用（judge の偽陽性のみでブロックしない）を最終形として確定した