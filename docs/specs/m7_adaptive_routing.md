# M7: Adaptive Routing — LangGraph によるクエリ書き換えと回答経路制御

- Status: Draft (rev.3 — ローカル LLM (Ollama / OpenAI 互換) 前提へ改訂)
- Depends on: M2 (SSE streaming), M3 (conversation history), M4 (evaluation), 既存 retrieval pipeline
  - **注: 実装完了状況および reranker の有無はタスク T0 で着手前に確認する（§9 リスク R-A）**
- Blocked by: なし
- Blocks: M8 候補 (clarify / HITL)

## 改訂履歴

- **rev.3: 推論基盤をローカル LLM（Ollama / OpenAI 互換エンドポイント / 単一モデル）前提に改訂。** 主な設計変更:
  - 「一般知識に基づく補足」を **固定デリミタ + SSE パース方式**に変更（構造化出力はストリーミングと両立しないため）
  - **LLM-as-judge を原則廃止**し、決定的チェック（禁止語彙リスト / デリミタパース）+ 少数件の人手裁定に置換（ローカル judge の品質上限を eval に持ち込まないため）
  - rewrite の structured output を Ollama の JSON schema (`format`) に変更（tool calling は使用しない）
  - 単一モデル・直列 GPU 前提のレイテンシ設計、履歴が空の場合の rewrite スキップを追加
- rev.2: THETA_HIGH を廃止し grade を 2 値化。用語定義を新設。calibration / holdout 分割を導入
- rev.1: 初版

---

## 1. 背景と目的

現状、すべての質問が無条件に retrieval パイプラインを通る。これには 2 つの問題がある。

1. 一般知識の質問（例: Python の文法、統計の一般論）に対しても検索・context 注入が走る。**ローカル LLM 環境では GPU が直列資源であるため、無駄な context 注入は prefill 時間に直結する**
2. コーパスに無関係な chunk が context に混入し、回答品質を下げる、あるいはコーパス由来であるかのような誤帰属を生む

M7 では LangGraph を導入し、**クエリ書き換え → 検索 → 関連度判定（grade）→ 経路分岐** という適応的なフローを実装する。

### スコープ

**In:**
- LangGraph によるグラフオーケストレーション基盤の導入
- 会話履歴を考慮したクエリ書き換え（rewrite ノード）
- 常時検索 + 関連度スコア閾値（THETA）による grade
- 2 経路の回答生成: `grounded` / `direct`。grounded 内での「一般知識に基づく補足」のデリミタ方式（§2）
- SSE プロトコルへのグラフ実行イベントの追加（後方互換）
- routing eval データセット（calibration / holdout 分割）と決定的な eval チェックの新設

**Out（明示的に非スコープ）:**
- clarify / 聞き返し（HITL）→ M8 候補
- LangGraph checkpointer の導入 → M8 で HITL と同時に検討
- LLM grader → 閾値方式の eval 結果を見て判断（§8.5）
- マルチターンのエージェント的探索（再検索ループ）
- LangChain の LLM 抽象（`ChatOpenAI` 等）の採用 → 恒久的に不採用（§4.3）
- rewrite / generate へのモデル分割（単一モデル運用を前提とする。§5.1）

---

## 2. 用語定義

本スペックおよび M7 関連文書で使う用語を定義する。実装・eval・UI 表示はこの定義に従う。

### chunk / 関連度スコア

**chunk** はコーパス（プロジェクト設計ドキュメント群）を分割した検索単位。retrieval パイプラインは hybrid search（pgvector + pg_bigm → RRF）で候補 chunk を集め、**関連度スコア**を付与して降順に返す。M7 はこの既存パイプラインの出力を入力として扱い、内部には変更を加えない。

> **前提条件（T0 で確認）:** grade は「クエリ間で比較可能な絶対的スコア」を必要とする。reranker（cross-encoder）のスコアはこれを満たすが、**RRF スコアは順位の逆数和であり絶対値に意味がなく、クエリをまたいだ閾値を引けない**。reranker が存在しない構成の場合、grade の設計自体を見直す（§9 R-A）。

### THETA（θ）

chunk を回答の根拠として**採用するかどうかの足切り閾値**。関連度スコアが THETA 以上の chunk のみを残し（= `kept`）、未満は捨てる。M7 の grade で使う閾値は**この 1 本のみ**。

- 値はハードコードせず config 管理とする
- 初期値は routing eval データセット（calibration split）上でのキャリブレーションにより決定する（§8）
- スコア分布はモデル・コーパス依存であり、**絶対値に意味を求めない**。eval セットに対するキャリブレーション結果のみを根拠とする
- 誤判定コストの非対称性（§3.1）により、キャリブレーションは「grounded の見逃し最小化」を優先する（迷ったら低めに引く）

### grounded（経路）

**足切りを生き残った chunk が 1 件以上ある**場合の回答経路。kept chunks を context として注入し、引用付きで回答を生成する。

質問の一部が context でカバーできない場合、その部分は回答内の「一般知識に基づく補足」（後述）で補う。**カバレッジの判定（context で答えられる範囲の見極め）は grade ではなく generate 時の LLM が行う**。数値閾値では質問へのカバレッジを測定できないため。

### direct（経路）

**足切りの結果、kept が 0 件**（= コーパスに関連する記述が見つからなかった）場合の回答経路。context 注入なしで LLM の一般知識のみから回答する。

direct 経路では**コーパスの内容に言及してはならない**（記憶からの捏造防止）。ドキュメント固有の質問と思われる場合は「ドキュメントに該当する記述が見つからなかった」旨を回答内で伝える。この遵守は §8.3 の**禁止語彙チェック（決定的）**で検証する。

### 一般知識に基づく補足（デリミタ方式。経路ではない）

grounded 回答内で、context に根拠のない内容を補う場合の**出力規約**。

- モデルは補足の開始位置に**単独行のデリミタ `---SUPPLEMENT---`** を出力する
- デリミタ以降が補足セクション。補足内では引用マーカーを使用しない
- **バックエンドは streaming 中にこのデリミタを検出し、SSE の `supplement_start` イベントを発火する**。デリミタ行自体はクライアントに送出しない。フロントはこのイベント以降を視覚的に区切って表示する

**構造化出力（JSON schema）を採らない理由:** grounded 回答はストリーミングされる。JSON schema で出力全体を縛ると生の JSON がトークンとして流れ、ストリーミング UX が壊れる。デリミタ 1 行のみの制約であれば、小型モデルでも遵守しやすく、ストリーミングを維持したまま**遵守を機械的に検証できる**（§8.3）。

これは grade が決める経路ではなく、**grounded プロンプトが generate に課す出力規約**である。

### 経路判定の全体像

```
retrieval が返した chunks の関連度スコア
        │
        ▼
  score >= THETA の chunk を kept に残す
        │
   ┌────┴─────┐
 kept == 0   kept >= 1
   │            │
 direct      grounded
（一般知識のみ） （引用付き。カバー外は ---SUPPLEMENT--- 以降で補う）
```

---

## 3. 設計原則

### 3.1 誤判定コストの非対称性（最重要）

- **grounded にすべき質問を direct に流す誤り = 致命的**。コーパスに関するハルシネーションが発生し、プライベート RAG の存在意義が毀損される
- **direct でよい質問を grounded に流す誤り = 軽微**。無関係 chunk の混入と prefill 時間の浪費に留まる

したがって、**迷ったら grounded 側に倒す**。THETA・プロンプト・eval 指標のすべてがこの原則から導出される。grade の主要指標は accuracy ではなく **「grounded にすべき質問の見逃し数」** とする。

### 3.2 Single Source of Truth

- 会話履歴の正は既存の conversation テーブル（M3）。LangGraph は状態を永続化しない（checkpointer 不使用）
- グラフは 1 リクエスト = 1 実行のステートレスな関数として扱う

### 3.3 LangGraph は薄く使う（ローカル LLM 前提での根拠）

- LangGraph の責務は **状態機械・分岐・イベント伝搬のみ**
- LLM 呼び出しは既存の OpenAI 互換クライアント（`openai` SDK の `base_url` 差し替え、または現行の実装）を維持する
- **`langgraph` のみ依存に追加し、`langchain` / `langchain-openai` は追加しない**

理由:

1. **ローカル LLM はサーバー実装とモデルの癖が本体**（stop token の扱い、`format` / `response_format` の対応差、指示追従性のばらつき）。LangChain の抽象はこの癖を隠す方向に働き、障害時に「LangChain / Ollama / モデル」のどこが原因かの切り分けコストが増える
2. **OpenAI 互換 SDK 自体がすでに十分薄い抽象**であり、その上にもう一枚被せる利得が小さい
3. Ollama の prefix cache を効かせるにはプロンプトの前方一致（system → history → context → query の順序固定）を自分で制御する必要がある

### 3.4 将来の checkpointer 導入に備えた制約

- State は JSON シリアライズ可能な `TypedDict` に限定する。DB コネクション、HTTP クライアント、Langfuse オブジェクト等を State に入れない
- 履歴のロード / 永続化はグラフの**外**（FastAPI ハンドラ層）に置き、グラフ本体を純粋に保つ

### 3.5 eval は決定的であること（ローカル LLM 前提での再定義）

- **eval の合否判定に LLM-as-judge を使わない。** judge をローカルモデルで回すと判定品質がモデル上限に縛られ、eval の合否が揺れる。判定はすべて (a) 決定的チェック（文字列マッチ / パース）または (b) 少数件の人手裁定 に置き換える（§8.3）
- routing 判定に関与する LLM 呼び出し（rewrite）は `temperature=0` + `seed` 固定とし、加えて実行結果をキャッシュして再現可能にする（§8.4）

---

## 4. アーキテクチャ

### 4.1 グラフ構造

```
                    ┌─────────┐
 (history + query)  │ rewrite │  履歴が空ならスキップ（§5.1）
        ──────────▶ └────┬────┘
                         │
                    ┌────▼─────┐
                    │ retrieve │  既存 hybrid search（常時実行）
                    └────┬─────┘
                         │
                    ┌────▼────┐
                    │  grade  │  THETA で chunk を filter（純関数・LLM 不使用）
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

generate は 1 ノードとし、`state["route"]` によってプロンプトテンプレートを切り替える。

### 4.2 State 定義

```python
class GraphState(TypedDict):
    # input（ハンドラ層が組み立てる）
    conversation_id: str
    user_query: str
    history: list[Message]          # 既存 DB からロード済み。JSON 化可能な dict 形式

    # rewrite の出力
    search_query: str               # 書き換え後クエリ。スキップ/失敗時は user_query と同値
    rewrite_applied: bool

    # retrieve / grade の出力
    retrieved: list[ScoredChunk]    # 関連度スコア付き全件
    kept: list[ScoredChunk]         # THETA 通過分
    route: Literal["grounded", "direct"]

    # generate の出力
    citations: list[Citation]
    supplement_emitted: bool        # デリミタが検出されたか（可観測性・eval 用）
```

`Message` / `ScoredChunk` / `Citation` はすべて plain dict（TypedDict）とし、Pydantic モデルを State に置かない（§3.4）。

### 4.3 LLM クライアント方針

- 既存の OpenAI 互換クライアントを使用する。**LangChain の LLM ラッパーは使わない**（§3.3）
- **structured output が必要な箇所（rewrite のみ）は Ollama の JSON schema 指定を使う。** OpenAI 互換エンドポイント経由での `response_format: {"type": "json_schema"}` の動作可否は T0 で検証し、不可の場合は Ollama ネイティブ API (`/api/chat` の `format` フィールド) を rewrite に限って使う
- **tool calling / function calling は使用しない**（Ollama の tool call はモデル依存で不安定なため）
- プロンプトは prefix cache が効くよう前方一致を保つ順序（system → history → context → query）で構築する

---

## 5. ノード仕様

### 5.1 rewrite

- **目的:** 履歴を踏まえ、指示語・照応（「それ」「さっきの」）を解決した自己完結の検索クエリを生成する
- **スキップ条件:** `history` が空（初回質問）の場合、**LLM を呼ばずに** `search_query = user_query`, `rewrite_applied = False` として即座に通過する。単一モデル・直列 GPU 構成では rewrite の prefill が generate と直列に乗るため、確実に不要なケースを機械的に除外する
  - **語彙ヒューリスティック（「それ」を含むか等）によるスキップは行わない。** 照応語を含まないフォローアップ（例:「重み付けは？」）を見逃すため
- **モデル:** generate と**同一の単一モデル**（§スコープ Out）。モデルの切り替えは行わない（Ollama のモデルスワップは VRAM 再ロードを伴い、レイテンシが大きく劣化する）
- **推論設定:** `temperature=0`、`seed` 固定（§3.5）
- **出力形式:** JSON schema による structured output（§4.3）。スキーマ: `{"search_query": str, "rewrite_applied": bool}`
- **履歴ターン数:** 直近 N ターン（初期値 N=4、config 化）。**rev.2 の N=6 から削減**。単一モデルでは rewrite の prefill コストが直接ユーザー体感レイテンシに乗るため
- **フォールバック:** LLM 呼び出し失敗・JSON パース失敗時は `search_query = user_query` で続行し、警告ログと Langfuse に記録（rewrite は best-effort。ここで全体を落とさない）
- **レイテンシ:** ローカル環境では TPS がハードウェア依存のため絶対値の基準を置かない。**T0 で測定する generate の TTFT ベースラインに対する相対値で評価**し、rewrite の追加分が TTFT ベースラインを超える場合は N の削減を検討する（M7 内では計測と記録まで）

### 5.2 retrieve

- **処理:** 既存の retrieval サービスを `search_query` で呼ぶ。**M7 では retrieval 内部に変更を加えない**。top_k は既存設定を踏襲
- **出力:** `retrieved`（関連度スコア降順）

### 5.3 grade

- **処理:** LLM を使わない純関数
  - `kept = [c for c in retrieved if c.score >= THETA]`
  - `route = "direct" if len(kept) == 0 else "grounded"`
- **閾値:** THETA 1 本のみ（§2）。config 管理とし、変更時の eval を必須とする（§8.4）

### 5.4 generate

- **共通:** `route` に応じてプロンプトを切り替え、既存 OpenAI 互換クライアントで streaming 生成する
  - **会話履歴の注入は既存実装（M3）を踏襲し、両経路で同一とする**（rewrite は検索クエリの照応を解決するが、回答生成自体も履歴文脈を必要とするため。グラフ移設時にこれを落とさないこと）
- **`grounded`:** 既存の RAG プロンプトを基礎に、以下を追加する
  - 「context で回答できる部分は引用付きで答える」
  - 「質問に context でカバーできない部分が含まれる場合に限り、単独行 `---SUPPLEMENT---` を出力し、それ以降で一般知識に基づいて補う。補足部分では引用マーカーを使用しない」
  - 「カバーできるか迷う内容は `---SUPPLEMENT---` 以降に置く」
  - **カバレッジの判定はこのプロンプト指示に委ねる**（§2 grounded 参照）
- **`direct`:** context 注入なし。「コーパス（プロジェクト設計ドキュメント）の内容には言及しない。ドキュメント固有の質問と思われる場合は、該当する記述が見つからなかった旨を伝える」ことを明示
- **デリミタ処理（バックエンド）:**
  - streaming 中に `---SUPPLEMENT---` の行を検出したら、`supplement_start` SSE イベントを発火し、`supplement_emitted = True` を記録する
  - **デリミタ行そのものはクライアントに送出しない**（トークンバッファでの行境界処理が必要。改行を跨いでデリミタが分割されるケースを考慮すること）
  - デリミタが 2 回以上出現した場合、2 回目以降は通常テキストとして扱いログに警告を残す（プロンプト遵守の逸脱検知）
- **出力:** SSE 経由のトークンストリーム（§6）、`citations`、`supplement_emitted`

---

## 6. ストリーミング統合（M2 差分）

### 6.1 方式

`graph.astream(stream_mode="custom")` + `get_stream_writer()` を使用する。generate ノード内で既存の streaming を回し、トークンを writer に渡す。**`astream_events` は使わない**（LangChain のイベント形式に SSE ペイロードを引きずられないため）。

FastAPI ハンドラは writer 経由のイベントを既存 SSE フォーマットに変換する。M2 のペイロード構造は変更しない。

### 6.2 SSE イベント型の追加

既存イベント型（`token`, `citation`, `done`, `error`）は不変。

| event | payload | 発火タイミング |
|---|---|---|
| `node_start` | `{"node": "rewrite" \| "retrieve" \| "grade" \| "generate"}` | 各ノード開始時 |
| `route_decided` | `{"route": "grounded" \| "direct", "kept": int, "dropped": int, "top_score": float \| null}` | grade 完了時 |
| `rewrite_result` | `{"applied": bool, "query": str}` | rewrite 完了時（スキップ時も `applied: false` で送出） |
| `supplement_start` | `{}` | grounded 生成中にデリミタを検出した時 |

クライアント（assistant-ui）が未知イベント型を無視する実装であることを T3 で確認する（壊れる場合、default-ignore の小修正を T3 スコープに含める）。

### 6.3 互換性の検証方法

LLM 生成は非決定的であるため、実 LLM でのペイロード diff 比較は検証手段として成立しない。検証は 2 層に分ける。

1. **構造検証（自動・決定的）:** 生成を stub（固定文字列を返す mock クライアント）に差し替えた統合テストで、SSE イベント型の系列・JSON スキーマ・順序を現行実装のキャプチャと比較する。**デリミタ処理のテストは stub で行う**（デリミタを含む固定文字列、行を跨いで分割されたデリミタ、デリミタ 2 回出現の各ケース）
2. **実機検証（手動）:** 実モデルで TTFT・トークン表示・citation・done・補足区切りの体感確認

---

## 7. 可観測性（Langfuse）

- 1 リクエスト = 1 trace は既存踏襲。各ノードを span として trace 配下にぶら下げる（既存の Langfuse クライアントをノード関数内から直接使う）
- trace metadata に `route`, `rewrite_applied`, `rewrite_skipped`, `theta`, `kept_count`, `top_score`, `supplement_emitted` を記録する
- **ローカル LLM 環境ではレイテンシ分析が特に重要**。rewrite / retrieve / generate の各 span の所要時間と、generate の TTFT を記録し、rewrite 導入によるユーザー体感への影響を追跡できるようにする

---

## 8. 評価

### 8.1 routing eval データセット

`eval/datasets/routing.jsonl`。各行:

```json
{"id": str, "query": str, "history": list, "expected_route": "grounded" | "direct",
 "category": "corpus" | "general" | "ambiguous" | "followup",
 "split": "calibration" | "holdout",
 "expected_search_query": str | null}
```

| カテゴリ | 件数 | expected_route | 例 |
|---|---|---|---|
| corpus | 40 | grounded | 「このプロジェクトで RRF を採用した理由は」 |
| general | 40 | direct | 「Python の walrus operator とは」 |
| ambiguous | 30 | 記述の実在で決定 | 「HNSW の efConstruction はどう決めるべきか」 |
| followup | 20 | 記述の実在で決定（direct 期待も 3–5 件含める） | history: RRF の議論 → query: 「それの重み付けは？」 |

作成規約:

- **calibration / holdout 分割:** カテゴリ内層化で 70 / 30。**閾値決定（grid search）は calibration のみ、合格判定は holdout のみ**を使う
- **ambiguous は手作業で厳選する**。expected_route は「コーパスに関連記述が実在するか」で機械的に決め、判断根拠（該当ドキュメントのパス or「記述なし」）を全件 README に記録する
- followup の history に含める assistant 応答は、**実アプリで生成した実物を記録して使う**
- 複合質問（一部 corpus・一部一般論。補足デリミタの検証用）を 5 件作成し、**routing.jsonl ではなく既存 e2e eval セットに追加する**

### 8.2 コーパス固有語彙リスト（新規・決定的チェック用）

`eval/datasets/corpus_vocabulary.txt`。コーパスにのみ現れる固有語彙を人手でキュレーションする。

- プロジェクト固有の名称・ドキュメント名・独自用語・固有の設定値（例: `muzia`, `AGENTS.md`, プロジェクト独自のモジュール名、コーパスに書かれた具体的なパラメータ値）
- **一般語と衝突する語（`RRF`, `HNSW`, `pgvector` のような一般技術用語）は含めない**。これらは direct 経路で言及されても捏造ではない
- 30–50 語程度。false positive を避けるため、**「コーパスを読んでいなければ書けない語」のみ**に絞る

### 8.3 指標と合格基準（すべて決定的チェックまたは人手裁定。LLM-as-judge を使わない）

grid search（calibration 上）は「grounded 見逃し率 ≤ 0.05 を制約に direct 適中を最大化」。**合格判定は holdout 上、小標本のため件数で定義する**:

| 指標 | 判定方法 | 基準 |
|---|---|---|
| grounded 見逃し | 決定的（route の比較） | **≤ 1 件（必達）** |
| direct 誤り | 決定的（route の比較） | ≤ 3 件 |
| rewrite quality | 決定的（retrieval hit@k の比較） | followup で rewrite なし比 非劣化 |
| **direct 捏造** | **決定的**: direct 経路の回答本文に `corpus_vocabulary.txt` の語が出現するか文字列マッチ | **検出 0 件**（検出時は人手で真偽を確認し、一般語の混入なら語彙リストを修正して再実行） |
| **補足デリミタの遵守** | **決定的**: 複合質問 5 件で `supplement_emitted == True` かつデリミタが 1 回のみ | 5/5 |
| 補足内容の妥当性 | **人手裁定**（5 件のみ） | context 外の内容が補足側に分離されている: 5/5 |

### 8.4 Makefile ターゲット

```
make eval               # 既存 e2e eval + 複合質問 5 件の補足デリミタ検証
make eval-routing       # 8.1 のデータセットで rewrite → retrieve → grade を評価（generate は実行しない）
make eval-direct        # direct 経路を実際に生成し、corpus_vocabulary による捏造チェック（決定的）
make eval-all           # 上記すべて
```

- `eval-routing` は `--cached-rewrite` オプションを持つ: rewrite 結果を jsonl にキャッシュし、閾値チューニング時は retrieval 以降のみ再実行する（**ローカル GPU での eval 実行コストが高いため、これは必須機能**）
- AGENTS.md への追記: **THETA・rewrite プロンプト・grade ロジックの変更時は `make eval-routing` を必須とする。grounded / direct プロンプトの変更時は `make eval` と `make eval-direct` を必須とする**

### 8.5 LLM grader への昇格判断（exit criteria）

閾値方式で §8.3 の基準を holdout 上で**同時に満たせない**ことが示された場合に限り、グレーゾーン（THETA 近傍）のみを対象とした LLM grader の追加を別スペックとして起票する。**ただしローカル単一モデル構成では grader も同一モデルとなり、grade のたびに追加の prefill が発生する**ため、レイテンシへの影響を含めて再評価すること。eval による証明なしに先行実装しない。

---

## 9. リスク

| ID | リスク | 影響 | 緩和策 |
|---|---|---|---|
| **R-A** | **reranker が存在せず RRF スコアしかない** | **RRF スコアは絶対値に意味がなく、クエリ横断の閾値が原理的に引けない → grade 設計が破綻** | **T0 の最優先確認項目。不在の場合は M7 を保留し、(a) ローカル cross-encoder reranker の導入を別マイルストーンとして起票 (b) grade を LLM grader 方式に変更（§8.5）のいずれかを選択** |
| R-B | 小型ローカルモデルが補足デリミタを守らない | grounded 回答の出典境界が滲む | デリミタ 1 行のみという最小の制約に留めた。§8.3 で決定的に検証。守れない場合は T4 の実装ノートの脱出条件に従う |
| R-C | Ollama の OpenAI 互換エンドポイントが `json_schema` 未対応 | rewrite の structured output が不安定 | T0 で検証。不可ならネイティブ API (`format`) を rewrite に限定使用（§4.3） |
| R-D | rewrite の追加により体感レイテンシが劣化 | UX 劣化 | 履歴が空ならスキップ（§5.1）、N=4 に削減。T0 で TTFT ベースラインを測定し相対評価 |
| R-E | custom stream と既存 SSE の接合、デリミタのバッファ処理 | ストリーミング破損 | T3 で stub による決定的な構造検証（§6.3） |
| R-F | スコア分布が平坦で閾値が引けない | grade 精度が出ない | T1–T2 で実装前に検証。NO-GO なら §8.5 へ |
| R-G | LangGraph のバージョン追従コスト | 保守 | 依存を `langgraph` 単体に限定 |

---

## 10. タスク分割（概要）

詳細は `M7-tasks.md`。実行順序: **T0 →（R-A 判定）→ T1 → T2 →（GO/NO-GO）→ T3 → T4 → T5 → T6 → T7**

- T0: 前提確認（**reranker の有無**、Ollama の json_schema 対応、TTFT ベースライン測定、M2/M3/M4 の実装状況）
- T1: routing eval データセット + コーパス固有語彙リストの作成
- T2: スコア分布分析と THETA 初期値決定（GO/NO-GO 判定）
- T3: LangGraph 最小導入（pass-through + stub 構造検証）
- T4: grade ノードと 2 経路分岐 + 補足デリミタ処理
- T5: rewrite ノード
- T6: SSE 追加イベント + フロント最小表示
- T7: 可観測性の仕上げとドキュメント確定

---

## 11. 未決事項（実装中に決定してよいもの）

- rewrite に渡す履歴ターン数 N の最終値（初期値 4、Langfuse でレイテンシを観測して調整）
- デリミタ文字列の最終形（初期値 `---SUPPLEMENT---`。モデルが出力しづらい場合は変更可。**変更時は §8.3 の検証を再実行すること**）
- `corpus_vocabulary.txt` の語彙数と収録基準の最終調整（false positive が出た場合の除外運用）