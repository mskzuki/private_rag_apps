# M1 — ハイブリッド検索 + リランク（フィーチャースペック）

> 配置先: `docs/specs/m1_hybrid_search.md`
> 準拠: requirements.md v0.3 / architecture.md v0.3 / db_design.md v0.2 / AGENTS.md v0.4
> 前提: **M0（Walking Skeleton）完了済み**（ベクトル検索のみ → 非ストリーム回答 + Langfuse + Recall@5）
> ステータス: ドラフト v0.1

---

## 1. ゴール

M0 の**ベクトル検索のみ**の検索段を、**ハイブリッド検索（ベクトル + pg_bigm 全文）→ RRF 融合 → リランク**に置き換える。そして「M0(vector) → M1(hybrid) → M1(hybrid+rerank)」の検索品質の変化を **Eval で before/after として記録**する。

この before/after 表が本プロジェクト最大のショーケース成果物になる（「検索を良くした」を主張ではなく数値で示す）。

充足する要件: **FR-3, NFR-1**

---

## 2. スコープ

### In scope (M1)

- Alembic `0002`: `chunks.content` への pg_bigm GIN 索引追加
- 全文検索（pg_bigm）の検索段
- **RRF 融合**（ベクトル候補 + 全文候補を順位ベースで統合）
- **リランク**（Voyage rerank-2.5、融合上位 → 最終 top_k=8）
- `RetrievalStrategy`（`vector` / `hybrid` / `hybrid_rerank`）の切替設定 — Eval の before/after に使う
- Langfuse スパンの拡張（`vector_search` / `fts_search` / `rrf_fuse` / `rerank`）
- Eval 拡張: ゴールデン10問に対し **Recall@5 / Recall@10 / nDCG@10 / MRR** を**3モードで**算出し比較表を出力・記録

### Out of scope (M1 → 後続M)

| 項目 | 送り先 |
|---|---|
| SSE ストリーミング / assistant-ui UI / 出典カードUI | M2 |
| 会話履歴・複数ターン・condense | M2 |
| 増分再取り込み・削除反映・データ管理UI | M4 |
| ゴールデン30〜50問への拡充・生成品質(Faithfulness)・CI連携 | M3 |

### 変えないもの

- **API 契約は不変**（`POST /api/chat` は M0 と同じ `{content, citations}` を返す）。改善は検索段の内部に閉じる
- 生成プロンプト・生成の非ストリーム挙動（M0 のまま。渡すチャンクが top-5 → top-8 に増えるのみ）
- DB スキーマ（`chunks.embedding` / `content` は M0 で用意済み。M1 は索引の追加のみ）

---

## 3. 受け入れシナリオ（Given / When / Then）

**S1: 語彙一致が効くクエリ**
- Given デモコーパス取り込み済み・`hybrid_rerank` モード
- When 固有語を含む質問（例:「pg_bigm を選んだ理由は?」）を送る
- Then 該当固有語を含むチャンクが全文検索経由でも候補に入り、リランク後の top_k に反映され、出典付き回答が返る

**S2: 片側の検索が0件でも動く**
- Given 同上
- When 全文一致が起きにくい抽象的な質問を送る（pg_bigm 側が0件になりうる）
- Then エラーにならず、ベクトル側の結果だけで融合・リランクして回答する

**S3: API 契約の不変**
- Given 同上
- When M0 の受け入れシナリオ（コーパス内 S2 / コーパス外 S3）を再実行
- Then レスポンス形式は M0 と同一（`{content, citations}` / 「見つからない」）で、後方互換が壊れていない

**S4: 可観測性の拡張**
- When 任意の `/api/chat` を1回実行
- Then Langfuse トレースに `embed_query` / `vector_search` / `fts_search` / `rrf_fuse` / `rerank` / `generate` のスパンが出て、**rerank のコスト・レイテンシ**も記録される

**S5: Eval before/after ★核**
- Given ゴールデン10問
- When `make eval`
- Then `vector` / `hybrid` / `hybrid_rerank` の3モードで Recall@5・Recall@10・nDCG@10・MRR が算出され、**比較表**として出力・記録される（M0 ベースラインからの改善が読み取れる）

---

## 4. 技術設計（M1 固有）

### 4.1 検索パイプライン

```
question
  → embed_query(voyage-4-lite, input_type=query)
  → [ vector_search(top cand_k=50) ]  ┐
  → [ fts_search  (top cand_k=50) ]  ┘→ rrf_fuse(k=60, 上位 fuse_k=40)
  → rerank(voyage rerank-2.5, 40件 → top_k=8)
  → context(top_k=8) → generate（M0 のまま）
```

デフォルト（設定化, `core/config.py`）:

| 設定 | 既定値 |
|---|---|
| `RETRIEVAL_STRATEGY` | `hybrid_rerank` |
| `CANDIDATE_K`（各検索の候補数） | 50 |
| `RRF_K` | 60 |
| `FUSE_K`（融合後・リランク投入数） | 40 |
| `RERANK_TOP_K`（最終・生成へ渡す数） | 8 |

### 4.2 RetrievalStrategy（Eval の before/after 用）

検索段を戦略で切替可能にする。生成層はこの切替を意識しない（インターフェース不変）。

| モード | 内容 | 用途 |
|---|---|---|
| `vector` | ベクトルのみ top_k（M0 相当） | before（ベースライン） |
| `hybrid` | ベクトル + 全文 → RRF → top_k（リランクなし） | 中間比較 |
| `hybrid_rerank` | 上記 + リランク → top_k | after（本番既定） |

### 4.3 ハイブリッド検索 SQL（RRF）

db_design §6 に準拠。ベクトル CTE + 全文 CTE を RRF で融合し `FUSE_K` 件を返す。

```sql
WITH vector_search AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY c.embedding <=> :q_embedding) AS rank
    FROM chunks c
    JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL
    ORDER BY c.embedding <=> :q_embedding
    LIMIT :cand_k
),
fts_search AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY bigm_similarity(c.content, :q_text) DESC) AS rank
    FROM chunks c
    JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL
    WHERE c.content =% :q_text
    LIMIT :cand_k
),
fused AS (
    SELECT id, 1.0/(:rrf_k + rank) AS score FROM vector_search
    UNION ALL
    SELECT id, 1.0/(:rrf_k + rank) AS score FROM fts_search
)
SELECT id, SUM(score) AS rrf_score
FROM fused GROUP BY id
ORDER BY rrf_score DESC
LIMIT :fuse_k;
```

- 融合後、`FUSE_K` 件の `content` / 出典メタを取得してリランクへ渡す。
- どちらかの CTE が0件でも `UNION ALL` で成立する（S2）。

### 4.4 リランク

- Voyage **rerank-2.5** に「クエリ + `FUSE_K` 件の本文」を渡し、関連度上位 `RERANK_TOP_K=8` を採用
- 返った8件を出典番号 `[1..8]` に対応付けて generation の context にする
- 候補が8件未満なら、ある分だけを採用（下限で落とさない）

### 4.5 可観測性（スパン拡張）

M0 の `embed_query → retrieve → generate` を、`retrieve` を親として次の子スパンに分解:

```
trace(chat)
├─ embed_query      (voyage: query 埋め込み, tokens/cost)
├─ retrieve
│  ├─ hybrid_search (親スパン — vector CTE + fts CTE は 1 SQL で同時実行)
│  │  └─ rrf_fuse   (SQL latency, fused_candidates 件数)
│  └─ rerank        (voyage rerank: tokens/cost/latency)
└─ generate         (claude: tokens/cost)
```

> **実装上の補足**: ベクトル検索と全文検索は 1 つの CTE SQL で同時に実行されるため、
> `vector_search` / `fts_search` を独立したスパンに分離することは不可。
> `hybrid_search` スパンの中で `rrf_fuse` 子スパンが SQL 実行全体をラップし、
> 融合後の候補件数をメタデータに記録する。

---

## 5. Eval 定義（before/after・M1）

- **ゴールデンデータ**: M0 と同じ `evals/golden/m0.yaml`（10問）。**30〜50問への拡充は M3**
- **指標**（すべて二値関連度: チャンクが期待ソース由来か否か）:
  - **Recall@5 / Recall@10** — top-k に期待ソース由来チャンクが1つ以上入った質問の割合
  - **nDCG@10** — 期待ソース由来チャンクの順位品質（リランクの効果が出る指標）
  - **MRR** — 最初に期待ソース由来チャンクが現れた順位の逆数の平均
- **モード横断**: `vector` / `hybrid` / `hybrid_rerank` の3モードで全指標を算出
- **出力**: 下記の比較表を標準出力 + `evals/results/m1_<timestamp>.json` に保存
- **記録**: この before/after を **PR に貼る**（AGENTS §9）

### 出力イメージ（比較表）

| mode | Recall@5 | Recall@10 | nDCG@10 | MRR |
|---|---|---|---|---|
| vector (M0 baseline) | … | … | … | … |
| hybrid | … | … | … | … |
| hybrid_rerank | … | … | … | … |

> 期待: 語彙一致で hybrid が Recall を、rerank が nDCG@10 / MRR（順位品質）を押し上げる。この差分がショーケースの見せ場。

---

## 6. タスク分解（実装順）

| ID | タスク | 完了条件 |
|---|---|---|
| **M1-1** | DB: Alembic `0002` で `chunks_content_bigm`（GIN, `gin_bigm_ops`）追加 | `make migrate` が通り索引が作られる |
| **M1-2** | 全文検索: pg_bigm クエリ（候補 `CANDIDATE_K`） | 与えた語彙で関連チャンクが返る（統合テスト） |
| **M1-3** | 融合: RRF SQL + `RetrievalStrategy`（vector/hybrid） | hybrid モードで融合結果が返る |
| **M1-4** | リランク: Voyage rerank-2.5 連携 + `hybrid_rerank` モード（top_k=8） | rerank 後の順序で top_k が返る（rerank はモック可のテスト） |
| **M1-5** | 生成配線: top_k=8 を context に。**API 契約は不変**を担保 | S3（M0 シナリオ）が引き続き成立 |
| **M1-6** | 可観測性: `vector_search`/`fts_search`/`rrf_fuse`/`rerank` スパン + rerank コスト | S4 が成立 |
| **M1-7** | Eval: 3モード横断で Recall@5/@10・nDCG@10・MRR、比較表出力 + 保存 | S5 が成立、before/after 記録 |
| **M1-8** | テスト + ドキュメント更新（結果表を PR/README に反映） | `make lint`/`make test` が通る |

---

## 7. Definition of Done（M1）

- [ ] `0002` マイグレーションが適用され pg_bigm GIN 索引が存在する
- [ ] `hybrid_rerank` で融合 + リランク後の top_k が生成に渡る（S1）
- [ ] 片側検索が0件でも例外なく回答する（S2）
- [ ] `POST /api/chat` の応答形式が M0 と同一（後方互換, S3）
- [ ] Langfuse に拡張スパン + rerank コストが出る（S4）
- [ ] `make eval` が3モードの比較表（Recall@5/@10・nDCG@10・MRR）を出力・保存する（S5）
- [ ] PR に before/after の比較表を記載した（AGENTS §9）
- [ ] `make lint` / `make test` が通る（RRF 融合ロジックの単体・全文検索SQL統合・rerank モックをカバー）
- [ ] 依存方向ルール（AGENTS §3）を守っている（全文検索・rerank は `retrieval/` に閉じる）

---

## 8. オープンな論点

- **pg_bigm の類似度しきい値**: 既定の0.3でM1実装を完了し、Eval等の結果を踏まえて今後調整を行うこととした。
- **rerank 投入数（FUSE_K=40）のコスト/精度トレード**: 現在は40で実装を完了。
- **候補数 CANDIDATE_K=50 の妥当性**: 現在は50で実装を完了。

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-07 | 初版（M1 スペック・ドラフト） |