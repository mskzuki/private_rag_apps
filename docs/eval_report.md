# Eval レポート

> **作成中**: このファイルは M5 Phase 3（`docs/specs/m5_showcase_finishing.md` §5）で、実際の `make eval` 実行結果をもとに完成させます。
> 数値は **M3 Eval ハーネス（`backend/src/private_rag_apps/evals/`）が生成する machine-readable JSON / human-readable Markdown サマリを一次ソースとして引用**し、手書きの数値・捏造は一切行いません（M5スペック§5.3）。
> 現時点では Docker（PostgreSQL + pgvector + pg_bigm）と有効な `OPENAI_API_KEY`/`VOYAGE_API_KEY`・モデル設定が揃っていないため、実行がブロックされています。

## 掲載予定の構成

1. **狙い** — 測定指標（Retrieval: Recall@5/10・nDCG@10・MRR / Generation: Faithfulness・Answer Relevance）
2. **データセット** — `backend/evals/dataset/m3_golden.jsonl`（31問。lookup/synthesis/negative の内訳、pathレベル正解）
3. **スコア推移（中核）** — RRF融合直後（fused）とリランク後（reranked）の比較表。リランクの寄与を可視化
4. **生成品質** — Faithfulness / Answer Relevance と negative ケースの棄権成功率
5. **provenance** — 使用モデル名（LLM/埋め込み/リランク/judge）・検索パラメータ・corpus ハッシュ・dataset version・実行日時
6. **限界と今後** — judge のノイズ、データセット規模、マルチターン評価が sanity check 止まりであること

関連: [docs/decisions.md](decisions.md) / [docs/specs/m3_eval_expansion.md](specs/m3_eval_expansion.md) / [docs/specs/m5_showcase_finishing.md](specs/m5_showcase_finishing.md)
