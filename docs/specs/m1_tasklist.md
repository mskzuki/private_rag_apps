# M1 ハイブリッド検索 + リランク タスクリスト

`docs/specs/m1_hybrid_search.md` に基づくタスク一覧です。
前提: **M0（Walking Skeleton）完了済み**。
各機能タスクには**対応するテストを同時に**含めます（AGENTS §8）。LLM・埋め込み・rerank 呼び出しはテストでモックします。
**API 契約（`POST /api/chat` の `{content, citations}`）は M1 を通して不変**です。変更は検索段の内部に閉じます。

- `[ ]` **M1-1 データベース・マイグレーション**
  - `[ ]` Alembic `0002` の作成: `chunks.content` への pg_bigm GIN 索引 `chunks_content_bigm`（`gin_bigm_ops`）
  - ※ pg_bigm 拡張自体は M0 の `0001_init` で作成済み。ここは索引のみ
  - *完了条件*: `make migrate` が成功し、GIN 索引が存在すること。
  - *テスト*: マイグレーションの upgrade/downgrade が通ること。

- `[ ]` **M1-2 全文検索 (pg_bigm)**
  - `[ ]` pg_bigm 検索クエリの実装（`bigm_similarity` 降順、候補 `CANDIDATE_K=50`、`sources.deleted_at IS NULL` で除外）
  - `[ ]` `=%` の類似度しきい値（`pg_bigm.similarity_limit` 既定0.3）の挙動確認 — 短いクエリで候補が痩せる場合の扱いをスペック §8 の論点に沿って計測・決定し、結果をスペックに追記
  - *完了条件*: 固有語を含むクエリで該当チャンクが返ること（統合テスト）。
  - *テスト*: 全文検索 SQL の統合テスト（テスト用 DB に日本語チャンク投入 → 語彙一致で期待チャンクが返る / 0件クエリでも例外にならない）。

- `[ ]` **M1-3 RRF 融合 + RetrievalStrategy**
  - `[ ]` RRF 融合 SQL の実装（ベクトル CTE + 全文 CTE → `1/(RRF_K + rank)` 合算 → 上位 `FUSE_K=40`。db_design §6 準拠）
  - `[ ]` `RetrievalStrategy` の導入（`vector` / `hybrid`。設定 `RETRIEVAL_STRATEGY` で切替、生成層はインターフェース不変）
  - `[ ]` 片側 0 件時のフォールバック（どちらかの CTE が空でも `UNION ALL` で成立することの担保）
  - `[ ]` 検索パラメータの設定化（`CANDIDATE_K` / `RRF_K` / `FUSE_K` を `core/config.py` へ）
  - *完了条件*: `hybrid` モードで融合結果が返ること。片側 0 件でも回答できること（S2 成立）。
  - *テスト*: RRF スコア計算の単体テスト（順位→スコア→合算の検証。SQL を通さない純粋ロジック分離を推奨）+ 融合 SQL の統合テスト（片側空のケース含む）。

- `[ ]` **M1-4 リランク (Voyage rerank-2.5)**
  - `[ ]` rerank クライアントの実装（`retrieval/` に閉じる。AGENTS §3 依存方向）
  - `[ ]` `hybrid_rerank` モードの追加（融合 `FUSE_K=40` 件 → rerank → 最終 `RERANK_TOP_K=8`）
  - `[ ]` 候補が 8 件未満の場合はある分だけ採用（下限で落とさない）
  - `[ ]` rerank API 失敗時のフォールバック（リトライ → 失敗時は融合順のまま top_k を採用し、劣化を Langfuse に記録）
  - *完了条件*: rerank 後の順序で top_k が返ること（rerank はモックのテストで検証）。
  - *テスト*: rerank 結果の並べ替え・件数不足・失敗フォールバックの単体テスト（rerank API はモック）。

- `[ ]` **M1-5 生成への配線（API 契約不変）**
  - `[ ]` 生成 context を top-5（M0）→ `RERANK_TOP_K=8` に変更（出典番号 `[1..8]` の対応付け）
  - `[ ]` 既定戦略を `hybrid_rerank` に設定
  - `[ ]` M0 の受け入れシナリオ（コーパス内 S2 / コーパス外 S3）の回帰確認
  - *完了条件*: レスポンス形式が M0 と同一で後方互換が保たれていること（S1, S3 成立）。
  - *テスト*: API の回帰テスト（M0 のテストがそのまま通ること。増えたのは citations の最大件数のみ）。

- `[ ]` **M1-6 可観測性（スパン拡張）**
  - `[ ]` `retrieve` スパンを親に、子スパン `vector_search` / `fts_search` / `rrf_fuse` / `rerank` を追加
  - `[ ]` rerank 呼び出しのトークン・コスト・レイテンシの記録
  - *完了条件*: 1 回のチャットで拡張スパンと rerank コストが Langfuse に出ること（S4 成立）。

- `[ ]` **M1-7 Eval（before/after ★M1 の核）**
  - `[ ]` Eval ハーネスの3モード対応（`vector` / `hybrid` / `hybrid_rerank` を横断実行）
  - `[ ]` 指標の追加実装: Recall@10 / nDCG@10 / MRR（Recall@5 は M0 実装を流用）
  - `[ ]` 比較表の出力（標準出力）と `evals/results/m1_<timestamp>.json` への保存
  - `[ ]` ゴールデンは M0 の `evals/golden/m0.yaml`（10問）を流用（拡充は M3）
  - *完了条件*: `make eval` が3モード×4指標の比較表を出力・保存すること（S5 成立）。
  - *テスト*: nDCG / MRR 計算の単体テスト（既知の順位列に対する期待値検証）。

- `[ ]` **M1-8 仕上げ**
  - `[ ]` `make lint` / `make test` の全通過
  - `[ ]` PR に Eval before/after 比較表を記載（AGENTS §9）
  - `[ ]` 計測結果に基づく §8 オープン論点（similarity_limit / FUSE_K / CANDIDATE_K）の判断をスペックに反映
  - `[ ]` README への結果表の反映（Definition of Success の布石）
  - *完了条件*: `make lint` / `make test` が通り、before/after が記録・共有されていること。

---

## 実装上の注意（スペックからの再掲）

- **依存方向**: 全文検索・RRF・rerank はすべて `retrieval/` に閉じる。`generation/` は戦略切替を意識しない。
- **不変条件**: DB スキーマ変更は `0002` の索引追加のみ。生成プロンプト・非ストリーム挙動は M0 のまま。
- **Eval の意味**: この before/after 表（vector → hybrid → hybrid_rerank）がプロジェクト最大のショーケース成果物。数値が期待通りでなくても、その分析と対応（しきい値調整等）を記録することに価値がある。