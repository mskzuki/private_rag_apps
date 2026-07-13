# Private RAG Apps — M3 フィーチャースペック: Eval 拡充（データセット・生成指標・CI 連携） (m3_eval_expansion.md)

> 配置先: `docs/specs/m3_eval_expansion.md`
> 対象マイルストーン: **M3**（requirements.md §10）
> 充足要件: **NFR-1（完全）** / **§9 評価計画（拡充）**、関連 **NFR-3 / NFR-5 / NFR-6 / NFR-7 / NFR-8 / §12 Definition of Success**
> 上位ドキュメント: 要件=`requirements.md`、構成=`architecture.md`、物理設計=`db_design.md`、作業規約=`AGENTS.md`。
> **矛盾時の優先順位**（AGENTS.md 冒頭）: 本スペック > AGENTS.md > 一般慣習。本書が上位ドキュメントを更新する箇所は §13 に明記し、実装 PR で反映する。

---

## 1. 目的と背景

**Eval はこのプロジェクトのショーケースの核**（requirements §1 の第1項、NFR-1・§9 に★）。「なんとなく動く RAG」ではなく、検索・回答品質を**数値で計測し、変更の前後で回帰を検出できる**ことを示すのが目的。

M0 で最小 Eval（10 問 + Recall@5）を導入し、M1（ハイブリッド化 + リランク）の before/after を計測してきた。M3 はこれを **プロダクション水準の評価基盤**へ拡充する:

1. **ゴールデンデータセットを 30〜50 問へ拡充**（seed コーパスと兼用。FR-7 / §9）
2. **検索指標の拡充**: Recall@5 のみ → **Recall@5 / Recall@10 / nDCG@10 / MRR**（NFR-1）
3. **生成指標の追加**: **Faithfulness / Answer Relevance**（LLM-as-judge。NFR-1）
4. **CI 連携**: プロンプト・検索・チャンキング・埋め込み変更を含む PR で自動実行し、**ベースラインからの劣化をゲート**（§9 / AGENTS.md §7・§9・§11）
5. **スコア推移の公開**: M1 前後を含む Eval レポートをリポジトリに公開（§12 Definition of Success）

> M3 完了をもって「Eval を回さずにプロンプト/チャンキング/埋め込みを変更しない」（AGENTS.md §11 DO NOT）が **CI で強制**される状態になる。

---

## 2. スコープ

### 2.1 In scope（M3 で実装する）

- ゴールデンデータセット（30〜50 問、正解ラベル + 参照回答）の作成とスキーマ確定
- 検索指標（Recall@5 / Recall@10 / nDCG@10 / MRR）の計算実装
- 生成指標（Faithfulness / Answer Relevance）の LLM-as-judge 実装（判定プロンプトは `prompts/`）
- 評価ハーネスの拡張（`make eval`）: データセット → 検索 → 指標 → 生成 → 判定 → **レポート出力**（provenance 付き）
- **ベースライン比較と回帰検出**（committed baseline との差分）
- **CI ワークフロー**（対象 PR で自動実行・ゲート・before/after を PR に記載）
- Eval レポートの公開（`docs/eval_report.md` 相当）
- （任意）Langfuse Datasets/Experiments への実験ミラーリング（可視化・ショーケース）

### 2.2 Out of scope（M3 では扱わない）

| 項目 | 送り先/理由 |
|---|---|
| マルチターン（condense）評価の本格化 | M3 では**小規模サブセットの sanity check のみ**（非ゲート。§8）。本格的な会話評価は後続 |
| 人手評価（human rating）・アノテーション UI | v1 スコープ外。LLM-as-judge + 少数の手作業正解で足りる |
| 自動データセット生成（合成 QA）| v1 では手作業中心。合成は将来検討（品質担保が別課題） |
| レイテンシ/TTFT の SLO ゲート | NFR-2 の領域。Eval（品質）とは別軸。M2 §7 で計測済み |
| A/B 実験・多構成同時ベンチの作り込み | Langfuse Experiments の範囲で足りる分のみ。独自基盤は作らない |
| ragas / deepeval 等への全面移行 | 依存を増やさず自前の薄い判定を基本とする（§5.4 で比較） |

### 2.3 前提（M0〜M2 で完成しているもの）

- `evals/`（ゴールデンデータセットと評価ハーネス）と `make eval`、最小 Eval（10 問 + Recall@5）
- `retrieval`（ハイブリッド + RRF + Voyage rerank、`top_k = 8`）と `generation`（出典付き生成）
- seed コーパス（`seed/`。デモ兼用。FR-7）
- Langfuse 計装（LLM/埋め込み/リランクのトレース・コスト。NFR-4）
- **依存方向**（AGENTS.md §3）: **LLM 呼び出しは `generation/` と `evals/` のみ**。判定モデル（judge）は `evals/` から呼ぶ。検索は `retrieval/` を、生成は `generation/` を呼び出して評価する。

---

## 3. ゴールデンデータセット

### 3.1 規模・出所

- **30〜50 問**（§9）。seed コーパス（`seed/`）を根拠文書とし、**Eval データセットとデモモードを兼用**（FR-7）。
- **日本語を含む現実的な文書構成**（requirements §NFR-1 の条件、pg_bigm 日本語検索の実証も兼ねる）。
- 難易度・種別のタグ（例: `lookup`（単純検索）/ `synthesis`（複数文書統合）/ `negative`（コーパスに答えが無い=「見つからない」を期待））を付け、**negative ケースを必ず含める**（NFR-6 ハルシネーション/不知応答の検証）。

### 3.2 データセットスキーマ（JSONL・`evals/dataset/` に配置）

```json
{
  "id": "q001",
  "question": "増分再取り込みで無変更ファイルをスキップする仕組みは?",
  "relevant": [
    {"path": "seed/design/ingestion.md", "heading": "増分取り込み", "grade": 3},
    {"path": "seed/ops/notes.md", "heading": "content_hash", "grade": 1}
  ],
  "reference_answer": "content_hash を比較し、無変更ならチャンクの再埋め込みをスキップする。",
  "tags": ["synthesis"],
  "expect_no_answer": false
}
```

- `relevant`: **正解の根拠文書**（後述 §3.3 のとおり path/見出し単位）。`grade` は nDCG 用の等級（任意。省略時は binary=1 扱い）。
- `reference_answer`: Answer Relevance / Faithfulness 判定の**参考**（judge プロンプトに与える。厳密一致採点はしない）。
- `expect_no_answer: true` の問い（negative）は「該当情報が見つかりません」を正解とし、検索指標の対象外・生成は「不知応答か」を判定。
- `turns`（任意）: マルチターン用の会話列。指定時は §8 のサニティ評価対象になる（単一ターン項目には付けない）。

### 3.3 ★正解の粒度 = 文書（path）レベル（chunk_id を正解に使わない）

**重要な設計判断**: 正解ラベルは **chunk の UUID ではなく `sources.path`（+ 任意の見出しアンカー）で表現**する。理由は、db_design の更新戦略が「source 更新時に chunks を全削除→再挿入」（全置換）であり、**再取り込みのたびに `chunks.id` が変わる**ため。chunk UUID を正解に固定するとデータセットが再取り込みで壊れる。

- **relevance 判定**: 検索で返ったチャンクを `source_id → sources.path` に写像し、**その path が `relevant` に含まれれば「正解文書がヒット」**とみなす。
- 見出しアンカー（`heading`）は診断・graded 採点の補助に使う（一致必須にはしない。チャンク境界の揺れに強くするため）。
- これにより **チャンキング戦略を変えてもデータセットを作り直さずに再評価**できる（M3 の主眼＝チャンキング変更の回帰検出に必須）。

### 3.4 バージョニングと再現性

- データセットに `version` を持たせ、`evals/dataset/` を Git 管理。**実データ（個人文書）は含めない**（NFR-3。同梱するのは seed 由来のみ）。
- レポートに **corpus のハッシュ**（seed の内容ハッシュ）と **dataset version** を記録し、スコアの比較可能性を担保（§6.2）。
- **seed とデータセットの結合注意**: seed はデモ兼用のため、デモ都合で seed の追加/リネーム/削除を行うと `relevant` の path が静かに壊れうる。**seed 変更時は dataset の path 実在チェック（§12 のスキーマ検証）を必ず通す**運用とする。

---

## 4. 検索指標（Retrieval Metrics）

### 4.1 指標と定義（★`@k` は「取得チャンクリスト」に対して数える）

前提の統一: **`@k` は検索が返した*チャンクリスト*（長さ `EVAL_TOP_K`）の先頭 k 件に対して数える**（doc を先に畳み込んでから k を取るのではない）。正解の hit 判定は §3.3 のとおり `source_id → path` 写像で行う。

| 指標 | 定義 |
|---|---|
| **Recall@k** | (top-k チャンク内に**1つでも**チャンクが入った正解文書の数) / (正解文書の総数)。k = 5, 10 |
| **nDCG@10** | `DCG@10 = Σ_{i=1..10} rel(i)/log2(i+1)`、`nDCG = DCG/IDCG`。`rel(i)` は順位 i のチャンクの**文書が正解なら 1（`grade` 指定時はその等級）**、非正解なら 0。ただし**同一正解文書の2つ目以降のチャンク位置は rel=0**（doc の二重計上防止） |
| **MRR（@`EVAL_TOP_K`）** | 1 / (打ち切り内で最初に現れた「正解文書に属するチャンク」の順位)。打ち切り内に無ければ 0 |

- **doc-dedup は「得点する順位」の決定にのみ使う**（リストの長さは変えない）: ある正解文書が複数チャンクで出たら、**最上位の1チャンク位置でだけ得点**し、同一文書の下位チャンク位置は rel=0 とする。Recall の分子・MRR の順位・nDCG の rel すべてこの規則で一貫させる。
- **IDCG の基準**: 正解文書を doc-dedup した理想順序（正解を上位から `grade` 降順に並べた仮想リスト）で DCG を計算した値を IDCG とする。これで nDCG が一意に定まる。
- **binary が既定**（`grade` 省略時は 1）。graded は nDCG の分解能を上げたいときの任意拡張。

### 4.2 計測ポイント（どの段の出力で測るか）

- **主指標 = リランク後の最終リスト**（生成が実際に見る順位）。Recall@10 を測るには本番の `top_k=8` では足りないため、**評価専用に `EVAL_TOP_K`（≥10、既定 12）まで取得**する（本番の生成用 `top_k=8` とは別パラメータ）。`@k` はこのリストの先頭 k 件で数える（§4.1）。
- **診断指標 = リランク前（RRF 融合直後）**でも同じ指標を算出し、**リランクの寄与を before/after で可視化**（M1 の成果=リランク導入の効果をレポートで裏づける）。
- **★実装インターフェースの影響**: 「融合直後」と「リランク後」の両リストを得るため、**`retrieval` に評価/診断モードを設け、`{fused_ranking, reranked_ranking}` の両方を返せるようにする**。evals 側で検索ロジックを再実装しない（AGENTS.md §3 の依存方向・DRY を守る。evals はこの診断 API を読むだけ）。この I/F 追加は §10・§11 に反映する。
- **HNSW 近似のブレを潰す**: seed は小規模なので、**評価時は `hnsw.ef_search` を実質全探索に近い大きめ値に固定**し、近似最近傍由来の recall 揺れをほぼ排除する（索引再構築のたびのブレでハードゲートが偽陽性 fail するのを防ぐ。§7.4）。残差は §7.4 の tolerance で吸収する。

---

## 5. 生成指標（Generation Metrics・LLM-as-judge）

### 5.1 指標

- **Faithfulness（忠実性）**: 回答の各主張が**取得コンテキストに支持されているか**（コンテキスト外の主張=ハルシネーションが無いか）。NFR-6 の中核。
- **Answer Relevance（回答妥当性）**: 回答が**問いに答えているか**（的外れ・冗長でないか）。

### 5.2 LLM-as-judge の設計

- **判定モデルを固定**（`JUDGE_MODEL` = Claude 系の軽量モデル）し、**モデル名をレポートに必ず記録**（NFR-1 の条件）。`generation` の生成モデルとは分離する。
- **判定プロンプトは `prompts/`** に置く（ハードコード禁止。AGENTS.md §6/§11）。judge 呼び出しは `evals/` から行う（AGENTS.md §3）。
- **構造化出力**（JSON: `{score, rationale}`）を強制し、パースする。`score` は連続値（例 0.0–1.0）または離散等級を設定で選ぶ。
- **temperature = 0**（`JUDGE_TEMPERATURE`）で判定のブレを抑える。
- **Faithfulness の入力**: 問い / 回答 / **取得コンテキスト**（reference_answer は使わない=コンテキストへの忠実性を測るため）。
- **Answer Relevance の入力**: 問い / 回答 /（任意で reference_answer をヒントに）。
- **negative ケース**（`expect_no_answer`）: 「該当情報が見つかりません」相当を返せているかを別判定（不知応答の正しさ）。誤って答えをでっち上げたら Faithfulness を最低評価とする。
  - ※ 注意: negative でも retrieval は tangential なチャンクを top-k で返すため、architecture の「検索 0 件で見つからない」短絡は基本的に発動しない。**negative の合否は「弱い/無関係なコンテキストで生成が棄権（abstain）できるか」で決まる**（プロンプトの棄権指示の質を測るケース）。
- **★評価時は generation の decoding も固定**（§6.2 と一体）: judge だけでなく**被評価側の生成**も、eval 実行では `temperature=0`・`max_tokens` 固定で走らせ、run 間の生成揺れが生成指標に二重に乗るのを防ぐ。本番の生成は temp>0 でも可。固定値は provenance に記録する。

### 5.3 判定の変動対策（judge variance）

- LLM 判定は temp=0 でも完全決定的ではない。**生成指標はノイズを含む前提**で扱う:
  - 既定は 1 サンプル。`EVAL_JUDGE_SAMPLES > 1` で複数回判定し平均（コストと相談）。
  - **ゲートは検索指標を主・生成指標を従**にする（§7.3）。生成指標は閾値を緩め、大幅劣化のみ検出。
- judge のトークン/コストは Langfuse に記録（NFR-5）。

### 5.4 実装方針（自前 judge を基本、ragas は代替として比較）

- **自前の薄い judge**（prompts/ + 構造化出力）を基本とする。理由: 判定モデルを Claude に固定でき、プロンプトを資産化・バージョン管理でき、依存を増やさない（NFR-7）。
- **ragas**（リファレンスフリーで faithfulness/answer relevancy を提供）は検討したが、**検索指標（Recall/nDCG/MRR）は正解ラベルが必要**で ragas 単体では賄えず、判定モデルの固定・プロンプト資産化の観点からも自前を優先。将来 ragas/deepeval を判定の一実装として差し込む余地は残す。

---

## 6. 評価ハーネス（`make eval`）

### 6.1 実行フロー

```mermaid
flowchart TD
    DS[golden dataset JSONL<br/>question + relevant(path) + reference] --> LOOP{各問}
    LOOP --> RET[retrieval.retrieve<br/>EVAL_TOP_K で取得]
    RET --> RM[検索指標<br/>Recall@5/10 nDCG@10 MRR<br/>（リランク後 / 前の両方）]
    RET --> GEN[generation.generate]
    GEN --> JUDGE[evals: LLM-as-judge<br/>Faithfulness / Answer Relevance]
    RM --> AGG[集計（平均・分布）]
    JUDGE --> AGG
    AGG --> REP[レポート出力<br/>JSON + Markdown（provenance 付き）]
    REP --> CMP{baseline 比較}
    CMP -->|許容超の劣化| FAIL[CI: fail]
    CMP -->|範囲内| PASS[CI: pass]
    REP -. 任意 .-> LF[Langfuse Datasets/Experiments へ実験記録]
```

- 実行は **CLI（`make eval`）でローカル完結**。外部 API（embed/rerank/judge/gen）は実呼び出し（テストとは別物。AGENTS.md §8）。

### 6.2 出力（レポートと provenance）

- **機械可読**: `evals/reports/<timestamp>.json`（各問のスコア + 集計 + メタ）。
- **人間可読**: サマリ Markdown（集計表、リランク前/後の比較、negative ケースの成否）。
- **provenance（必須）**: 埋め込みモデル/次元・rerank モデル・生成モデル・judge モデル名・**評価時の decoding 設定（生成/judge の temperature・max_tokens）**・検索パラメータ（`cand_k`/`rrf_k`/`fuse_k`/`EVAL_TOP_K`/`ef_search`）・**corpus ハッシュ**・**dataset version**・実行日時。これが無いとスコア比較が無意味になる。

### 6.3 ベースラインと回帰検出

- **committed baseline**: `evals/baselines/current.json`（現行の基準スコア）をリポジトリに置く。
- 実行結果を baseline と比較し、**メトリクスごとの許容誤差（tolerance）を超える低下**を「回帰」として検出。
- baseline の更新は**意図的な PR で行う**（スコア改善時に明示的に上書き。レビューで意図を残す）。

### 6.4 Langfuse Datasets/Experiments 連携（任意・ショーケース）

- 同じゴールデンデータを Langfuse **Dataset** として登録し、`make eval` の各実行を **Experiment（run）** として記録すると、Langfuse UI でスコア推移・実験横断比較が見られる（可観測性ピラーとの統合を示せる）。
- **位置づけ**: CI ゲートの正は §6.3 の committed baseline（外部依存なし・決定的）。Langfuse は**可視化の補助**であり、Langfuse 障害で CI が落ちないようにする（連携は best-effort）。

---

## 7. CI 連携

### 7.1 トリガ（対象 PR）

以下に触れる PR で Eval を必須実行（AGENTS.md §7・§9・§11）:

- `backend/src/private_rag_apps/prompts/**`（プロンプト）
- `backend/src/private_rag_apps/retrieval/**`（検索ロジック）
- `backend/src/private_rag_apps/ingestion/**`（チャンキング/正規化）
- `backend/src/private_rag_apps/generation/**`（生成）
- `evals/**`（データセット/ハーネス/baseline）
- 埋め込み/リランク/生成/judge のモデル・次元を変える設定変更

### 7.2 実行手順（再現性 = NFR-8 と同じ経路）

CI ジョブは本番と同じクリーン経路をたどる:

1. DB 起動（pgvector + pg_bigm）→ `make migrate`
2. **`make ingest`（seed コーパスを取り込み）** — 埋め込み次元/チャンキング変更時はここで再インデックスされる（AGENTS.md §7）
3. `make eval` 実行 → レポート生成 → baseline 比較
4. before/after を **PR に自動記載**（コメント or artifact。AGENTS.md §9）

- **API キーは CI シークレット**（`OPENAI_API_KEY` / `VOYAGE_API_KEY`）。Langfuse キーは任意（未設定でも eval は動く）。
- **コスト**: 30〜50 問 ×（embed + 検索 + rerank + 生成 + judge×samples）。小規模・上限が読めるため CI 実行を許容。judge は軽量モデルでコストを抑える（NFR-5）。

### 7.3 ゲート方針（検索は硬め・生成は柔らかめ）

| 指標群 | 方針 | 根拠 |
|---|---|---|
| 検索（Recall/nDCG/MRR） | **tolerance 超の低下で CI fail（ハードゲート）** | 決定的に近く、回帰の意味が明確 |
| 生成（Faithfulness/Answer Relevance） | **大幅低下のみ fail、軽微は warn（ソフト）** | judge のノイズ（§5.3）を許容するため |

- tolerance はメトリクスごとに設定（例: Recall 低下 > 0.03 で fail、Faithfulness 低下 > 0.1 で warn/fail）。値は初回ベースライン取得後に調整。

### 7.4 決定性と許容誤差

- 検索: `ef_search` を**評価時は全探索に近い大きめ値に固定**して索引再構築ごとの recall 揺れを潰す（§4.2）。候補件数/RRF パラメータも固定。残差のみ tolerance で吸収。
- 生成: 被評価側の生成も **temp=0・max_tokens 固定**で走らせる（§5.2）。判定（judge）も temp=0 + 構造化出力 + （必要なら）複数サンプル平均。**tolerance を 0 にしない**（judge の残ノイズによるフレーキー CI を避ける）。
- 上記の固定値はすべて provenance に記録する（§6.2）。

---

## 8. マルチターン（condense）の評価 — 小規模サニティのみ

- M2 で導入した condense（履歴考慮のクエリ書き換え）は、**少数（数問）のマルチターン項目**で「フォローアップが正しく自己完結クエリ化され、正解文書を引けるか」を sanity check する。
- **非ゲート**（本格的な会話評価は後続）。データセットには `turns`（会話列）を持つ拡張項目として少数だけ含める。
- 位置づけ: M2 §11 で「マルチターン用 Eval は M3 スコープ」とした持ち越しに対する**最小限の回答**。過剰実装はしない（§2.2）。

---

## 9. コスト管理（NFR-5）

- judge / embed / rerank / 生成の Eval 実行コストを **Langfuse で集計**（自作ダッシュボードは作らない。NFR-5）。
- judge は軽量モデル固定、`EVAL_JUDGE_SAMPLES` の既定 1。CI では毎回フル実行だが小規模で上限が読める。

---

## 10. データモデル・設定への影響

- **DDL 変更なし**。Eval はアプリの読み取り経路（`retrieval`/`generation`）を使うのみ。データセット/baseline/レポートは**ファイル（Git 管理）**で持つ。
- 新規設定（`core/config.py`。ハードコード禁止）:

| キー(例) | 用途 | 既定(暫定) |
|---|---|---|
| `JUDGE_MODEL` | LLM-as-judge のモデル名（記録必須） | Claude 系軽量 |
| `JUDGE_TEMPERATURE` | judge の温度 | 0 |
| `EVAL_GEN_TEMPERATURE` | 評価時の**被評価側 生成**の温度（run 間の揺れ防止。§5.2/§7.4） | 0 |
| `EVAL_GEN_MAX_TOKENS` | 評価時の生成 max_tokens（固定） | 実測後確定 |
| `EVAL_TOP_K` | 評価時の検索取得件数（Recall@10 用。本番 top_k と別） | 12 |
| `EVAL_EF_SEARCH` | 評価時の `hnsw.ef_search`（全探索寄りの大きめ値。§4.2/§7.4） | 大きめ（実測後確定） |
| `EVAL_JUDGE_SAMPLES` | judge の反復回数（平均） | 1 |
| `EVAL_DATASET_PATH` | データセット JSONL のパス | `evals/dataset/` |
| `EVAL_TOLERANCE_*` | メトリクス別の回帰許容 | 初回計測後に確定 |

---

## 11. 受け入れ条件（Acceptance Criteria）

満たして初めて M3 完了とする（AGENTS.md §10 DoD に加えて）。

**データセット（§9 / NFR-1）**
- [x] ゴールデンデータセットが 30〜50 問あり、negative（不知期待）ケースを含む — `backend/evals/dataset/m3_golden.jsonl`（31問、`tags` 内訳: lookup 24 / synthesis 4 / negative 3。うち `turns` 付きマルチターン項目1問）
- [x] 正解ラベルが **path（+任意 heading）レベル**で表現され、chunk_id に依存しない — `evals/schema.py:8-11` `RelevantDoc(path, heading, grade)` に `chunk_id` フィールドは存在しない
- [x] 実データを含まず seed 由来のみ（NFR-3）／dataset に version がある — 収録データは seed/`db_design.md`/`requirements.md` 等の設計文書由来のみ。`version` は各アイテムの JSON フィールドとしては持たないが、ファイル名（`m3_golden.jsonl`）＋ハーネス側の `provenance["dataset_version"]="m3_golden"`（`evals/__main__.py:138`）で一貫してトラッキングされている

**検索指標（NFR-1）**
- [x] Recall@5 / Recall@10 / nDCG@10 / MRR を算出できる（**`@k` は取得チャンクリスト基準**、正解は path 写像、**doc-dedup で二重計上しない**）— `evals/metrics.py:5-57` `evaluate_retrieval()`。`tests/evals/test_metrics.py::test_evaluate_retrieval_doc_dedup` で doc-dedup を検証
- [x] `retrieval` の診断 API が **融合直後リストとリランク後リストの両方**を返し、リランク前/後の指標を出せる — `retrieval/searcher.py` の `retrieve_context(..., diagnostic_mode=True)` が `{"fused_ranking":..., "reranked_ranking":...}` を返す
- [x] `EVAL_TOP_K ≥ 10` で Recall@10 が測れる／評価時 `ef_search` を大きめ固定して recall 揺れを抑えている — `core/config.py:36-37` `eval_top_k: int = 12`, `eval_ef_search: int = 100`（本番用 `candidate_k`/`rrf_k`/`fuse_k`/`rerank_top_k` とは別設定として分離されている）

**生成指標（NFR-1 / NFR-6）**
- [x] Faithfulness / Answer Relevance を LLM-as-judge で算出（判定モデル名を記録）— `evals/judge.py:44-53`、`provenance["models"]["judge"]=settings.judge_model`（`__main__.py:153`）
- [x] 判定プロンプトが `prompts/` にあり、構造化出力をパースしている — `prompts/judge.py`（`JUDGE_FAITHFULNESS_PROMPT`/`JUDGE_ANSWER_RELEVANCE_PROMPT`）、`evals/judge.py:8-42` `_call_judge()` がJSON抽出・パースし、`tests/evals/test_judge.py` が正常/markdown/不正JSONを検証
- [ ] **評価時は被評価側の生成も temp=0・max_tokens 固定**で走り、その値が provenance に記録される — **genuine gap**。`EVAL_GEN_TEMPERATURE`/`EVAL_GEN_MAX_TOKENS` は `core/config.py` に存在するが、`evals/__main__.py:28-34` の `get_answer()` のコメントで明言されている通り `generate_answer_stream()` は temp/max_tokens の上書きを受け付けず、実際には固定されていない。provenance にもこれらの値は記録されていない（`__main__.py:136-155` の `provenance` dict に該当フィールド無し）
- [x] negative ケースで「弱いコンテキストでの棄権（abstain）」の正しさを判定できる — `prompts/judge.py:8,31` の Faithfulness/Answer Relevance プロンプトが「見つからない」の正しい返答を1点・誤った断定を0点とする採点基準を明記。ただし専用のユニットテストは無い

**ハーネス・レポート（§9 / §12）**
- [x] `make eval` がデータセット→検索→指標→生成→判定→レポートを通しで実行する — `evals/__main__.py:42-243` `run_eval()`
- [x] レポートに provenance（各モデル名・検索パラメータ・corpus ハッシュ・dataset version）が記録される — `__main__.py:136-155`（embed/rerank フィールドを含め本 M5 セッションで追加済み。ただし `embed_dims`/`rerank` はハードコード値であり `settings` からの動的取得ではない旨がコード中コメントに明記）
- [x] committed baseline との比較で回帰を検出できる — `evals/baselines/current.json` が存在し、`__main__.py:163-188` で比較ロジックが実装されている。※ 現状の `current.json` の数値は丸い値（0.9/0.95等）で実 `make eval` 実行由来か未確認（実行は Docker 起動待ち）
- [ ] M1 前後を含むスコア推移の **Eval レポートが公開**されている（§12）— **未達（意図的に未チェック）**。`docs/eval_report.md` は現時点で存在しない。生成には実 DB に対する `make eval` 実行が必要で、Docker 起動が前提の M5 Phase 3 待ち

**CI（§9 / AGENTS.md §7・§9・§11）**
- [x] 対象パス（prompts/retrieval/ingestion/generation/evals）変更 PR で Eval が自動実行される — `.github/workflows/eval.yml:3-12` の `on.pull_request.paths` がスペック §7.1 の全パス（+`core/config.py`）と一致
- [ ] CI が migrate→ingest(seed)→eval の再現経路をたどる（NFR-8）— ステップ自体（migrate→ingest→eval）は `.github/workflows/eval.yml:48-69` に存在するが、**genuine gap**: DB サービスに `pgvector/pgvector:pg16`（無印）を使っており、pg_bigm 拡張が入っていない。`0001_init.py` の `CREATE EXTENSION IF NOT EXISTS "pg_bigm"` はこのイメージでは失敗する見込み（ローカルの `docker-compose.yml`/AGENTS.md §4 が明記する通り、pg_bigm 入りは `backend/docker/db/Dockerfile.local` でソースビルドしたカスタムイメージが必要）。CI が実際に緑になるかは要確認・要修正
- [x] 検索指標はハードゲート、生成指標はソフト（tolerance 設定済み）— `__main__.py:172-183`（reranked 指標の低下 > 0.05 で fail、generation 指標の低下 > 0.1 で warn）。※ tolerance はスペック §10 が想定する `EVAL_TOLERANCE_*` 設定ではなく `__main__.py` にハードコードされている（`core/config.py` に `eval_tolerance_*` は存在しない。AGENTS.md §6 のハードコード禁止からは軽微な逸脱）
- [x] before/after が PR に自動記載される — `.github/workflows/eval.yml:71-86`（`docs/eval_report.md` が存在すれば `github-script` で PR コメントとして投稿）

**共通（AGENTS.md §10）**
- [ ] `make lint` / `make test` が通る／依存方向（LLM は generation・evals のみ）を守る — `make lint` は 2026-07-13 時点でクリーン（exit 0）。`make test` は `tests/evals/`（DB非依存、10件）は全 pass するが、DB 接続を要する統合テストは本セッションで Postgres 未起動のため未確認。依存方向は確認済み（`retrieval/searcher.py` は generation/ingestion を import せず、evals は generation/retrieval を呼ぶのみで LLM 呼び出しは `evals/judge.py`・`generation/generator.py` に限定）だが、test 側が環境制約で未完了のため全体は未チェック
- [x] 本スペック §13 の上位ドキュメント反映が済んでいる — `requirements.md` v0.4（L143-144,247,285,297）・`architecture.md` v0.4（L155,267,312）・`AGENTS.md` §7 がいずれも path レベル正解・`EVAL_TOP_K` 分離・ハード/ソフトゲート・再現経路・`docs/eval_report.md` パスを反映済み

---

## 12. テスト方針（AGENTS.md §8。Eval 自体とは別物）

- **ユニット（決定的・API 非依存）**: Recall@k / nDCG@10 / MRR を**既知の入力**（合成したチャンク順位リスト + 各チャンクの所属 path + 正解 path 集合）で検算する。特に **`@k` がチャンク基準であること**、**同一正解文書の複数チャンクが二重計上されないこと（doc-dedup）**、**IDCG が doc-dedup 理想順序で作られること**、タイブレークを個別ケースでテスト。
- **ユニット**: データセットのスキーマ検証（必須フィールド・path 実在・grade 範囲）、judge 出力パーサ（不正 JSON のハンドリング）、baseline 比較（tolerance 境界）。
- **judge はモック/記録再生**（テストで実課金しない。AGENTS.md §8）。
- **ハーネス smoke**: 2〜3 問の極小データセットで end-to-end が回ることを、外部呼び出しをモックして確認。
- **Eval 本体（`make eval`）はテストではない**: 合否ではなくスコア回帰の監視に使う（AGENTS.md §8）。

---

## 13. 上位ドキュメントへの反映（本スペックによる変更点）

実装 PR で以下を合わせて改訂する（Definition of Success「文書と実装の一致」）:

1. **`requirements.md` §9 / §NFR-1**: 検索指標の計測が **path レベル正解**であること、**評価時 `EVAL_TOP_K` は本番 `top_k` と別**であること、CI ゲート方針（検索=ハード/生成=ソフト）を追記。
2. **`requirements.md` §12 Definition of Success**: 「Eval レポート公開」の具体パス（`docs/eval_report.md`）を明記。
3. **`architecture.md`**: `evals/` の評価フロー（§6.1）と judge が `evals/` から LLM を呼ぶ依存、および **`retrieval` の評価/診断モード（融合直後＋リランク後の両ランキングを返す）** を明記（AGENTS.md §3 と整合。§4.2）。
4. **`AGENTS.md` §7/§9**: CI の実行経路（migrate→ingest(seed)→eval）とゲート方針を反映。

> **決定事項**（Phase 0 にて確定）
> - スコア尺度: 生成指標は 0/1 の離散等級（Binary）とする。
> - tolerance の初期値: 初回ベースライン取得後に確定する。
> - Langfuse Datasets 連携: M3 ではフックだけ用意し、実配線は後続とする。
> - graded relevance（`grade`）: M3 では binary 開始とし、grade の付与は見送る。

---

## 14. 実装順序の目安 → 次アクション

AGENTS.md §12 に従い、**本スペック → `docs/specs/m3_tasklist.md` → 実装**の順で進める。概略の依存順:

1. データセット拡充（30〜50 問、path レベル正解、negative 含む、version 付与）
2. 検索指標の実装（Recall@5/10・nDCG@10・MRR、**chunk 基準・doc-dedup**、**`retrieval` 診断 API で融合前/後の両リスト取得**）
3. LLM-as-judge（判定プロンプト→ `prompts/`、構造化出力、`JUDGE_MODEL` 固定、negative 判定）
4. ハーネス統合とレポート出力（provenance・JSON/Markdown）
5. ベースライン確立と回帰検出（committed baseline・tolerance）
6. CI ワークフロー（トリガ・再現経路・ゲート・PR 自動記載）
7. （任意）Langfuse Datasets/Experiments 連携
8. マルチターン小規模サブセット（sanity・非ゲート）
9. 仕上げ: Eval レポート公開、§13 の上位ドキュメント反映、受け入れ条件の充足確認

> 次に作成すべき成果物は **`docs/specs/m3_tasklist.md`**（チェックボックスでの進捗管理。AGENTS.md §12）。

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.2 | 2026-07-07 | セルフレビュー反映: (1) **`@k` を取得チャンクリスト基準に統一**し、doc-hit / doc-dedup / IDCG 基準を厳密化（§4.1、受け入れ・テストに波及）。(2) **評価時は被評価側の生成も temp=0・max_tokens 固定**（生成揺れの二重計上を防止。§5.2/§6.2/§7.4/§10）。(3) リランク前/後の指標のため **`retrieval` に評価/診断モード**（融合前/後の両ランキング返却）を明記し依存方向を保持（§4.2/§13）。(4) **評価時 `ef_search` を全探索寄りに固定**して索引再構築由来の recall 揺れを抑制（§4.2/§7.4）。(5) negative は「弱いコンテキストでの生成 abstain」判定であることを明確化（§5.2）。(6) `turns` 任意フィールド・seed 変更時の path レビュー・設定キー追加などの minor 修正 |
| v0.1 | 2026-07-07 | 初版。M3（Eval 拡充）のフィーチャースペック。データセット 30〜50 問・**path レベル正解（chunk_id 非依存）**、検索指標（Recall@5/10・nDCG@10・MRR、リランク前/後・`EVAL_TOP_K` 分離）、生成指標（Faithfulness/Answer Relevance の LLM-as-judge、判定モデル固定・prompts 化）、ハーネス/レポート（provenance）、committed baseline による回帰検出、CI 連携（再現経路・検索ハード/生成ソフトのゲート・PR 自動記載）、Langfuse Datasets 任意連携、マルチターン小規模サニティを定義。上位ドキュメント反映点を §13 に明記 |