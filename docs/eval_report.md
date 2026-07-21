# Eval レポート

> 本ページは M5 ショーケース仕上げの一部として、実際の `make eval` 実行結果をもとに作成しています（`docs/specs/26070922-m5_release_readiness/spec.md` §5）。
> 数値はすべて **M3 Eval ハーネス（`backend/src/private_rag_apps/evals/`）が生成する machine-readable JSON / human-readable Markdown サマリを一次ソースとして引用**しており、手書きの数値・捏造は一切行っていません（M5スペック§5.3）。一次ソースは `backend/evals/reports/latest_summary.md`（毎回上書き）と `backend/evals/baselines/current.json`（committed baseline）です。

## 1. 狙い（測定指標）

- **Retrieval**: Recall@5 / Recall@10 / nDCG@10 / MRR（RRF融合直後＝fused と、リランク後＝reranked の両方を計測し、リランクの寄与を可視化する）
- **Generation**: Faithfulness（コンテキストに忠実か）/ Answer Relevance（質問に対して的確か）を LLM-as-judge で採点（NFR-1）
- 検索指標はハードゲート（許容誤差 0.05 超の低下で FAIL）、生成指標はソフトゲート（許容誤差 0.1 超の低下で WARN）で回帰を検出する（`docs/specs/26070805-m3_eval_expansion/spec.md` §7）

## 2. データセット

- `backend/evals/dataset/m3_golden.jsonl`、**31問**（spec の目安 30〜50問の範囲内）
- 内訳（`tags` フィールドによる分類）: **lookup 24問** / **synthesis 4問** / **negative 3問**
- 正解ラベルは **path レベル**（`relevant: [{"path": "..."}]`）。チャンク単位ではなく文書単位の正解集合で Recall/nDCG/MRR を計算する（M3 §3.3）

## 3. スコア推移（中核）

`make eval` は評価時、常に `hybrid_rerank` 戦略（ハイブリッド検索 → リランク）を強制実行し、リランク前（fused）とリランク後（reranked）の両方を診断モードで記録する。

| Metric | RRF Fused（ハイブリッド直後） | Reranked（リランク後） |
|---|---|---|
| Recall@5 | 0.903 | 0.935 |
| Recall@10 | 0.968 | 0.968 |
| nDCG@10 | 0.710 | 0.742 |
| MRR | 0.627 | 0.666 |

リランクにより nDCG@10 が 0.710 → 0.742、MRR が 0.627 → 0.666 に改善しており、上位順位の質を押し上げる効果が出ている。Recall@10 は fused の時点で既に 0.968 と高く、リランクは主に**順位の並べ替え**（上位に正解を寄せる）に寄与している。

## 4. 生成品質

- **Faithfulness**: 0.935
- **Answer Relevance**: 0.774
- **negative ケースの棄権成功率**: `tags` に `negative` を含む3問（q13, q15, q30）はすべて `faithfulness=1.0` / `answer_relevance=1.0`（**3/3 = 100%**）と judge に採点されており、コンテキストに答えが無い場合に正しく「見つからない」旨を返せていることを確認した（AGENTS.md §7 の RAG 誠実性ルールに対応）。集計値（`aggregate.generation`）にはこの内訳が個別に出力されないため、`backend/evals/baselines/current.json` の `results` 配列を該当 id でフィルタして手動集計した。

## 5. Provenance

| 項目 | 値 |
|---|---|
| 実行日時 | 2026-07-13T08:49:51 UTC |
| Dataset Version | m3_golden |
| Corpus Hash | `1779a40e82adce0e51c95b352f689a5d6ec95445c6374924b42921b7a72a7e2d` |
| LLM（生成・judge） | gpt-5.4-nano |
| Embed | voyage-4（1024次元） |
| Rerank | rerank-2.5 |
| EVAL_TOP_K / EVAL_EF_SEARCH | 12 / 100 |
| 結果 | **PASSED**（全指標が許容誤差内） |

**既知の注意点**: `EVAL_GEN_TEMPERATURE` / `EVAL_GEN_MAX_TOKENS` は `core/config.py` に設定項目として存在するが、`evals/__main__.py` の `get_answer()` は現状の `generate_answer_stream()` をそのまま呼んでおり、これらの値を生成呼び出しに反映していない（コード中コメントで明記）。したがって本レポートの生成品質スコアは、eval 専用の decoding 設定ではなく、通常のチャット生成と同じ設定で得られたものである。

## 6. 限界と今後

- **judge のノイズ**: `EVAL_JUDGE_SAMPLES=1`（既定値）のため、judge 1回のみのスコアであり、複数サンプルの平均化は行っていない。
- **データセット規模**: 31問はショーケースとして妥当な規模だが、統計的に厳密な回帰検出には心もとない。特に synthesis（4問）・negative（3問）は少数のため、個別問題での外れ値の影響を受けやすい。
- **マルチターン評価は sanity check 止まり**: `turns` を持つ設問はクエリ書き換え（condense）を経由するが、マルチターン特有の失敗モード（誤った文脈継承等）を網羅的に検証する設計にはなっていない。
- **M0（ベクトルのみ）ベースラインが無い**: 本レポートは spec §5.2 が理想とする「M0 ベクトルのみ → M1 ハイブリッド → M1 +リランク」の3段階比較を意図していたが、`evals/__main__.py` は評価時に常に `strategy="hybrid_rerank"` を強制しており、**ベクトル単独の指標をこの評価パイプラインでは計測していない**。今回のレポートは「ハイブリッド直後（fused）」対「リランク後（reranked)」の2段階比較にとどめており、ベクトル単独との比較が必要な場合は M3 側の課題として別途対応する（数値を推測・捏造していない）。
- **再現性**: 同一コーパス・同一設定で再実行すれば、LLM/judge の非決定性を除き同等のスコアが得られる想定（`corpus_hash` で入力コーパスの同一性を検証可能）。

---

関連: [docs/decisions.md](decisions.md) / [docs/specs/26070805-m3_eval_expansion/spec.md](docs/specs/26070805-m3_eval_expansion/spec.md) / [docs/specs/26070922-m5_release_readiness/spec.md](docs/specs/26070922-m5_release_readiness/spec.md) / 生データ: `backend/evals/reports/latest_summary.md`（都度生成・gitignore対象）/ `backend/evals/baselines/current.json`（committed baseline）
