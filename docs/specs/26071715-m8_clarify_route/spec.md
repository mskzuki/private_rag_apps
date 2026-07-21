# M8: Clarify Route — グレーゾーン質問への聞き返し

- Status: Draft
- Depends on: M7（`docs/specs/26071422-m7_adaptive_routing/spec.md`。grade / generate ノード・THETA・SSE `route_decided` イベント・routing eval 基盤をそのまま拡張する）
- Blocked by: `evals/routing.py::retrieve_and_grade()` が `grade()` を LangGraph 実行コンテキスト外から直接呼んでおり、T6 で `grade()` 冒頭に追加された `get_stream_writer()` が `RuntimeError` を送出する未修正バグ（§8 参照）。THETA_HIGH キャリブレーション・3値化 eval のいずれもこの経路を使うため、実装着手前に修正が必須
- Blocks: なし

> 本機能は M7 スペックで「M8 候補」として明記されていた項目（clarify / HITL、LangGraph checkpointer 導入、rewrite N 削減、LLM grader）のうち、**clarify（聞き返し）のみ**をスコープインするものである。他の3項目は本スペックの非スコープとし、必要になった時点で別途起票する。

---

## 改訂履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-17 | 初版。ユーザーとの壁打ちでスコープ（clarifyのみ）・HITL方式（checkpointerなし・新規ターン方式）・clarifyトリガー（gradeのグレーゾーンのみ）・閾値方式（THETA/THETA_HIGH 2閾値制）・応答生成方式（LLM生成）を確定 |

---

## 1. 背景と目的

M7 は grade ノードで rerank score が単一閾値 THETA 以上のチャンクが1件でもあれば `grounded`、無ければ `direct` に倒す2値判定を実装した（`docs/specs/26071422-m7_adaptive_routing/spec.md` rev.2 で THETA_HIGH を一度廃止し2値化した経緯がある）。この方式は「誤判定コストの非対称性（迷ったら grounded に倒す）」という設計原則の下では安全側だが、以下の問題が残る。

- rerank score が THETA をわずかに上回るだけの弱い関連チャンクを context として、コーパスの記述であるかのように断定的な回答を生成してしまうケースがある
- 逆に、質問がコーパスのどのトピックについてのものか本来は特定できるはずなのに、rewrite 後のクエリが曖昧で検索結果が分散し、grounded/direct のどちらに倒しても外れるケースがある

これらは「回答を確定させる前にユーザーに一言確認する」ことで解決できる。本 M8 では、grade が rerank score のグレーゾーンを検出した場合に、検索結果を確定コンテキストとして使わず、ユーザーに聞き返す第3の経路 `clarify` を追加する。

### スコープ

**In:**
- `grade` ノードの2閾値化（THETA / THETA_HIGH）による `grounded` / `clarify` / `direct` の3経路判定
- `clarify` 経路のLLMによる聞き返し文生成（grade の `kept` チャンクを弱い候補として参照）
- SSE `route_decided` イベントへの `clarify` 値の追加（新規イベント型は追加しない）
- フロントエンドの route バッジを3状態に拡張
- routing eval データセットの3値化（`expected_route` に `clarify` を追加）と THETA_HIGH のキャリブレーション
- `evals/routing.py` の `get_stream_writer()` 起因の既知バグ修正（§8。本実装の前提作業）

**Out（明示的に非スコープ）:**
- LangGraph checkpointer の導入・グラフの中断/再開（clarify はユーザーの返答を**新規の独立したグラフ実行**として扱う。既存の会話履歴機構と `condense()` がそのターンの文脈を引き継ぐ。グラフの「1リクエスト = 1実行のステートレスな関数」という M7 の原則をそのまま維持する）
- rewrite ノード段階でのクエリ曖昧性検知（clarify のトリガーは grade のグレーゾーンスコアのみ。rewrite 自体には変更を加えない）
- rewrite に渡す履歴ターン数 N の削減（レイテンシ対応。M7 §10 で持ち越された別項目であり、本スペックとは無関係）
- LLM grader の導入（ADR 0006 により GO 達成のため任意の将来検討に格下げ済み。本スペックのグレーゾーン処理はこれを代替する）
- clarify の連続発生に対するループガード（§7 参照。意図的に対応しない）

---

## 2. 用語定義（M7 用語に追加するもののみ）

### THETA_HIGH（θ_high）

grade ノードが `grounded` と判定するための rerank score の上限側閾値。`THETA_HIGH > THETA` を満たす。`routing_theta_high` として `core/config.py` に追加する（既存 `routing_theta` と対称の命名）。

### clarify（経路）

grade が「関連しそうだが確信を持てない」と判定した場合の経路。検索結果を確定コンテキストとして生成には使わず、`kept`（THETA 以上のチャンク）を弱い候補として参照しつつ、質問の意図を確認する聞き返し文を LLM が生成する。回答には出典を付けない（`grounded` のみが出典を持つ）。

### 経路判定の全体像（M7 §2 を拡張）

```
top_score = kept が空でなければ先頭(最高スコア)チャンクの rerank_score、空なら None

top_score is None            → direct  （関連チャンクなし）
top_score < THETA            → direct  （関連チャンクなし。kept が空になるため上と同じ分岐で表現される）
THETA <= top_score < THETA_HIGH → clarify（弱い関連候補あり。確信不足）
top_score >= THETA_HIGH       → grounded（確信のある関連チャンクあり）
```

`kept`（`score >= THETA` のチャンク全体）は `grounded` と `clarify` の両方で共有する。`grounded` は `kept` を確定コンテキストとして生成に使い、`clarify` は同じ `kept` を「聞き返しの材料」として使う（弱い候補として提示するのみで、コンテキストとして断定的に扱わない）。

---

## 3. 設計原則（M7 §3 を継承。追加分のみ）

### 3.1 グレーゾーンの非対称性は grounded 側に残す

M7 の設計原則「誤判定コストの非対称性（迷ったら grounded に倒す）」は、THETA 未満（明確に無関係）のケースには引き続き適用する（`direct` ではなく安全側に倒す既存の防御的デフォルトは変更しない）。一方、THETA と THETA_HIGH の間のグレーゾーンについては、「無理に grounded/direct のどちらかに倒す」のではなく「ユーザーに確認する」ことが、断定的な誤答よりも better user experience であるという判断が M8 の前提である。

### 3.2 グラフはステートレスなまま

clarify 経路を追加しても、グラフの「1リクエスト = 1実行」という M7 の原則（`docs/specs/26071422-m7_adaptive_routing/spec.md` §3.2）は変更しない。checkpointer は導入しない。ユーザーの clarify への返答は、既存の会話履歴ロード・保存（`api` 層）と `rewrite` ノードの `condense()` によって、通常のフォローアップターンと同じ経路で処理される。

---

## 4. アーキテクチャ

### 4.1 グラフ構造

グラフの配線（`rewrite → retrieve → grade → (conditional) → generate`）自体は変更しない。conditional edge の分岐先が2つから3つに増えるのみ。

```python
graph.add_conditional_edges(
    "grade",
    _route_after_grade,
    {"grounded": "generate", "clarify": "generate", "direct": "generate"},
)
```

`_route_after_grade` のフォールバック値（route 未設定時）は `grounded` のまま変更しない（M7 §3.1 の安全側デフォルトを継承）。

### 4.2 State 定義

`graph/state.py` の `GraphState.route` の型を拡張する。

```python
route: Literal["grounded", "clarify", "direct"]
```

`kept` フィールドの意味は変わらない（`score >= THETA` のチャンク全体）。新規フィールドは追加しない。

`graph/state.py` モジュール docstring は「将来の checkpointer（PostgresSaver、M8）導入時に局所変更で差し込めるようにするための制約」と記載しているが、本スペック（§3.2、§4.1「Out」）で checkpointer を導入しない方針を確定したため、この記述は古い想定を指したままになる。実装タスクでこの一文を削除・訂正する（State を JSON シリアライズ可能な TypedDict に限定する制約自体は理由を変えて維持してよいが、「M8」という名指しは外す）。

### 4.3 ノード仕様

#### grade（変更）

`core/config.py` に `routing_theta_high: float` を追加する（デフォルト値は §6 のキャリブレーション結果を採用する。暫定値は起票せず、実装タスクでキャリブレーション後に確定する）。

```python
theta = settings.routing_theta
theta_high = settings.routing_theta_high
kept = [c for c in retrieved if c.get("rerank_score", theta) >= theta]
top_score = kept[0].get("rerank_score") if kept else None  # kept は既存実装同様 retrieved の降順を維持

if not kept:
    route = "direct"
elif top_score is not None and top_score >= theta_high:
    route = "grounded"
else:
    route = "clarify"
```

rerank score を持たないチャンク（Voyage リランク失敗時の RRF フォールバック）を `kept` に含める既存の安全側デフォルト（`.get("rerank_score", theta)`）は変更しない。`grade` が「THETA/THETA_HIGH との比較のみを行う純関数」であるという M7 の申し送り（`grade.py` 冒頭コメント）は引き続き遵守し、カバレッジ判定ロジックを追加しない。

Langfuse の trace レベル metadata（`route`, `theta`, `kept_count`, `top_score`）に `theta_high` を追加する。

#### generate（変更）

`route == "clarify"` の分岐を追加する。

```python
if route == "direct":
    stream = generate_direct_answer_stream(query)
elif route == "clarify":
    kept_chunks = cast(List[Dict[str, Any]], state.get("kept", []))
    stream = generate_clarify_answer_stream(query, kept_chunks)
else:
    kept_chunks = cast(List[Dict[str, Any]], state.get("kept", []))
    stream = generate_answer_stream(query, kept_chunks)
```

`clarify` は `citations` イベントを送出しない（`direct` と同様、確定した出典を持たないため）。

#### rewrite / retrieve

変更なし。

---

## 5. 生成（`generation/generator.py`）

新規関数 `generate_clarify_answer_stream(query: str, weak_candidates: List[Dict[str, Any]])` を追加する。既存の `generate_answer_stream` / `generate_direct_answer_stream` と同じ関数群（`generation/` 層のみが LLM を呼ぶという AGENTS.md の制約に従う）に置く。

- `weak_candidates`（= `kept`）のタイトル・見出しパスを LLM に渡し、「これらのトピックのうちどれについて聞きたいか、もう少し具体的に教えてください」という趣旨の聞き返し文を生成させる。チャンク本文をそのまま context として断定的に使わない（`grounded` との違いはここ）
- 新規プロンプト `CLARIFY_SYSTEM_PROMPT` を `prompts/routing.py` に追加する（`GROUNDED_SYSTEM_PROMPT` / `DIRECT_SYSTEM_PROMPT` と同列）
- 既存の Langfuse `@observe(as_type="generation")` 計装パターン・SDK ラッパーをそのまま踏襲する（M7 §3.3「LangGraph は薄く使う」を継承）

---

## 6. 評価（Eval）

### 6.1 THETA_HIGH のキャリブレーション

M7 T1/T2 と同様の手順を踏む。

1. 既存 routing eval データセット（`backend/evals/dataset/routing.jsonl`、130件）のうち `category: ambiguous`（30件）を中心に見直し、`expected_route: "clarify"` が妥当なケースをラベリングし直す。既存の「迷ったら grounded/direct に倒す」ラベリング方針（`routing-README.md` §2）を、M8 では「迷ったら clarify が妥当かをまず検討する」方針に改める
2. 既存のスコア収集基盤（`routing_scores.jsonl`）を再利用し、`expected_route` ごとのスコア分布を分析して THETA_HIGH の初期値を決定する（THETA=0.56 は不変。ADR 0001 と同様の分析手順で新規 ADR を起票する）
3. `routing-README.md` に clarify カテゴリのラベリング基準・根拠を追記する（既存の corpus/general/ambiguous/followup 各節と同じ形式）

### 6.2 指標と合格基準

`make eval-routing` の混同行列を2値から3値（grounded/clarify/direct）に拡張する。M7 §7.2 の合格基準に、clarify の適合率・再現率に関する基準を追加する（具体的な閾値はキャリブレーション実施タスクで、スコア分布の実データを見た上で決定する。プレースホルダとしない）。

### 6.3 前提バグの修正

`evals/routing.py::retrieve_and_grade()` は `graph/nodes/grade.py::grade()` を LangGraph の実行コンテキスト外から直接呼び出す設計（Voyage 呼び出しペーシング制御のため、モジュール docstring に理由が明記されている）。T6 で `grade()` 冒頭に追加された `get_stream_writer()` は、実際のグラフ実行以外の文脈で呼ばれると `RuntimeError` を送出するため、このバグが未修正のままでは THETA_HIGH のキャリブレーション（§6.1）も3値化 eval も実行できない。

修正方針（`grade.py` の局所修正のみ。新規抽象化を導入しない）:

```python
try:
    writer = get_stream_writer()
except RuntimeError:
    writer = lambda event: None
```

この修正は M8 の実装着手前（タスク分割の先頭）に行う。

---

## 7. フロントエンド

### 7.1 SSE

`route_decided` イベントの `route` フィールドに `"clarify"` が乗る。新規イベント型は追加しない（M7 の SSE プロトコル後方互換の方針を継承）。

### 7.2 UI

- `frontend/src/lib/chat-adapter.ts`: route の型を `"grounded" | "direct" | "clarify"` に拡張し、`route_decided` パース時の許容値に `"clarify"` を追加
- `frontend/src/components/RouteBadge.tsx`: 3状態表示に拡張する。`clarify` 用の見た目（例: 黄系バッジ + 疑問符アイコン、tooltip「質問の意図を確認しています」）を追加する

### 7.3 会話フロー

clarify 応答は通常の assistant メッセージとして `done` 一括保存され、会話履歴に含まれる（M2 の永続化方式をそのまま使う。特別な保存処理は追加しない）。ユーザーの返答は既存の condense（rewrite ノード）が履歴を踏まえて処理する、独立した新規ターンである。

---

## 8. 既知の制限（対応しない）

グレーゾーンな質問に聞き返した後、ユーザーの返答が再び THETA/THETA_HIGH の間に該当した場合、連続して `clarify` になりうる。ループガード（例: 直近ターンが clarify だった場合は grounded/direct のどちらかに強制する）は設けない。理由は以下の2点。

1. checkpointer を導入しない設計上、グラフは前のターンが clarify だったことを state として認識できない（会話履歴からの推定は可能だが、複雑さに見合わない）
2. 連続する聞き返しは、断定的な誤答よりもユーザー体験として許容範囲と判断する（Out-of-scope の明示であり、実装上のバグではない）

---

## 9. 依存関係とリスク

- **依存**: M7（grade/generate ノード、THETA、routing eval 基盤）、M6（`generation.condense()` 経由で LLM provider 抽象化の恩恵を受ける。rewrite 自体は無変更）
- **リスク**: THETA_HIGH の初期値が不適切だと、clarify が発生しすぎて UX を損なう（ほぼ全ての質問に聞き返す）か、ほとんど発生せず機能として意味を持たない（THETA と実質同じ挙動になる）可能性がある。M7 の THETA キャリブレーションと同様、eval データセットのスコア分布分析で緩和する
- **依存方向**: 新規パッケージの追加なし。AGENTS.md §3 の改訂は不要（既存の `graph`/`generation`/`prompts` 層内で完結する）

---

## 10. タスク分割（概要）

詳細は `docs/specs/26071715-m8_clarify_route/tasklist.md` で管理する。想定順序:

- T0: 前提バグ修正（§6.3。`grade.py` の `get_stream_writer()` フォールバック）+ M7実装状況の前提確認
- T1: routing eval データセットの clarify ラベリング見直し（§6.1手順1）
- T2: スコア分布分析と THETA_HIGH 初期値決定（GO/NO-GO 判定。§6.1手順2、ADR起票）
- T3: `core/config.py` への `routing_theta_high` 追加、grade ノードの3値化実装（§4.3）
- T4: clarify プロンプト・`generate_clarify_answer_stream` 実装、generate ノードの分岐追加（§5）
- T5: フロントエンド3状態対応（§7）
- T6: `make eval-routing` の3値混同行列・合格基準拡張、可観測性の仕上げ（§6.2）

---

## 11. 未決事項

なし（本スペック起票時点で確定した内容のみを記載。実装タスクで判明した事項はタスクリスト・ADR に記録する）。
