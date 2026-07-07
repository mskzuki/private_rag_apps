# M3 タスクリスト (m3_tasklist.md)

> 配置先: `docs/specs/m3_tasklist.md`
> 対応スペック: `docs/specs/m3_eval_expansion.md`（v0.2、以下「スペック」）
> 進め方: 上から順に実施する。各タスクは対応するスペックの節番号を付記。
> 指標の計算ロジックは**決定的ユニットテストを先に**書く（TDD 寄り）。外部 API を叩く箇所はテストではモック/記録再生（AGENTS.md §8）。

---

## Phase 0 — 準備・方針確定（スペック §13 未決事項）

- [ ] 生成指標のスコア尺度を決定（連続値 0–1 / 離散等級のいずれか）
- [ ] Langfuse Datasets/Experiments 連携を M3 で実配線するか、フックのみ用意して後続にするか決定（§6.4）
- [ ] graded relevance（`grade`）を付与するか、binary 開始とするか決定（§4.1）
- [ ] tolerance は「初回ベースライン取得後（Phase 5）に確定」する運用を確認（ここでは値を決めない）
- [ ] 決定事項をスペックに反映（differ があれば先にスペック更新。AGENTS.md §12）

---

## Phase 1 — ゴールデンデータセット拡充（スペック §3）

- [ ] データセット JSONL スキーマを確定（`id` / `question` / `relevant[{path, heading?, grade?}]` / `reference_answer` / `tags` / `expect_no_answer` / `turns?`）
- [ ] 30〜50 問を seed コーパス由来で作成（**実データを含めない**。NFR-3）
- [ ] **negative（`expect_no_answer: true`）ケースを必ず含める**（§3.1）
- [ ] 正解ラベルを **path（+任意 heading）レベル**で付与（**chunk_id を使わない**。§3.3）
- [ ] タグ（lookup / synthesis / negative）を付与し、種別の分布を確認
- [ ] データセットに `version` を付与し `evals/dataset/` に Git 管理で配置
- [ ] スキーマ検証スクリプト（必須フィールド・**path 実在チェック**・grade 範囲）を用意
- [ ] seed 変更時に path 実在チェックを通す運用をドキュメント化（§3.4）
- [ ] ユニットテスト: スキーマ検証（不正フィールド・存在しない path を弾く）

---

## Phase 2 — 検索指標の実装（スペック §4）★指標の正しさが核

> `@k` は**取得チャンクリスト基準**。正解は path 写像で hit 判定。doc-dedup は得点順位の決定のみに使う（§4.1）。

- [ ] `source_id → sources.path` 写像を用いた hit 判定ユーティリティを実装
- [ ] **doc-dedup ロジック**を実装（同一正解文書は最上位チャンク位置のみ得点、下位は rel=0）
- [ ] Recall@5 / Recall@10 を実装（top-k チャンク内に入った正解文書数 / 正解文書総数）
- [ ] nDCG@10 を実装（DCG は doc-dedup 済み rel、**IDCG は doc-dedup 理想順序**で算出）
- [ ] MRR（@`EVAL_TOP_K`）を実装（打ち切り内で最初の正解チャンク順位の逆数）
- [ ] **`retrieval` に評価/診断モードを追加**し、`{fused_ranking, reranked_ranking}` の両方を返せるようにする（evals は再実装しない。AGENTS.md §3。§4.2）
- [ ] リランク前（融合直後）/後の両方で指標を算出できるようにする
- [ ] `EVAL_TOP_K`（既定 12）・`EVAL_EF_SEARCH`（全探索寄り）を `core/config.py` に追加（§10）
- [ ] ユニットテスト: **既知入力**（合成チャンク順位 + 所属 path + 正解 path 集合）で Recall/nDCG/MRR を検算
- [ ] ユニットテスト: **`@k` がチャンク基準**であること
- [ ] ユニットテスト: **同一正解文書の複数チャンクが二重計上されない**こと（doc-dedup）
- [ ] ユニットテスト: **IDCG が doc-dedup 理想順序**で作られること／タイブレーク

---

## Phase 3 — 生成指標（LLM-as-judge）（スペック §5）

- [ ] Faithfulness / Answer Relevance の判定プロンプトを `prompts/` に追加（ハードコード禁止。AGENTS.md §6/§11）
- [ ] judge 呼び出しを `evals/` に実装（LLM は generation・evals のみ。AGENTS.md §3）
- [ ] 構造化出力（`{score, rationale}`）の生成強制とパーサ実装（不正 JSON のハンドリング含む）
- [ ] `JUDGE_MODEL` / `JUDGE_TEMPERATURE(=0)` / `EVAL_JUDGE_SAMPLES(=1)` を `core/config.py` に追加し、**judge モデル名を記録**
- [ ] Faithfulness の入力を「問い / 回答 / 取得コンテキスト」に限定（reference_answer は使わない。§5.2）
- [ ] Answer Relevance の入力を「問い / 回答 /（任意）reference_answer」に
- [ ] **negative ケースの棄権（abstain）判定**を実装（弱いコンテキストで棄権できたか。でっち上げは Faithfulness 最低。§5.2）
- [ ] `EVAL_JUDGE_SAMPLES > 1` 時の複数サンプル平均を実装
- [ ] ユニットテスト: judge 出力パーサ（正常/不正 JSON）。judge 呼び出しはモック

---

## Phase 4 — ハーネス統合とレポート出力（スペック §6）

- [ ] `make eval` を拡張し、データセット→検索→指標→生成→judge→集計→レポートを通しで実行
- [ ] **被評価側の生成を eval 時 temp=0・max_tokens 固定**で走らせる（`EVAL_GEN_TEMPERATURE` / `EVAL_GEN_MAX_TOKENS`。§5.2/§7.4）
- [ ] 機械可読レポート `evals/reports/<timestamp>.json`（各問スコア + 集計 + メタ）を出力
- [ ] 人間可読サマリ Markdown（集計表・**リランク前/後比較**・negative 成否）を出力
- [ ] **provenance を記録**（埋め込み/次元・rerank・生成・judge モデル名・**生成/judge の temp・max_tokens**・検索パラメータ・**corpus ハッシュ**・**dataset version**・日時。§6.2）
- [ ] Langfuse への eval 実行コスト記録を確認（NFR-5。§9）
- [ ] スモークテスト: 2〜3 問の極小データセットで end-to-end（外部呼び出しはモック）

---

## Phase 5 — ベースライン確立と回帰検出（スペック §6.3, §7.3）

- [ ] 現行構成で `make eval` を実行し、**committed baseline** `evals/baselines/current.json` を確立
- [ ] メトリクス別 tolerance（`EVAL_TOLERANCE_*`）の初期値を、実測のブレ幅を見て設定
- [ ] baseline 比較ロジック（tolerance 超の低下を回帰として検出）を実装
- [ ] **検索指標=ハードゲート / 生成指標=ソフト**の判定方針を実装（§7.3）
- [ ] baseline 更新は意図的な PR でのみ行う運用をドキュメント化（§6.3）
- [ ] ユニットテスト: baseline 比較の tolerance 境界（fail/warn/pass の分岐）

---

## Phase 6 — CI ワークフロー（スペック §7）

- [ ] トリガ設定（`prompts/` `retrieval/` `ingestion/` `generation/` `evals/` 変更 PR で実行。§7.1）
- [ ] CI ジョブを **再現経路**で組む: DB 起動 → `make migrate` → **`make ingest`(seed)** → `make eval`（§7.2 / NFR-8）
- [ ] API キーを CI シークレットに設定（`ANTHROPIC_API_KEY` / `VOYAGE_API_KEY`。Langfuse は任意）
- [ ] 検索ハードゲート / 生成ソフトの CI 判定を配線
- [ ] **before/after を PR に自動記載**（コメント or artifact。AGENTS.md §9）
- [ ] 評価時 `ef_search` 全探索寄り固定で、索引再構築由来の recall 揺れが偽陽性 fail にならないことを確認（§4.2/§7.4）

---

## Phase 7 — Langfuse Datasets/Experiments 連携（任意・スペック §6.4）

> Phase 0 で「実配線する」と決めた場合のみ。CI ゲートの正は committed baseline（連携は best-effort）。

- [ ] ゴールデンデータを Langfuse Dataset として登録
- [ ] `make eval` の各実行を Experiment（run）として記録
- [ ] Langfuse 障害時も CI/eval が落ちないこと（best-effort）を確認

---

## Phase 8 — マルチターン小規模サニティ（スペック §8）

- [ ] `turns` を持つマルチターン項目を数問追加
- [ ] condense 経由でフォローアップが自己完結クエリ化され正解文書を引けるかを sanity check
- [ ] **非ゲート**（本格的な会話評価は後続）であることを確認

---

## Phase 9 — 仕上げ・受け入れ確認（スペック §11, §13）

- [ ] スペック §11 の受け入れ条件をすべてチェック（データセット/検索指標/生成指標/ハーネス/CI/共通）
- [ ] **M1 前後を含むスコア推移の Eval レポートを公開**（`docs/eval_report.md`。§12 Definition of Success）
- [ ] `requirements.md` §9/§NFR-1 を更新（path レベル正解・`EVAL_TOP_K` 分離・ゲート方針）
- [ ] `requirements.md` §12 に Eval レポート公開の具体パスを明記
- [ ] `architecture.md` に評価フロー・judge の依存・**`retrieval` 診断モード**を追記（§4.2/§13）
- [ ] `AGENTS.md` §7/§9 に CI 実行経路・ゲート方針を反映
- [ ] `make lint` / `make test` が通ることを最終確認
- [ ] 依存方向（LLM は generation・evals のみ）を最終確認

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-07 | 初版。m3_eval_expansion.md v0.2 の §14 実装順序に基づき Phase 0〜9 を作成。指標は決定的ユニットテスト先行、`@k` チャンク基準・doc-dedup・IDCG 基準、`retrieval` 診断モード、生成 decoding 固定、ef_search 全探索寄り、検索ハード/生成ソフトのゲート、再現経路 CI を各 Phase に反映 |