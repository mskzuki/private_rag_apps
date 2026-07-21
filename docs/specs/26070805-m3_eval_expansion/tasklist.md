# M3 タスクリスト (docs/specs/26070805-m3_eval_expansion/tasklist.md)

> 配置先: `docs/specs/26070805-m3_eval_expansion/tasklist.md`
> 対応スペック: `docs/specs/26070805-m3_eval_expansion/spec.md`（v0.3、以下「スペック」）
> 進め方: 上から順に実施する。各タスクは対応するスペックの節番号を付記。
> 指標の計算ロジックは**決定的ユニットテストを先に**書く（TDD 寄り）。外部 API を叩く箇所はテストではモック/記録再生（AGENTS.md §8）。

> **M5 監査メモ（2026-07-13）**: 本ファイルのチェックは実装済みコードとの一括照合（bulk verification）で行った。`docs/specs/26070805-m3_eval_expansion/spec.md` §11 受け入れ条件の検証結果（file:line 単位の根拠）をこのタスクリストの該当項目に敷衍する形でチェックを付けている。テスト未整備・設定はあるが未配線・`docs/eval_report.md` 未生成などが判明した項目は未チェックのまま `genuine gap` として明記した。

> **2026-07-16 追加タスク**: Voyage 無支払い枠（3 RPM）でも日常的な Eval 反復を可能にするため、M3 スペック v0.3 の検索結果キャッシュを実装する。検索品質を変更する作業では `--no-cache` による再取得を必須とする。

---

## Phase 0 — 準備・方針確定（スペック §13 未決事項）

- [x] 生成指標のスコア尺度を決定（連続値 0–1 / 離散等級のいずれか）— スペック §13「決定事項」に **Binary** と明記済み。judge プロンプト（`prompts/judge.py`）も `score: 1または0` を強制
- [x] Langfuse Datasets/Experiments 連携を M3 で実配線するか、フックのみ用意して後続にするか決定（§6.4）— §13「決定事項」で**フックのみ**と決定。`evals/__main__.py:235-237` にコメントアウトの `[HOOK]` として実装済み（Phase 7 は意図的に未実装）
- [x] graded relevance（`grade`）を付与するか、binary 開始とするか決定（§4.1）— §13「決定事項」で **binary 開始**と決定。データセットの `relevant` は `grade` を省略（既定 1）で統一
- [x] tolerance は「初回ベースライン取得後（Phase 5）に確定」する運用を確認（ここでは値を決めない）— 運用方針としては踏襲されたが、実際には `EVAL_TOLERANCE_*` 設定を経由せず `evals/__main__.py` に `0.05`/`0.1` がハードコードされている（Phase 5 に注記）
- [x] 決定事項をスペックに反映（differ があれば先にスペック更新。AGENTS.md §12）— `docs/specs/26070805-m3_eval_expansion/spec.md` §13 に「決定事項」ブロックとして反映済み（M2 の §12 未決事項が未反映のままなのとは対照的）

---

## Phase 1 — ゴールデンデータセット拡充（スペック §3）

- [x] データセット JSONL スキーマを確定（`id` / `question` / `relevant[{path, heading?, grade?}]` / `reference_answer` / `tags` / `expect_no_answer` / `turns?`）— `evals/schema.py:8-21` `RelevantDoc`/`DatasetItem`
- [x] 30〜50 問を seed コーパス由来で作成（**実データを含めない**。NFR-3）— `backend/evals/dataset/m3_golden.jsonl`（31問）。設計文書（`db_design.md` 等）由来のみで個人実データなし
- [x] **negative（`expect_no_answer: true`）ケースを必ず含める**（§3.1）— 3問（`tags: ["negative"]`）
- [x] 正解ラベルを **path（+任意 heading）レベル**で付与（**chunk_id を使わない**。§3.3）— `RelevantDoc` に `chunk_id` フィールドなし
- [x] タグ（lookup / synthesis / negative）を付与し、種別の分布を確認 — lookup 24 / synthesis 4 / negative 3（grep で確認）
- [ ] データセットに `version` を付与し `evals/dataset/` に Git 管理で配置 — 配置場所は正しいが、**`version` フィールド自体はデータセットファイル内に存在しない**（ファイル名 `m3_golden.jsonl` とハーネス側の `provenance["dataset_version"]="m3_golden"`（`__main__.py:138`）のハードコード文字列で代替されているのみ）。厳密には未達
- [x] スキーマ検証スクリプト（必須フィールド・**path 実在チェック**・grade 範囲）を用意 — `evals/schema.py:24-52` `load_dataset()`（pydantic必須フィールド検証）/`validate_paths()`（path実在）。※ `grade` の値域チェック（範囲外を弾く仕組み）は無い（binary運用のため優先度低）
- [x] seed 変更時に path 実在チェックを通す運用をドキュメント化（§3.4）— `docs/specs/26070805-m3_eval_expansion/spec.md:102` に明記
- [x] ユニットテスト: スキーマ検証（不正フィールド・存在しない path を弾く）— `tests/evals/test_schema.py::test_invalid_dataset`, `test_validate_paths`

---

## Phase 2 — 検索指標の実装（スペック §4）★指標の正しさが核

> `@k` は**取得チャンクリスト基準**。正解は path 写像で hit 判定。doc-dedup は得点順位の決定のみに使う（§4.1）。

- [x] `source_id → sources.path` 写像を用いた hit 判定ユーティリティを実装 — 写像自体は `retrieval` 側で解決済みの `path` を chunk dict に含めて返す設計。`evals/metrics.py:24-30` が `chunk.get("path")` で判定
- [x] **doc-dedup ロジック**を実装（同一正解文書は最上位チャンク位置のみ得点、下位は rel=0）— `evals/metrics.py:21-30`
- [x] Recall@5 / Recall@10 を実装（top-k チャンク内に入った正解文書数 / 正解文書総数）— `evals/metrics.py:37-38,53-54`
- [x] nDCG@10 を実装（DCG は doc-dedup 済み rel、**IDCG は doc-dedup 理想順序**で算出）— `evals/metrics.py:32-34,40-44,55`
- [x] MRR（@`EVAL_TOP_K`）を実装（打ち切り内で最初の正解チャンク順位の逆数）— `evals/metrics.py:46-50,56`
- [x] **`retrieval` に評価/診断モードを追加**し、`{fused_ranking, reranked_ranking}` の両方を返せるようにする（evals は再実装しない。AGENTS.md §3。§4.2）— `retrieval/searcher.py` の `retrieve_context(..., diagnostic_mode=True)`
- [x] リランク前（融合直後）/後の両方で指標を算出できるようにする — `evals/__main__.py:82-86`（`fused_metrics`/`reranked_metrics` を両方算出）
- [x] `EVAL_TOP_K`（既定 12）・`EVAL_EF_SEARCH`（全探索寄り）を `core/config.py` に追加（§10）— `core/config.py:36-37`
- [x] ユニットテスト: **既知入力**（合成チャンク順位 + 所属 path + 正解 path 集合）で Recall/nDCG/MRR を検算 — `tests/evals/test_metrics.py::test_evaluate_retrieval_doc_dedup`（DCG/IDCG/MRR を手計算した期待値と比較）
- [x] ユニットテスト: **`@k` がチャンク基準**であること — 同テストで chunk 単位のリストに対し `recall_5`/`ndcg_10` を検証（doc 数ではなく chunk 位置基準で判定）
- [x] ユニットテスト: **同一正解文書の複数チャンクが二重計上されない**こと（doc-dedup）— 同テスト（`# Ignored (doc-dedup)` のケース）
- [ ] ユニットテスト: **IDCG が doc-dedup 理想順序**で作られること／タイブレーク — IDCG 自体の正しさは `test_evaluate_retrieval_doc_dedup` で検証されているが、**タイブレーク（同一 grade 複数正解のケース）専用のテストは無い**

---

## Phase 3 — 生成指標（LLM-as-judge）（スペック §5）

- [x] Faithfulness / Answer Relevance の判定プロンプトを `prompts/` に追加（ハードコード禁止。AGENTS.md §6/§11）— `prompts/judge.py`
- [x] judge 呼び出しを `evals/` に実装（LLM は generation・evals のみ。AGENTS.md §3）— `evals/judge.py`
- [x] 構造化出力（`{score, rationale}`）の生成強制とパーサ実装（不正 JSON のハンドリング含む）— `evals/judge.py:8-42` `_call_judge()`
- [x] `JUDGE_MODEL` / `JUDGE_TEMPERATURE(=0)` / `EVAL_JUDGE_SAMPLES(=1)` を `core/config.py` に追加し、**judge モデル名を記録**— `core/config.py:32-38`、`__main__.py:153` `provenance["models"]["judge"]`
- [x] Faithfulness の入力を「問い / 回答 / 取得コンテキスト」に限定（reference_answer は使わない。§5.2）— `evals/judge.py:45-48`。※ `question` 引数は関数シグネチャにあるが `JUDGE_FAITHFULNESS_PROMPT` のプレースホルダは `{context}`/`{answer}` のみで、実際には質問文をプロンプトに埋め込んでいない（軽微な乖離）
- [x] Answer Relevance の入力を「問い / 回答 /（任意）reference_answer」に — `evals/judge.py:50-53`、`JUDGE_ANSWER_RELEVANCE_PROMPT` が `{question}`/`{reference_answer}`/`{answer}` を使用
- [x] **negative ケースの棄権（abstain）判定**を実装（弱いコンテキストで棄権できたか。でっち上げは Faithfulness 最低。§5.2）— 専用コードパスではなく `prompts/judge.py:8,31` のプロンプト指示（「見つからない」を正答として1点、誤った断定は0点）で実現
- [ ] `EVAL_JUDGE_SAMPLES > 1` 時の複数サンプル平均を実装 — **genuine gap**。`core/config.py:38` に設定はあるが、`judge.py`/`__main__.py` のどこからも参照されておらず未配線（grep で使用箇所ゼロ）
- [x] ユニットテスト: judge 出力パーサ（正常/不正 JSON）。judge 呼び出しはモック — `tests/evals/test_judge.py`（valid/markdown-wrapped/invalid JSON、faithfulness/answer_relevance のモックテスト）

---

## Phase 4 — ハーネス統合とレポート出力（スペック §6）

- [x] `make eval` を拡張し、データセット→検索→指標→生成→judge→集計→レポートを通しで実行 — `evals/__main__.py:42-243` `run_eval()`
- [x] 検索結果キャッシュを実装: `make eval` は既定で再生、`make eval ARGS="--no-cache"` / `make eval-no-cache` は Voyage を呼んで成功時に更新。dataset・corpus・モデル・検索設定の provenance を検証し、不一致時は明示エラー（§6.1）— `evals/retrieval_cache.py` / `evals/__main__.py` / `core/config.py`
- [x] ユニットテスト: キャッシュの有効性判定、キャッシュ再生時に retrieval を呼ばないこと、`--no-cache` 時の更新（外部 API はモック）— `tests/evals/test_retrieval_cache.py`
- [ ] **被評価側の生成を eval 時 temp=0・max_tokens 固定**で走らせる（`EVAL_GEN_TEMPERATURE` / `EVAL_GEN_MAX_TOKENS`。§5.2/§7.4）— **genuine gap**。設定は `core/config.py` にあるが `evals/__main__.py:28-34` の `get_answer()` のコメントで明言されている通り `generate_answer_stream()` に反映されておらず未使用
- [x] 機械可読レポート `evals/reports/<timestamp>.json`（各問スコア + 集計 + メタ）を出力 — `evals/__main__.py:225-233`
- [ ] 人間可読サマリ Markdown（集計表・**リランク前/後比較**・negative 成否）を出力 — 集計表とリランク前/後比較は `__main__.py:191-212` の Markdown に含まれるが、**negative ケースの成否を個別に示す節は無い**（`results` 配列には残るが Markdown サマリには反映されない）
- [ ] **provenance を記録**（埋め込み/次元・rerank・生成・judge モデル名・**生成/judge の temp・max_tokens**・検索パラメータ・**corpus ハッシュ**・**dataset version**・日時。§6.2）— モデル名・検索パラメータ・corpus ハッシュ・dataset version・日時は記録済み（`__main__.py:136-155`）が、**生成/judge の temp・max_tokens は provenance に含まれていない**（実際に固定もされていないため未達）
- [ ] Langfuse への eval 実行コスト記録を確認（NFR-5。§9）— `@observe` デコレータは配線済み（judge/generation）。**M5追記（2026-07-13）**: 本セッションで `core/config.py` に実在した別バグ（`.env` のLangfuse鍵が `os.environ` へ反映されず計装が常にno-op化していた）を修正したが、`backend/.env` に設定されている鍵ペア自体がLangfuse API（EU/US両ホスト）で401 Unauthorizedを返すため、鍵を再発行しない限りこの項目は検証できない。引き続き未チェック
- [ ] スモークテスト: 2〜3 問の極小データセットで end-to-end（外部呼び出しはモック）— **genuine gap**。`evals/__main__.py`（`run_eval`）を対象とするテストが見つからない

---

## Phase 5 — ベースライン確立と回帰検出（スペック §6.3, §7.3）

- [x] 現行構成で `make eval` を実行し、**committed baseline** `evals/baselines/current.json` を確立 — **M5追記（2026-07-13）**: 以前の丸い数字のbaseline（`aggregate`のみ、provenance/results無し）は実行由来か確認できない疑わしい値だったため退避・削除し、Docker上で実 `make eval`（31問、実API）を実行して再生成した。新しい `current.json` は `provenance`（日時・corpus_hash・モデル名・検索パラメータ）と31問分の `results` を含む。詳細は [docs/eval_report.md](../eval_report.md) 参照
- [ ] メトリクス別 tolerance（`EVAL_TOLERANCE_*`）の初期値を、実測のブレ幅を見て設定 — `core/config.py` に `eval_tolerance_*` 設定は存在せず、`evals/__main__.py:175,181` に `0.05`/`0.1` がハードコードされている。実測のブレ幅から設定した記録も無い
- [x] baseline 比較ロジック（tolerance 超の低下を回帰として検出）を実装 — `evals/__main__.py:163-188`
- [x] **検索指標=ハードゲート / 生成指標=ソフト**の判定方針を実装（§7.3）— 同上（reranked指標は `fail=True`→`sys.exit(1)`、生成指標は `warnings` リストに追加するのみで exit しない）
- [x] baseline 更新は意図的な PR でのみ行う運用をドキュメント化（§6.3）— `docs/specs/26070805-m3_eval_expansion/spec.md:196` に明記
- [ ] ユニットテスト: baseline 比較の tolerance 境界（fail/warn/pass の分岐）— **genuine gap**。該当テストが見つからない

---

## Phase 6 — CI ワークフロー（スペック §7）

- [x] トリガ設定（`prompts/` `retrieval/` `ingestion/` `generation/` `evals/` 変更 PR で実行。§7.1）— `.github/workflows/eval.yml:3-12`
- [ ] CI ジョブを **再現経路**で組む: DB 起動 → `make migrate` → **`make ingest`(seed)** → `make eval`（§7.2 / NFR-8）— ステップの並び自体は `.github/workflows/eval.yml:48-69` にあるが、**genuine gap**: DB サービスに pg_bigm 非搭載の `pgvector/pgvector:pg16`（無印）を使っており、`0001_init.py` の `CREATE EXTENSION IF NOT EXISTS "pg_bigm"` が失敗する可能性が高い（ローカルの `docker-compose.yml`/AGENTS.md §4 は pg_bigm をソースビルドしたカスタムイメージ `backend/docker/db/Dockerfile.local` を使う設計）。CI が実際に緑になるか要検証・要修正
- [x] API キーを CI シークレットに設定（`OPENAI_API_KEY` / `VOYAGE_API_KEY`。Langfuse は任意）→ M5監査で `.github/workflows/eval.yml` の `ANTHROPIC_API_KEY` 誤参照を `OPENAI_API_KEY` に是正
- [x] 検索ハードゲート / 生成ソフトの CI 判定を配線 — `evals/__main__.py:239-240` `sys.exit(1)`（fail時）により `uv run python -m private_rag_apps.evals` のプロセス終了コードでCIジョブが失敗する。生成指標は `warnings` のみで exit しないためソフトゲートとして機能
- [x] **before/after を PR に自動記載**（コメント or artifact。AGENTS.md §9）— `.github/workflows/eval.yml:71-86`
- [ ] 評価時 `ef_search` 全探索寄り固定で、索引再構築由来の recall 揺れが偽陽性 fail にならないことを確認（§4.2/§7.4）— `eval_ef_search=100` という設定値はあるが、複数回実行しての安定性検証記録は無い（実行環境が必要）

---

## Phase 7 — Langfuse Datasets/Experiments 連携（任意・スペック §6.4）

> Phase 0 で「実配線する」と決めた場合のみ。CI ゲートの正は committed baseline（連携は best-effort）。

> Phase 0 の決定（§13）は「フックのみ用意」。以下3項目は**意図的に未実装**（`evals/__main__.py:235-237` にコメントアウトの `[HOOK]` として存在）であり、ギャップではない。

- [ ] ゴールデンデータを Langfuse Dataset として登録
- [ ] `make eval` の各実行を Experiment（run）として記録
- [ ] Langfuse 障害時も CI/eval が落ちないこと（best-effort）を確認

---

## Phase 8 — マルチターン小規模サニティ（スペック §8）

- [x] `turns` を持つマルチターン項目を数問追加 — `m3_golden.jsonl` に1問（スペック §8「少数（数問）」に対しては厚みが薄い。要件自体（1問以上）は満たす）
- [x] condense 経由でフォローアップが自己完結クエリ化され正解文書を引けるかを sanity check — `evals/__main__.py:68-77`（`item.turns` があれば `condense()` を呼んでから検索）
- [ ] **非ゲート**（本格的な会話評価は後続）であることを確認 — **genuine gap**。`__main__.py` のメインループは `turns` の有無で分岐せず、マルチターン項目の指標もそのまま `agg["fused"]`/`agg["reranked"]`/`agg["generation"]`（L107-113）に合算されており、この集計が baseline 比較・ハード/ソフトゲート判定（L172-183）にそのまま使われる。マルチターン項目を非ゲート対象として除外する処理が無く、スペック §8 の「非ゲート」要件を満たしていない

---

## Phase 9 — 仕上げ・受け入れ確認（スペック §11, §13）

- [ ] スペック §11 の受け入れ条件をすべてチェック（データセット/検索指標/生成指標/ハーネス/CI/共通）— 本 M5 監査（2026-07-13）で大半をチェック済みだが、生成 decoding 固定・`EVAL_JUDGE_SAMPLES`平均・CI の pg_bigm 欠如・マルチターン非ゲート・Eval レポート公開など複数項目が未達のまま残っている（`docs/specs/26070805-m3_eval_expansion/spec.md` §11 参照）
- [x] **M1 前後を含むスコア推移の Eval レポートを公開**（`docs/eval_report.md`。§12 Definition of Success）— **M5追記（2026-07-13）**: 実 `make eval` 実行結果（`backend/evals/reports/latest_summary.md`・`backend/evals/baselines/current.json`）を一次ソースとして `docs/eval_report.md` を作成・公開した。ただし fused/reranked の2段階比較にとどまり、spec §5.2 が理想とする「M0ベクトルのみ」との3段階比較は harness が対応しておらずレポート内で正直に明記している（詳細は同レポート§6）
- [x] `requirements.md` §9/§NFR-1 を更新（path レベル正解・`EVAL_TOP_K` 分離・ゲート方針）— `requirements.md:143-144`
- [x] `requirements.md` §12 に Eval レポート公開の具体パスを明記 — `requirements.md:285`（`docs/eval_report.md` と明記。**M5追記**: `docs/eval_report.md` 自体も公開済みのため、`requirements.md` §12 側のチェックはCommit 8で反映する）
- [x] `architecture.md` に評価フロー・judge の依存・**`retrieval` 診断モード**を追記（§4.2/§13）— `architecture.md:155,267`
- [x] `AGENTS.md` §7/§9 に CI 実行経路・ゲート方針を反映 — AGENTS.md §7 に「Eval CI は再現経路をたどる（M3 以降）: DB 起動 → make migrate → make ingest（seed）→ make eval → committed baseline と比較。ゲートは検索指標=ハード / 生成指標=ソフト」と明記済み
- [x] `make lint` / `make test` が通ることを最終確認 — `make lint` は 2026-07-13 時点でクリーン（exit 0）。**M5追記（2026-07-13）**: Docker起動の上で `pytest` をDB込みでフル実行し69件全通過を確認済み。ただしこの通過は `retrieval/searcher.py` の実SQLバグ（`docs/specs/26070717-m1_hybrid_search/tasklist.md` M5追記参照）を検出できておらず、`make eval` の実行で初めて判明した。テストスイートの実SQL網羅性には既知の穴がある
- [x] 依存方向（LLM は generation・evals のみ）を最終確認 — `retrieval/searcher.py`・`evals/metrics.py`・`evals/schema.py` に LLM 呼び出しなし。LLM呼び出しは `evals/judge.py`（openai）と `generation/generator.py`（openai）に限定

---

## 変更履歴

| version | 日付 | 変更 |
|---|---|---|
| v0.1 | 2026-07-07 | 初版。docs/specs/26070805-m3_eval_expansion/spec.md v0.2 の §14 実装順序に基づき Phase 0〜9 を作成。指標は決定的ユニットテスト先行、`@k` チャンク基準・doc-dedup・IDCG 基準、`retrieval` 診断モード、生成 decoding 固定、ef_search 全探索寄り、検索ハード/生成ソフトのゲート、再現経路 CI を各 Phase に反映 |
